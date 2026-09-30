#!/usr/bin/env python3
"""
Writes this session's tracking-test results back into the Lark spec sheet.

Rules (what goes into which column):
  PASSED   -> QA Test = "Passed". If it fired wrong at least once before being
              right on retest, QA Note says so (flaky).
  FAILED   -> QA Test = "Failed", QA Note = why (missing / unexpected params).
              Only written once the row was wrong at least --min-fail-attempts
              times (default 3 = first test + 2 retests); fewer is listed as
              "cần retest" and left alone.
  BLOCKED  -> (marked via POST /api/mark) QA Note = "Không test được: <lý do>",
              QA Test left blank — the dropdown only allows Passed/Failed.
  IN_PROGRESS / NOT_TESTED -> untouched.

Where it writes: --sheet-url/--sheet-id if given; otherwise the sheet the
running server's spec was fetched from (fetch_spec.py leaves a
lark-<id>.source.json next to the .xlsx).

Rows are matched to the LIVE sheet by identity (event + identifiers + param
name + overlay + screen), with real row numbers from lark-cli — never by the
local snapshot's row numbers. Cells whose value wouldn't change are skipped.

Default is a dry run that prints every cell as old -> new. --execute does the
real write (needs the sheets:spreadsheet:write_only scope) and then reads the
cells back to verify.
"""
import argparse
import json
import sys
import urllib.request
from pathlib import Path

from lark_sheet import EVENT_NAME_HEADER, parse_sheet, read_sheet, sheet_id_from_url, write_cells
from spec_store import split_values

QA_TEST_HEADER = "QA Test"
QA_NOTE_HEADER = "QA Note"
BLOCKED_PREFIX = "Không test được"


def fetch_json(server_url, path):
    with urllib.request.urlopen(f"{server_url}{path}", timeout=10) as resp:
        return json.loads(resp.read())


def resolve_target(args):
    if args.sheet_url:
        return args.sheet_url, args.sheet_id or sheet_id_from_url(args.sheet_url)
    spec = fetch_json(args.server, "/api/spec")
    path = Path(spec.get("path") or "")
    source = path.with_suffix(".source.json")
    if not source.exists():
        raise RuntimeError(f"Spec đang dùng ({path.name or 'chưa có'}) không lấy từ Lark qua fetch_spec.py "
                           f"— truyền --sheet-url <link sheet> để biết ghi vào đâu.")
    src = json.loads(source.read_text(encoding="utf-8"))
    return src["url"], src["sheet_id"]


def identity(event_name, identifiers, param_name, overlays, screens):
    return (event_name.strip(), tuple(identifiers), tuple(split_values(param_name)),
            tuple(overlays), tuple(screens))


def live_identity(cells):
    event_name = cells.get(EVENT_NAME_HEADER, "")
    if event_name.startswith("screen_"):
        ids = split_values(cells.get("Screen name", ""))
    elif event_name.startswith("overlay_"):
        ids = split_values(cells.get("Overlay name", ""))
    else:
        ids = split_values(cells.get("Button name", "")) or split_values(cells.get("Param name", ""))
    return identity(event_name, ids, cells.get("Param name", ""),
                    split_values(cells.get("Overlay name", "")), split_values(cells.get("Screen name", "")))


def coverage_identity(row):
    return identity(row["event_name"], row["values"], row.get("param_name", ""),
                    split_values(row["overlay_name"]), split_values(row["screen_name"]))


def format_log(ev):
    """Same shape as the dashboard's copy button:
    2026-09-29 13:41:11.874 18764-18764
    screen_show
    prev_screen_name=, screen_name=ScrSplash, session_number=3"""
    head = f"{ev.get('log_time') or ''} {ev.get('pid')}-{ev.get('tid')}".strip()
    params = ", ".join(f"{k}={v}" for k, v in ev.get("bundle", {}).items())
    return f"{head}\n{ev['event_name']}\n{params}"


def evidence_by_row(events):
    """row_num -> {"MATCH": last matching event, "PARTIAL": last wrong event}."""
    out = {}
    for ev in events:
        sr = ev.get("spec_row")
        if not sr or ev["verdict"] not in ("MATCH", "PARTIAL"):
            continue
        for n in [sr["row_num"]] + sr.get("also_rows", []):
            out.setdefault(n, {})[ev["verdict"]] = ev
    return out


def decide(row, min_fail, evidence=None):
    """-> (qa_test or None, qa_note or None, skip_reason or None)"""
    st = row["status"]
    extra = row.get("qa_note") or ""
    ev = evidence or {}
    if st == "PASSED":
        parts = []
        if row.get("flaky_reason"):
            parts.append(f"Pass khi test lại ({row['match']} đúng / {row['partial']} sai). "
                         f"Lần sai: {row['flaky_reason']}")
        if extra:
            parts.append(extra)
        if ev.get("MATCH"):
            parts.append(format_log(ev["MATCH"]))
        return "Passed", "\n".join(parts) or None, None
    if st == "FAILED":
        if row["partial"] < min_fail:
            return None, None, f"cần retest (mới sai {row['partial']}/{min_fail} lần)"
        note = f"{row['reason']} (sai {row['partial']}/{row['partial'] + row['match']} lần test)"
        if row["pending"]:
            note += f" | Chưa thấy: {', '.join(row['pending'])}"
        if extra:
            note += f" | {extra}"
        if ev.get("PARTIAL"):
            note += "\n" + format_log(ev["PARTIAL"])
        return "Failed", note, None
    if st == "BLOCKED":
        return None, f"{BLOCKED_PREFIX}: {extra}" if extra else BLOCKED_PREFIX, None
    return None, None, "chưa có kết quả"


