#!/usr/bin/env python3
import argparse
import json
import os
import queue
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import mirror
import report
from logtail import TrackingRunner
import spec_store
from spec_store import SpecStore, match_event

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
HISTORY_LIMIT = 2000
RC_SESSION_LIMIT = 20

# Spec sống trên Lark Wiki (sheet "Tracking Auto") — same page as the link button in the UI
# (static/index.html's COVERAGE_SPEC_URL). Keep both in sync if Technify moves it.
LARK_SPEC_URL = "https://technify.sg.larksuite.com/wiki/PYxGwDEFAizBpIk6DkolwgfIguT"
LARK_FETCH_INTERVAL = 60  # seconds between background auto-syncs from Lark into the spec folder

state = {
    "spec_store": None,
    "events": [],          # bounded history, each a dict ready to JSON-encode
    "errors": [],           # bounded history of grouped app crash/error entries
    "rc_sessions": [],      # RemoteConfig fetches, one per app start; collected silently, only served on /api/rc
    "marks": {},            # spec row_num -> {"status": "BLOCKED"|None, "note": str}, set by the tester via /api/mark
    "subscribers": [],      # list[queue.Queue] for SSE clients
    "lock": threading.Lock(),
    "device": {"state": "waiting", "serial": None},
    "tag": "TrackingEvent",
}


def broadcast(event_type, payload):
    data = f"event: {event_type}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
    with state["lock"]:
        subs = list(state["subscribers"])
    for q in subs:
        q.put(data)


def spec_summary():
    store = state["spec_store"]
    if store is None:
        return {"rows": 0, "path": "", "error": "chưa load"}
    return {"rows": len(store.rows), "path": str(store.path) if store.path else "",
            "error": store.error, "mtime": store.mtime}


def on_rc(entry):
    """Groups RemoteConfigManager log lines into sessions (one per 'start fetching' / per process).
    Deliberately not broadcast: the UI only reads this via /api/rc when the RC Manager tab is opened/refreshed."""
    with state["lock"]:
        sessions = state["rc_sessions"]
        cur = sessions[-1] if sessions else None
        is_start = entry["kind"] == "status" and "start fetching" in entry["message"].lower()
        if is_start or cur is None or cur["pid"] != entry["pid"]:
            cur = {"id": (cur["id"] + 1) if cur else 1, "pid": entry["pid"], "package": entry.get("package"),
                   "started": entry["log_time"], "status": "fetching", "messages": [], "keys": {}}
            sessions.append(cur)
            del sessions[:-RC_SESSION_LIMIT]
        if entry.get("package") and not cur["package"]:
            cur["package"] = entry["package"]
        if entry["kind"] == "status":
            cur["messages"].append(entry["message"])
            low = entry["message"].lower()
            if "success" in low:
                cur["status"] = "success"
            elif any(w in low for w in ("fail", "error", "timeout", "exception")):
                cur["status"] = "failed"
        else:
            cur["keys"][entry["key"]] = {"type": entry["type"], "value": entry["value"]}


def rc_payload(session_id):
    with state["lock"]:
        sessions = state["rc_sessions"]
        summary = [{"id": s["id"], "pid": s["pid"], "package": s["package"], "started": s["started"],
                    "status": s["status"], "count": len(s["keys"]), "messages": s["messages"]}
                   for s in reversed(sessions)]  # newest first
        chosen = next((s for s in sessions if s["id"] == session_id), sessions[-1] if sessions else None)
        config = [{"key": k, "type": v["type"], "value": v["value"]}
                  for k, v in sorted(chosen["keys"].items(), key=lambda kv: kv[0].lower())] if chosen else []
    return {"sessions": summary, "selected": chosen["id"] if chosen else None, "config": config}


def on_status(message):
    print(f"[status] {message}")
    broadcast("status", {"message": message})


def on_device(info):
    state["device"] = info
    broadcast("device", info)


