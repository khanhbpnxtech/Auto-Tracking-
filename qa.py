#!/usr/bin/env python3
"""
Terminal view of the running dashboard (http://127.0.0.1:8765), for testing
without the browser — e.g. by an AI tester driving the device with adbx.

  qa.py status                 counts + every row that has a result
  qa.py todo [-f FEATURE]      rows still to do: not tested, partly seen,
                               or failed fewer than 3 times (needs retest)
  qa.py row N                  one spec row + every event credited to it
  qa.py last [N]               last N tracking events (default 10) + verdict
  qa.py mark N "lý do"         mark row N "Không test được" (BLOCKED)
  qa.py note N "ghi chú"       attach a QA note to row N without blocking it
  qa.py unmark N               remove the mark/note

Env: QA_SERVER (default http://127.0.0.1:8765)
"""
import json
import os
import sys
import urllib.request

SERVER = os.environ.get("QA_SERVER", "http://127.0.0.1:8765")
MIN_FAIL = 3
LABEL = {"PASSED": "Passed", "FAILED": "Failed", "IN_PROGRESS": "Đang test",
         "NOT_TESTED": "Chưa test", "BLOCKED": "Không test được"}


def get(path):
    with urllib.request.urlopen(SERVER + path, timeout=10) as r:
        return json.loads(r.read())


def post(path, body):
    req = urllib.request.Request(SERVER + path, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())


def short(text, n=90):
    text = " ".join((text or "").split())
    return text if len(text) <= n else text[:n - 1] + "…"


def where(r):
    parts = []
    if r.get("param_name") and r["param_name"] not in ("button_name", "screen_name", "overlay_name"):
        parts.append(f"param={r['param_name']}")
    if r["screen_name"] and not r["event_name"].startswith("screen_"):
        parts.append(f"screen={short(r['screen_name'], 40)}")
    if r["overlay_name"] and not r["event_name"].startswith("overlay_"):
        parts.append(f"overlay={short(r['overlay_name'], 40)}")
    return " ".join(parts)


def line(r):
    extra = r["reason"] or r.get("flaky_reason") and f"flaky: {r['flaky_reason']}" or r.get("qa_note") or ""
    if r["pending"] and r["status"] != "NOT_TESTED":
        extra = (extra + " | " if extra else "") + "chưa thấy: " + ", ".join(r["pending"])
    seen = f"✓{r['match']} ✗{r['partial']}" if r["match"] or r["partial"] else ""
    return (f"  {r['row_num']:>4} {LABEL[r['status']]:<16} {r['event_name']:<14} {short(r['identifier'], 40):<40} "
            f"{where(r):<30} {seen:<8} {short(extra, 80)}")


def cmd_status(_):
    cov = get("/api/coverage")
    sm = cov["summary"]
    print(f"Tổng {sm['total']} · Passed {sm['PASSED']} · Failed {sm['FAILED']} · Đang test {sm['IN_PROGRESS']}"
          f" · Không test được {sm.get('BLOCKED', 0)} · Chưa test {sm['NOT_TESTED']}")
    for st in ("FAILED", "IN_PROGRESS", "BLOCKED", "PASSED"):
        for r in cov["rows"]:
            if r["status"] == st:
                print(line(r))


def cmd_todo(args):
    feature = None
    if args[:1] == ["-f"] and len(args) > 1:
        feature = args[1].lower()
    rows = get("/api/coverage")["rows"]
    todo = [r for r in rows
            if (r["status"] in ("NOT_TESTED", "IN_PROGRESS")
                or (r["status"] == "FAILED" and r["partial"] < MIN_FAIL))
            and (not feature or feature in (r["feature"] or "").lower())]
    cur = None
    for r in todo:
        if r["feature"] != cur:
            cur = r["feature"]
            print(f"\n## {cur or '(không có Feature)'}")
        tag = f"retest {r['partial']}/{MIN_FAIL}" if r["status"] == "FAILED" else LABEL[r["status"]]
        print(f"  {r['row_num']:>4} [{tag}] {r['event_name']} {r['identifier']}  {where(r)}")
        if r["definition"]:
            print(f"       ↳ {short(r['definition'], 160)}")
    print(f"\n{len(todo)} dòng cần làm.")


def cmd_row(args):
    n = int(args[0])
    cov = next((r for r in get("/api/coverage")["rows"] if r["row_num"] == n), None)
    if not cov:
        sys.exit(f"Không có dòng spec {n}.")
    print(json.dumps({k: cov[k] for k in ("row_num", "feature", "event_name", "param_name", "identifier",
                                          "screen_name", "overlay_name", "definition", "status", "match",
                                          "partial", "pending", "reason", "flaky_reason", "qa_note")},
                     ensure_ascii=False, indent=2))
    evs = [e for e in get("/api/history")["events"]
           if e.get("spec_row") and (e["spec_row"]["row_num"] == n or n in e["spec_row"].get("also_rows", []))]
    print(f"\n{len(evs)} event đã tính cho dòng này:")
    for e in evs:
        print(f"  {e.get('log_time')} {e['verdict']:<7} {json.dumps(e['bundle'], ensure_ascii=False)}")


def cmd_last(args):
    n = int(args[0]) if args else 10
    for e in get("/api/history")["events"][-n:]:
        sr = e.get("spec_row")
        row = f"row {sr['row_num']}" + (f"+{sr['also_rows']}" if sr.get("also_rows") else "") if sr else "—"
        why = " · ".join(filter(None, [
            e["missing"] and "thiếu " + ",".join(e["missing"]),
            e["extra"] and "lạ " + ",".join(e["extra"]),
            e.get("wrong") and "sai " + ",".join(e["wrong"])]))
        print(f"  {(e.get('log_time') or '')[11:23]} {e['verdict']:<7} {row:<12} {e['event_name']:<14} "
              f"{json.dumps(e['bundle'], ensure_ascii=False)}  {why}")


def cmd_mark(args, status="BLOCKED"):
    n, note = int(args[0]), " ".join(args[1:])
    if status == "BLOCKED" and not note:
        sys.exit("Cần lý do: qa.py mark N \"lý do không test được\"")
    print(post("/api/mark", {"row_num": n, "status": status, "note": note}))


def main():
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print(__doc__)
        return
    cmd, args = sys.argv[1], sys.argv[2:]
    handlers = {
        "status": cmd_status, "todo": cmd_todo, "row": cmd_row, "last": cmd_last,
        "mark": cmd_mark,
        "note": lambda a: cmd_mark(a, status=None),
        "unmark": lambda a: print(post("/api/mark", {"row_num": int(a[0]), "status": None, "note": ""})),
    }
    if cmd not in handlers:
        sys.exit(__doc__)
    try:
        handlers[cmd](args)
    except OSError as e:
        sys.exit(f"Không kết nối được dashboard {SERVER} ({e}). Server đã chạy chưa? "
                 f"python3 server.py --spec <file>")


if __name__ == "__main__":
    main()