def build_plan(coverage_rows, live_rows, qa_col, note_col, min_fail, events=()):
    live = {}
    for row in live_rows:
        if row["cells"].get(EVENT_NAME_HEADER, "").strip():
            live.setdefault(live_identity(row["cells"]), []).append(row)

    evidence = evidence_by_row(events)
    changes, skipped, unmatched = [], [], []
    for r in coverage_rows:
        qa, note, skip = decide(r, min_fail, evidence.get(r["row_num"]))
        if skip:
            if r["status"] == "FAILED":
                skipped.append((r, skip))
            continue
        slots = live.get(coverage_identity(r))
        if not slots:
            unmatched.append(r)
            continue
        target = slots.pop(0)
        for col, header, new in ((qa_col, QA_TEST_HEADER, qa), (note_col, QA_NOTE_HEADER, note)):
            if new is None:
                continue
            old = target["cells"].get(header, "")
            if old.strip() == new.strip():
                continue
            changes.append({"cell": f"{col}{target['row_num']}", "row_num": target["row_num"],
                            "header": header, "old": old, "new": new,
                            "event": r["event_name"], "ident": r["identifier"], "status": r["status"]})
    return changes, skipped, unmatched


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--server", default="http://127.0.0.1:8765", help="Local dashboard server URL")
    ap.add_argument("--sheet-url", default=None, help="Link sheet Lark (mặc định: nguồn của spec đang load)")
    ap.add_argument("--sheet-id", default=None)
    ap.add_argument("--min-fail-attempts", type=int, default=3,
                    help="Số lần sai tối thiểu trước khi ghi Failed (mặc định 3 = test + 2 lần retest)")
    ap.add_argument("--rows", default=None,
                    help="Chỉ ghi các dòng spec này, vd 11,141,147 (mặc định: mọi dòng có kết quả)")
    ap.add_argument("--json", action="store_true", help="In plan dạng JSON (cho script/skill đọc)")
    ap.add_argument("--execute", action="store_true", help="Ghi thật. Không có cờ này thì chỉ xem trước.")
    args = ap.parse_args()

    url, sheet_id = resolve_target(args)
    coverage = fetch_json(args.server, "/api/coverage")["rows"]
    if args.rows:
        wanted = {int(x) for x in args.rows.split(",") if x.strip()}
        coverage = [r for r in coverage if r["row_num"] in wanted]
    events = fetch_json(args.server, "/api/history")["events"]
    header_row, headers, col_letter, live_rows = parse_sheet(read_sheet(url, sheet_id))
    for h in (QA_TEST_HEADER, QA_NOTE_HEADER):
        if h not in col_letter:
            raise RuntimeError(f"Không thấy cột '{h}' trong sheet. Các cột: {[x for x in headers if x]}")
    qa_col, note_col = col_letter[QA_TEST_HEADER], col_letter[QA_NOTE_HEADER]

    changes, skipped, unmatched = build_plan(coverage, live_rows, qa_col, note_col,
                                           args.min_fail_attempts, events)

    if args.json:
        print(json.dumps({"sheet_url": url, "sheet_id": sheet_id, "changes": changes,
                          "need_retest": [{"row_num": r["row_num"], "event": r["event_name"],
                                           "ident": r["identifier"], "why": why} for r, why in skipped],
                          "unmatched": [{"event": r["event_name"], "ident": r["identifier"]} for r in unmatched]},
                         ensure_ascii=False, indent=2))
    else:
        print(f"Sheet: {sheet_id} · cột QA Test = {qa_col}, QA Note = {note_col} · header ở dòng {header_row}")
        for c in changes:
            old = c["old"].replace("\n", " ") or "(trống)"
            print(f"  {c['cell']:<6} {c['event']} / {c['ident']}\n         {old!r} -> {c['new']!r}")
        for r, why in skipped:
            print(f"  [bỏ qua] dòng spec {r['row_num']} {r['event_name']} / {r['identifier']}: {why}")
        for r in unmatched:
            print(f"  [warn] không tìm thấy dòng tương ứng trên sheet: {r['event_name']} / {r['identifier']}")
        if not changes:
            print("Không có ô nào cần ghi.")

    if not changes:
        return
    writes = [{"range": c["cell"], "cells": [[{"value": c["new"]}]]} for c in changes]
    result = write_cells(url, sheet_id, writes, dry_run=not args.execute)
    if not args.execute:
        if not args.json:
            print(f"\nDRY-RUN: {len(changes)} ô — chưa ghi gì. Thêm --execute để ghi thật.")
        return

    # Read back and compare, so "đã ghi" is a checked fact, not an assumption.
    _, _, col_letter2, after_rows = parse_sheet(read_sheet(url, sheet_id))
    by_row = {r["row_num"]: r["cells"] for r in after_rows}
    bad = [c for c in changes if by_row.get(c["row_num"], {}).get(c["header"], "").strip() != c["new"].strip()]
    print(json.dumps({"written": len(changes) - len(bad), "mismatch": bad,
                      "lark_revision": (result.get("data") or {}).get("revision")}, ensure_ascii=False, indent=2))
    if bad:
        sys.exit(2)


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as e:
        print(f"[error] {e}", file=sys.stderr)
        sys.exit(1)