def match_fields(event_name, bundle):
    store = state["spec_store"]
    result = match_event(store, event_name, bundle) if store else {
        "verdict": "UNKNOWN", "spec_row": None, "missing": [], "extra": [], "wrong": [], "also_rows": []
    }
    spec_row = result["spec_row"]
    return {
        "verdict": result["verdict"],
        "missing": result["missing"],
        "extra": result["extra"],
        "wrong": result["wrong"],
        "spec_row": None if not spec_row else {
            "screen": spec_row.screen,
            "identifier": spec_row.identifier_label(),
            "definition": spec_row.definition,
            "app_version": spec_row.app_version,
            "row_num": spec_row.row_num,
            "also_rows": result["also_rows"],
        },
    }


def rematch_history():
    with state["lock"]:
        for ev in state["events"]:
            ev.update(match_fields(ev["event_name"], ev["bundle"]))


def device_ts(log_time):
    """Epoch seconds (ms precision) of a logcat timestamp. Only differences
    between two of these are meaningful (timezone-independent), which is all
    the dashboard uses it for."""
    if not log_time:
        return None
    if not log_time[:4].isdigit() or log_time[4] != "-":
        log_time = f"{time.localtime().tm_year}-{log_time}"
    try:
        return datetime.strptime(log_time, "%Y-%m-%d %H:%M:%S.%f").timestamp()
    except ValueError:
        return None


def fail_reason(ev):
    parts = []
    if ev["missing"]:
        parts.append("Thiếu param: " + ", ".join(ev["missing"]))
    if ev["extra"]:
        parts.append("Param lạ: " + ", ".join(ev["extra"]))
    if ev.get("wrong"):
        parts.append("Sai giá trị: " + ", ".join(ev["wrong"]))
    return " · ".join(parts)


def event_identifier(ev):
    name = ev["event_name"]
    bundle = ev.get("bundle", {})
    if name.startswith("screen_"):
        return bundle.get("screen_name", "")
    if name.startswith("overlay_"):
        return bundle.get("overlay_name", "")
    return bundle.get("button_name", "")


def build_coverage():
    """Per spec row status for this session.

    A row can list several identifiers (one screen_show row for 4 screens), so
    verdicts are tracked per identifier value: a value is "good" once it was
    seen as MATCH, "bad" if it was only ever seen as PARTIAL.
      FAILED       any value only ever seen as PARTIAL
      PASSED       every value seen as MATCH
      IN_PROGRESS  some values seen as MATCH, others not seen yet
      NOT_TESTED   nothing seen
      BLOCKED      tester marked it "Không test được" via /api/mark (only
                   applies while the row has no Passed/Failed evidence)
    UNKNOWN events aren't tied to any spec row, so they never count here.

    `partial` counts wrong firings, so after retesting a failing row the
    caller can tell a confirmed failure (wrong every time) from a flaky one
    (wrong once, right on retest -> PASSED with `flaky_reason`)."""
    store = state["spec_store"]
    spec_rows = store.rows if store else []
    with state["lock"]:
        events = list(state["events"])
        marks = dict(state["marks"])

    agg = {}
    for ev in events:
        sr = ev.get("spec_row")
        if not sr:
            continue
        ident = event_identifier(ev)
        for row_num in [sr["row_num"]] + sr.get("also_rows", []):
            a = agg.setdefault(row_num, {"values": {}, "match": 0, "partial": 0,
                                         "last": None, "reason": "", "last_event": None})
            best = a["values"].get(ident)
            if ev["verdict"] == "MATCH":
                a["match"] += 1
                a["values"][ident] = "MATCH"
            elif ev["verdict"] == "PARTIAL":
                a["partial"] += 1
                a["reason"] = fail_reason(ev)
                if best != "MATCH":
                    a["values"][ident] = "PARTIAL"
            a["last"] = ev.get("log_time")
            # Raw log of whichever event last touched this row (MATCH or PARTIAL) — so a tester
            # (or an automation script) can copy the exact live line straight from this tab
            # instead of hunting for it back in Other events.
            a["last_event"] = {"log_time": ev.get("log_time"), "pid": ev.get("pid"), "tid": ev.get("tid"),
                               "event_name": ev["event_name"], "bundle": ev.get("bundle", {})}

    rows = []
    counts = {"PASSED": 0, "FAILED": 0, "IN_PROGRESS": 0, "NOT_TESTED": 0, "BLOCKED": 0}
    for r in spec_rows:
        values = r.identifier_values()
        a = agg.get(r.row_num)
        pending = []
        if not a:
            status = "NOT_TESTED"
        elif len(values) <= 1:
            status = "PASSED" if a["match"] else "FAILED"
        else:
            got = a["values"]
            pending = [v for v in values if v not in got]
            if any(got.get(v) == "PARTIAL" for v in values):
                status = "FAILED"
            elif pending:
                status = "IN_PROGRESS"
            else:
                status = "PASSED"
        mark = marks.get(r.row_num) or {}
        if mark.get("status") == "BLOCKED" and status in ("NOT_TESTED", "IN_PROGRESS"):
            status = "BLOCKED"
        counts[status] += 1
        rows.append({
            "row_num": r.row_num,
            "feature": r.screen,
            "event_name": r.event_name,
            "param_name": r.param_name,
            "identifier": r.identifier_label(),
            "values": values,
            "pending": pending,
            "screen_name": r.screen_name,
            "overlay_name": r.overlay_name,
            "definition": r.definition,
            "valid_value": r.bundle_params_text,
            "app_version": r.app_version,
            "status": status,
            "match": a["match"] if a else 0,
            "partial": a["partial"] if a else 0,
            "last": a["last"] if a else None,
            "bundle_live": a["last_event"] if a else None,
            "reason": a["reason"] if a and status == "FAILED" else "",
            "flaky_reason": a["reason"] if a and status == "PASSED" and a["partial"] else "",
            "qa_note": mark.get("note", ""),
        })
    return {"rows": rows, "summary": {"total": len(rows), **counts}}


REPORT_TYPES = {
    "xlsx": ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", report.build_xlsx),
    "md": ("text/markdown; charset=utf-8", report.build_md),
    "csv": ("text/csv; charset=utf-8", report.build_csv),
}


def build_report_data():
    with state["lock"]:
        events = list(state["events"])
        errors = list(state["errors"])
    store = state["spec_store"]
    dev = state["device"]
    counts = {}
    for ev in events:
        counts[ev["verdict"]] = counts.get(ev["verdict"], 0) + 1
    times = [ev["log_time"] for ev in events if ev.get("log_time")]
    device = "—"
    if dev.get("serial"):
        device = f"{dev.get('model') or ''} ({dev['serial']})".strip()
    return {
        "meta": {
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "device": device,
            "packages": sorted({ev["package"] for ev in events if ev.get("package") and ev["package"] != "?"}),
            "spec_file": store.path.name if store and store.path else "—",
            "first_event": times[0] if times else None,
            "last_event": times[-1] if times else None,
            "event_counts": counts,
        },
        "coverage": build_coverage(),
        "events": events,
        "errors": errors,
    }


def is_ads_event(ev):
    return ev["event_name"].lower().startswith("iaa_")


def on_event(raw_line, event_name, bundle, package=None, meta=None):
    meta = meta or {}
    payload = {
        "time": time.time(),
        "log_time": meta.get("log_time"),
        "pid": meta.get("pid"),
        "tid": meta.get("tid"),
        "dev_ts": device_ts(meta.get("log_time")),
        "raw": raw_line,
        "event_name": event_name,
        "package": package or "?",
        "bundle": bundle,
        **match_fields(event_name, bundle),
    }
    with state["lock"]:
        state["events"].append(payload)
        if len(state["events"]) > HISTORY_LIMIT:
            state["events"] = state["events"][-HISTORY_LIMIT:]
    broadcast("track", payload)


def on_error(entry):
    payload = {
        "time": entry["time"],
        "log_time": entry.get("log_time"),
        "pid": entry.get("pid"),
        "tid": entry.get("tid"),
        "dev_ts": device_ts(entry.get("log_time")),
        "level": entry["level"],
        "tag": entry["tag"],
        "package": entry["package"],
        "message": entry["message"],
    }
    with state["lock"]:
        state["errors"].append(payload)
        if len(state["errors"]) > HISTORY_LIMIT:
            state["errors"] = state["errors"][-HISTORY_LIMIT:]
    broadcast("error_log", payload)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass  # quiet; status already printed by on_status

    def _send_json(self, obj, status=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_download(self, body, content_type, filename):
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/report":
            fmt = (parse_qs(parsed.query).get("format") or ["xlsx"])[0]
            if fmt not in REPORT_TYPES:
                return self._send_json({"error": "format không hợp lệ"}, status=400)
            content_type, builder = REPORT_TYPES[fmt]
            body = builder(build_report_data())
            name = f"tracking-report-{datetime.now().strftime('%Y%m%d-%H%M%S')}.{fmt}"
            return self._send_download(body, content_type, name)
        if self.path == "/" or self.path == "/index.html":
            return self._serve_file(STATIC_DIR / "index.html", "text/html; charset=utf-8")
        if self.path == "/events":
            return self._serve_sse()
        if self.path == "/api/history":
            with state["lock"]:
                events = list(state["events"])
                errors = list(state["errors"])
            return self._send_json({"events": events, "errors": errors,
                                     "spec": spec_summary(), "device": state["device"]})
        if parsed.path == "/api/rc":
            raw = (parse_qs(parsed.query).get("session") or [""])[0]
            return self._send_json(rc_payload(int(raw) if raw.isdigit() else None))
        if self.path == "/api/spec":
            return self._send_json(spec_summary())
        if self.path == "/api/version":
            return self._send_json(git_version_info())
        if self.path == "/api/check-update":
            return self._send_json(check_remote_update())
        if self.path == "/api/device":
            return self._send_json(state["device"])
        if self.path in ("/api/mirror/info", "/api/mirror.h264"):
            return self._serve_mirror()
        if self.path == "/api/coverage":
            return self._send_json(build_coverage())
        return self._send_json({"error": "not found"}, status=404)

    def _local_only(self):
        """Mirror/control endpoints drive a real phone: refuse other Hosts (DNS rebinding) and other Origins."""
        port = self.server.server_address[1]
        allowed = {f"127.0.0.1:{port}", f"localhost:{port}"}
        origin = self.headers.get("Origin")
        if self.headers.get("Host") not in allowed or (origin and origin.split("//", 1)[-1] not in allowed):
            self._send_json({"error": "forbidden"}, status=403)
            return False
        return True

    def _mirror_serial(self):
        dev = state["device"]
        serial = dev.get("serial") if dev.get("state") == "connected" else None
        if not serial:
            self._send_json({"error": "chưa có máy nào đang kết nối"}, status=409)
        return serial

    def _serve_mirror(self):
        if not self._local_only():
            return
        serial = self._mirror_serial()
        if not serial:
            return
        try:
            if self.path == "/api/mirror/info":
                return self._send_json(mirror.info(serial))
            self.send_response(200)
            self.send_header("Content-Type", "video/h264")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()  # HTTP/1.0: body runs until the connection closes
            mirror.stream(serial, self.wfile.write)
        except (BrokenPipeError, ConnectionResetError):
            pass  # viewer closed the panel
        except Exception as exc:  # adb missing / wm size failed / etc.
            if self.path == "/api/mirror/info":
                return self._send_json({"error": str(exc)}, status=500)
            print(f"[mirror] {exc}")

    def _post_mirror(self):
        if not self._local_only():
            return
        if self.headers.get("X-Requested-With") != "tracking-ui":  # forces a CORS preflight for foreign pages
            return self._send_json({"error": "forbidden"}, status=403)
        parsed = urlparse(self.path)
        length = int(self.headers.get("Content-Length") or 0)
        if parsed.path == "/api/mirror/show-touches":
            try:
                turn_on = bool(json.loads(self.rfile.read(length) or b"{}").get("on"))
            except ValueError:
                return self._send_json({"error": "json không hợp lệ"}, status=400)
            if not turn_on:  # restoring must work even if the device just got unplugged
                mirror.restore_show_touches()
                return self._send_json({"ok": True})
            serial = self._mirror_serial()
            if not serial:
                return
            try:
                mirror.enable_show_touches(serial)
            except Exception as exc:
                return self._send_json({"error": str(exc)}, status=500)
            return self._send_json({"ok": True})
        serial = self._mirror_serial()
        if not serial:
            return
        try:
            if parsed.path == "/api/mirror/save-record":
                ext = (parse_qs(parsed.query).get("ext") or [""])[0]
                remaining = length

                def read_chunk():
                    nonlocal remaining
                    if remaining <= 0:
                        return b""
                    chunk = self.rfile.read(min(1 << 20, remaining))
                    remaining -= len(chunk)
                    return chunk
                path = mirror.save_upload(serial, ext, read_chunk)
                return self._send_json({"ok": True, "path": str(path)})
            body = json.loads(self.rfile.read(length) or b"{}")
            if parsed.path == "/api/mirror/input":
                mirror.send_input(serial, body)
                return self._send_json({"ok": True})
            if parsed.path == "/api/mirror/screenshot":
                return self._send_json({"ok": True, "path": str(mirror.screenshot(serial))})
        except (ValueError, KeyError, TypeError) as exc:
            return self._send_json({"error": f"yêu cầu không hợp lệ: {exc}"}, status=400)
        except Exception as exc:
            return self._send_json({"error": str(exc)}, status=500)
        return self._send_json({"error": "not found"}, status=404)

    def do_POST(self):
        if self.path.startswith("/api/mirror/"):
            return self._post_mirror()
        if self.path == "/api/self-update":
            return self._send_json(self_update())
        if self.path == "/api/reload":
            store = state["spec_store"]
            if store is None:
                return self._send_json({"ok": False, "error": "chưa có spec store"})
            store.load()
            if not store.error:
                rematch_history()
            broadcast("spec", spec_summary())
            if store.error:
                return self._send_json({"ok": False, "error": store.error})
            return self._send_json({"ok": True, "rows": len(store.rows)})
        if self.path == "/api/refresh":
            store = state["spec_store"]
            lark_error = None
            if store is not None and store.location.is_dir():
                result = fetch_spec_from_lark(store.location)
                if not result.get("ok"):
                    lark_error = result.get("error")
            if store is not None:
                store.load()
                broadcast("spec", spec_summary())
                if store.error:
                    return self._send_json({"ok": False, "error": store.error, "lark_error": lark_error})
                rematch_history()
            with state["lock"]:
                count = len(state["events"])
            return self._send_json({"ok": True, "events": count, "lark_error": lark_error})
        if self.path == "/api/mark":
            # {"row_num": 17, "status": "BLOCKED", "note": "cần tài khoản premium"}
            # status "BLOCKED" = không test được; null/omitted + empty note removes the mark.
            length = int(self.headers.get("Content-Length") or 0)
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
                row_num = int(body["row_num"])
            except (ValueError, KeyError, TypeError):
                return self._send_json({"ok": False, "error": "cần {row_num, status?, note?}"}, status=400)
            status = body.get("status")
            if status not in (None, "BLOCKED"):
                return self._send_json({"ok": False, "error": "status chỉ nhận BLOCKED hoặc null"}, status=400)
            note = (body.get("note") or "").strip()
            with state["lock"]:
                if status is None and not note:
                    state["marks"].pop(row_num, None)
                else:
                    state["marks"][row_num] = {"status": status, "note": note}
            broadcast("coverage", {"row_num": row_num})
            return self._send_json({"ok": True})
        if self.path == "/api/clear":
            length = int(self.headers.get("Content-Length") or 0)
            try:
                scope = json.loads(self.rfile.read(length) or b"{}").get("scope")
            except ValueError:
                scope = None
            with state["lock"]:
                if scope == "errors":
                    state["errors"] = []
                elif scope == "ads":
                    state["events"] = [e for e in state["events"] if not is_ads_event(e)]
                elif scope == "rc":
                    state["rc_sessions"] = []
                elif scope == "marks":
                    state["marks"] = {}
                elif scope == "other":
                    state["events"] = [e for e in state["events"] if is_ads_event(e)]
                else:
                    return self._send_json({"ok": False, "error": "scope không hợp lệ"}, status=400)
            return self._send_json({"ok": True})
        return self._send_json({"error": "not found"}, status=404)

    def _serve_file(self, path, content_type):
        try:
            body = path.read_bytes()
        except FileNotFoundError:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _serve_sse(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

        q = queue.Queue()
        with state["lock"]:
            state["subscribers"].append(q)
        try:
            self.wfile.write(f"event: spec\ndata: {json.dumps(spec_summary(), ensure_ascii=False)}\n\n".encode("utf-8"))
            self.wfile.write(f"event: device\ndata: {json.dumps(state['device'], ensure_ascii=False)}\n\n".encode("utf-8"))
            self.wfile.flush()
            last_ping = time.time()
            while True:
                try:
                    data = q.get(timeout=15)
                    self.wfile.write(data.encode("utf-8"))
                    self.wfile.flush()
                except queue.Empty:
                    now = time.time()
                    if now - last_ping >= 15:
                        self.wfile.write(b": ping\n\n")
                        self.wfile.flush()
                        last_ping = now
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            with state["lock"]:
                if q in state["subscribers"]:
                    state["subscribers"].remove(q)


# ---- self-update: this checkout is a git clone; "Update" = git pull + restart this same process ----

_httpd_ref = {"server": None}
_runner_ref = {"runner": None}


def _git(*args, timeout=30):
    return subprocess.run(["git", "-C", str(BASE_DIR), *args], capture_output=True, text=True, timeout=timeout)


def git_version_info():
    """Local, offline, always fast — safe to call on every page load."""
    rev = _git("rev-parse", "--short", "HEAD")
    if rev.returncode != 0:
        return {"is_repo": False}
    date = _git("log", "-1", "--format=%cI")
    branch = _git("rev-parse", "--abbrev-ref", "HEAD")
    dirty = _git("status", "--porcelain")
    remote = _git("remote", "get-url", "origin")
    return {
        "is_repo": True,
        "commit": rev.stdout.strip(),
        "date": date.stdout.strip() or None,
        "branch": branch.stdout.strip() or None,
        "dirty": bool(dirty.stdout.strip()),
        "has_remote": remote.returncode == 0,
    }


def check_remote_update():
    """Network call (git fetch) — only run when the user actually asks (page load / Refresh),
    never on a timer: colleagues on a flaky connection shouldn't see this fail silently and often."""
    info = git_version_info()
    if not info["is_repo"] or not info["has_remote"]:
        return {"checked": False, "error": "Không phải git clone, hoặc chưa có remote 'origin'."}
    fetch = _git("fetch", "--quiet", timeout=20)
    if fetch.returncode != 0:
        return {"checked": False, "error": (fetch.stderr or fetch.stdout).strip()[-300:] or "git fetch lỗi"}
    behind = _git("rev-list", "--count", "HEAD..@{upstream}")
    if behind.returncode != 0:
        return {"checked": False, "error": "Branch hiện tại chưa track branch nào trên remote."}
    n = int(behind.stdout.strip() or "0")
    return {"checked": True, "behind": n, "update_available": n > 0}


def _restart_self():
    """Re-exec this same process from scratch (same argv), now running the just-pulled code.
    Must close the listening socket first — os.execv keeps file descriptors open across exec, so
    the freshly re-exec'd server.py would otherwise fail to bind the same port (still held open
    by this, technically-still-alive-until-exec-completes process). Must also stop the runner
    (kills its adb logcat/screenrecord children) for the same reason: os.execv doesn't unwind the
    stack or run any `finally` block, so anything spawned via subprocess.Popen survives the exec
    as an orphan — confirmed on-device: 45 stray `adb logcat` processes piled up from repeated
    restarts before this was added, one leaked set per restart, still holding the USB connection
    open each time long after the server that spawned them was gone."""
    runner = _runner_ref["runner"]
    if runner is not None:
        try:
            runner.stop()
        except Exception:
            pass
    httpd = _httpd_ref["server"]
    if httpd is not None:
        try:
            httpd.socket.close()
        except Exception:
            pass
    os.execv(sys.executable, [sys.executable] + sys.argv)


def self_update():
    """git pull --ff-only, then restart this process to run the new code. Refuses on local
    changes (a colleague poking at the code, or a half-finished manual edit) rather than risk
    losing them — that always needs a person to look, never something to silently discard."""
    info = git_version_info()
    if not info["is_repo"] or not info["has_remote"]:
        return {"ok": False, "error": "Không phải git clone, hoặc chưa có remote 'origin' — không tự update được."}
    if info["dirty"]:
        return {"ok": False, "error": "Có thay đổi chưa commit trong thư mục tool — tự update bị chặn để khỏi mất "
                                      "thay đổi đó. Nhờ dev kiểm tra (git status) rồi update tay."}
    pull = _git("pull", "--ff-only", timeout=60)
    if pull.returncode != 0:
        return {"ok": False, "error": (pull.stderr or pull.stdout).strip()[-500:] or "git pull lỗi"}
    if "Already up to date" in pull.stdout:
        return {"ok": True, "restarted": False, "message": "Đã ở bản mới nhất."}
    threading.Timer(0.4, _restart_self).start()  # after this HTTP response is safely sent
    return {"ok": True, "restarted": True, "message": pull.stdout.strip()}


def fetch_spec_from_lark(folder):
    """Runs fetch_spec.py against LARK_SPEC_URL, dropping a fresh .xlsx straight into `folder`
    (real Lark row numbers, so "row 17" here is row 17 on Lark too). The existing spec_watcher
    thread below picks the new file up from there like any manual browser export — this just
    automates that export step so nobody has to open Lark, Download As, and drop the file by
    hand anymore. Only meaningful in folder mode (a single fixed --spec file is left alone)."""
    try:
        proc = subprocess.run([sys.executable, str(BASE_DIR / "fetch_spec.py"), LARK_SPEC_URL,
                               "--out-dir", str(folder)], capture_output=True, text=True, timeout=30)
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    if proc.returncode != 0:
        return {"ok": False, "error": (proc.stderr or proc.stdout or "lỗi không rõ").strip()[-500:]}
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {"ok": False, "error": "fetch_spec.py không trả JSON hợp lệ"}


def lark_spec_watcher(folder, interval=LARK_FETCH_INTERVAL):
    """Background thread: re-runs fetch_spec_from_lark every `interval` seconds so the local
    spec folder — and from there the dashboard — stays in sync with Lark on its own. A failure
    (no lark-cli, no network, no permission on the sheet) is logged once, not spammed every
    cycle, and is otherwise harmless: the tool just keeps using whatever .xlsx is already in
    the folder, same as if this thread didn't exist."""
    warned = False
    while True:
        result = fetch_spec_from_lark(folder)
        if result.get("ok"):
            if warned:
                print("[lark] Đồng bộ từ Lark lại thành công.")
            warned = False
        elif not warned:
            print(f"[lark] Không tự đồng bộ được spec từ Lark: {result.get('error')}")
            print("[lark] Sẽ tự thử lại mỗi "
                  f"{interval}s — nếu vẫn lỗi, export thủ công như README mục 1.")
            warned = True
        time.sleep(interval)


def spec_watcher(poll_interval=2.0):
    """Background thread: auto-reloads the spec file whenever its mtime changes,
    so overwriting spec.xlsx with a fresh export is picked up without needing
    to click "Reload spec file" by hand."""
    while True:
        time.sleep(poll_interval)
        store = state["spec_store"]
        if store is None:
            continue
        if store.has_changed():
            store.load()
            if store.error:
                print(f"[spec] Lỗi reload tự động: {store.error}")
            else:
                print(f"[spec] Tự động reload — {len(store.rows)} dòng")
                rematch_history()
            broadcast("spec", spec_summary())


def main():
    parser = argparse.ArgumentParser(description="Local realtime tracking test server")
    parser.add_argument("--spec", default="~/Documents/tracking-spec",
                         help="File .xlsx cụ thể, HOẶC 1 thư mục (mặc định: ~/Documents/tracking-spec) "
                              "— nếu là thư mục, tool tự lấy file .xlsx mới nhất trong đó mỗi khi có "
                              "file mới, không cần đè/đổi tên file.")
    parser.add_argument("--serial", default=None,
                         help="Ghim vào 1 serial cụ thể (mặc định: tự nhận device đang cắm, "
                              "tự chuyển khi rút/cắm máy khác)")
    parser.add_argument("--tag", default="TrackingEvent", help="Logcat tag cần lọc (mặc định: TrackingEvent)")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-clear", action="store_true", help="Không clear logcat buffer khi khởi động")
    parser.add_argument("--allow-param", action="append", default=[], metavar="KEY",
                         help="Param chung cho mọi event mà spec không khai (vd is_paywall) — không tính là "
                              "'Param lạ'. Lặp lại cờ cho nhiều param.")
    args = parser.parse_args()
    if args.allow_param:
        spec_store.GLOBAL_ALLOWED_KEYS.update(args.allow_param)
        print(f"[spec] Param chung được phép: {', '.join(sorted(spec_store.GLOBAL_ALLOWED_KEYS))}")

    spec_location = Path(args.spec).expanduser()
    if not spec_location.exists():
        if spec_location.suffix.lower() == ".xlsx":
            print(f"Không tìm thấy file spec: {spec_location}")
            sys.exit(1)
        spec_location.mkdir(parents=True, exist_ok=True)
        print(f"[spec] Đã tạo thư mục spec: {spec_location}")

    store = SpecStore(spec_location)
    if store.error:
        print(f"[warn] {store.error}")
    else:
        print(f"[spec] Đã load {len(store.rows)} dòng từ {store.path}")
    state["spec_store"] = store
    state["tag"] = args.tag

    threading.Thread(target=spec_watcher, daemon=True).start()
    if spec_location.is_dir():
        threading.Thread(target=lark_spec_watcher, args=(spec_location,), daemon=True).start()
        print(f"[lark] Tự đồng bộ spec từ Lark mỗi {LARK_FETCH_INTERVAL}s — không cần export tay nữa "
              f"(vẫn export thủ công được nếu muốn, xem README mục 1).")
    else:
        print(f"[lark] --spec trỏ vào 1 file cụ thể ({spec_location.name}) nên không tự đồng bộ từ Lark; "
              f"dùng --spec <thư mục> (mặc định) để bật lại.")

    runner = TrackingRunner(
        tag=args.tag,
        on_event=on_event,
        on_status=on_status,
        on_device=on_device,
        on_error=on_error,
        on_rc=on_rc,
        serial_override=args.serial,
        clear_first=not args.no_clear,
    )
    runner.start()
    _runner_ref["runner"] = runner

    # A plain `kill <pid>` (SIGTERM, the default) has no handler in Python by default -> the
    # process just dies immediately, skipping every `finally` block below, including runner.stop().
    # That's exactly how the 45-orphaned-adb-processes situation above happened: someone restarts
    # the server this way, the old process's adb logcat/screenrecord children get left running,
    # every single time. SIGINT (Ctrl+C) already raises KeyboardInterrupt on its own; make SIGTERM
    # do the same so both paths hit the same clean shutdown.
    def _on_sigterm(signum, frame):
        raise KeyboardInterrupt()
    signal.signal(signal.SIGTERM, _on_sigterm)

    httpd = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    _httpd_ref["server"] = httpd
    url = f"http://127.0.0.1:{args.port}"
    print(f"[server] Dashboard: {url}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        runner.stop()
        httpd.shutdown()


if __name__ == "__main__":
    main()
