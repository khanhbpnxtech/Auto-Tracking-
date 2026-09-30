import queue
import re
import subprocess
import threading
import time

LOG_LINE_RE = re.compile(r'logEvent:\s*(?P<event>[A-Za-z0-9_]+)\s*-\s*Bundle\[\{(?P<params>.*)\}\]')
PARAM_RE = re.compile(r'([A-Za-z0-9_\.]+)=([^,}]*)(?:,\s*|$)')
# `adb logcat -v threadtime -v year` line header, same fields Android Studio shows:
#   2026-09-25 08:39:12.508 24603 24717 D TrackingEvent: message
HEADER_RE = re.compile(
    r'^(?P<time>(?:\d{4}-)?\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3})\s+'
    r'(?P<pid>\d+)\s+(?P<tid>\d+)\s+(?P<level>[VDIWEF])\s+'
    r'(?P<tag>.+?)\s*: ?(?P<msg>.*)$'
)

# Errors are grouped per (pid, tag): a Java crash / stack trace arrives as many
# consecutive logcat lines sharing the same pid+tag, so we buffer and merge
# them into a single entry once that stream goes quiet for this long.
ERROR_FLUSH_IDLE_SECONDS = 0.6


def parse_bundle(params_str):
    result = {}
    for m in PARAM_RE.finditer(params_str):
        key = m.group(1).strip()
        val = m.group(2).strip()
        if key:
            result[key] = val
    return result


def parse_line(line):
    m = LOG_LINE_RE.search(line)
    if not m:
        return None
    event_name = m.group("event").strip()
    bundle = parse_bundle(m.group("params"))
    return event_name, bundle


def parse_header(line):
    m = HEADER_RE.match(line)
    return m.groupdict() if m else None


def parse_error_line(line):
    h = parse_header(line)
    if not h or h["level"] not in ("E", "F"):
        return None
    return {
        "level": h["level"],
        "tag": h["tag"].strip(),
        "pid": h["pid"],
        "tid": h["tid"],
        "log_time": h["time"],
        "message": h["msg"],
    }


# `fetchRemoteConfig boolean: Native_home_top: false` -> a config key; `fetchRemoteConfig: success (attempt 1)` -> status
RC_KEY_RE = re.compile(r'^fetchRemoteConfig\s+(?P<type>\w+):\s+(?P<key>[^\s:]+):\s?(?P<value>.*)$')
RC_STATUS_RE = re.compile(r'^fetchRemoteConfig:\s*(?P<message>.*)$')


def parse_rc_line(line):
    h = parse_header(line)
    if not h:
        return None
    msg = h["msg"].strip()
    base = {"log_time": h["time"], "pid": h["pid"], "tid": h["tid"], "level": h["level"]}
    m = RC_KEY_RE.match(msg)
    if m:
        return {**base, "kind": "key", "type": m.group("type"), "key": m.group("key"), "value": m.group("value").strip()}
    m = RC_STATUS_RE.match(msg)
    if m:
        return {**base, "kind": "status", "message": m.group("message").strip()}
    return None


class PackageResolver:
    """Resolves a logcat PID to the app's package name via /proc/<pid>/cmdline,
    cached per (serial, pid) since a PID normally stays the same for the whole
    life of the app process being tested."""

    def __init__(self):
        self._cache = {}

    def resolve(self, serial, pid):
        key = (serial, pid)
        if key in self._cache:
            return self._cache[key]
        pkg = self._query(serial, pid)
        self._cache[key] = pkg
        return pkg

    @staticmethod
    def _query(serial, pid):
        try:
            out = subprocess.run(
                ["adb", "-s", serial, "shell", f"cat /proc/{pid}/cmdline"],
                capture_output=True, text=True, timeout=3,
            )
            name = out.stdout.split("\x00")[0].strip()
            return name or None
        except Exception:
            return None

    def clear(self):
        self._cache.clear()


def query_device_model(serial):
    try:
        out = subprocess.run(
            ["adb", "-s", serial, "shell", "getprop", "ro.product.model"],
            capture_output=True, text=True, timeout=5,
        )
        return out.stdout.strip() or None
    except Exception:
        return None


def list_authorized_devices():
    """Returns serials currently in 'device' (authorized, ready) state."""
    try:
        out = subprocess.run(["adb", "devices"], capture_output=True, text=True, timeout=10)
    except FileNotFoundError:
        return None  # signals adb itself is missing
    devices = []
    for line in out.stdout.splitlines()[1:]:
        line = line.strip()
        if not line:
            continue
        parts = line.split("\t")
        if len(parts) == 2 and parts[1].strip() == "device":
            devices.append(parts[0].strip())
    return devices


class TrackingRunner(threading.Thread):
    """Continuously watches `adb devices` and tails logcat for whichever device is
    currently plugged in. If the device is swapped or unplugged/replugged, it
    automatically switches without needing a restart.

    Three logcat streams run per device: one filtered to the tracking tag (for
    logEvent lines), one at Error/Fatal level across all tags (for app
    crashes / Log.e() calls), and one for RemoteConfigManager (see the RC
    Manager tab). Error-level lines are only surfaced for packages that have
    already been seen emitting a tracking event — that's our proxy for "the
    app currently being tested", and it naturally excludes generic Android
    system noise without needing a hardcoded denylist.

    Calls on_event(raw_line, event_name, bundle, package, meta) per parsed logEvent
    line, on_error(entry) per grouped crash/error, and on_status(msg) /
    on_device(info) for lifecycle updates."""

    def __init__(self, tag, on_event, on_status, on_device=None, on_error=None, on_rc=None,
                 rc_tag="RemoteConfigManager", serial_override=None, clear_first=True, poll_interval=2.0):
        super().__init__(daemon=True)
        self.tag = tag
        self.rc_tag = rc_tag
        self.on_rc = on_rc or (lambda entry: None)
        self.on_event = on_event
        self.on_status = on_status
        self.on_device = on_device or (lambda info: None)
        self.on_error = on_error or (lambda entry: None)
        self.serial_override = serial_override
        self.clear_first = clear_first
        self.poll_interval = poll_interval
        self._stop = threading.Event()
        self._procs = []
        self.current_serial = None
        self._last_wait_state = None
        # User's live pick from the device dropdown (like Android Studio's "Running Devices") when
        # more than one device is plugged in — set from outside via the /api/select-device route,
        # checked fresh every poll so switching takes effect on the next cycle. Different from
        # serial_override (a fixed --serial CLI flag): this can change anytime and is simply
        # ignored, not an error, whenever the picked device isn't actually connected right now.
        self.preferred_serial = None
        self._pkg_resolver = PackageResolver()
        self.active_packages = set()

    def stop(self):
        self._stop.set()
        for proc in self._procs:
            if proc and proc.poll() is None:
                proc.terminate()

    def _resolve_target_serial(self):
        devices = list_authorized_devices()
        if devices is None:
            return "NO_ADB"
        if self.serial_override:
            return self.serial_override if self.serial_override in devices else "WAITING"
        if self.preferred_serial and self.preferred_serial in devices:
            return self.preferred_serial
        if len(devices) == 1:
            return devices[0]
        if len(devices) == 0:
            return "WAITING"
        return ("AMBIGUOUS", tuple(devices))

    def _should_switch_away(self, serial, current_devices):
        """True once the user has picked a *different, currently-connected* device from the
        dropdown — checked periodically from inside _tail_one_device's own loop so a live switch
        doesn't have to wait for the current device to be unplugged first (unlike serial_override,
        preferred_serial is meant to change anytime)."""
        return bool(self.preferred_serial) and self.preferred_serial != serial and self.preferred_serial in current_devices

    def run(self):
        while not self._stop.is_set():
            target = self._resolve_target_serial()

            if target == "NO_ADB":
                self._emit_wait("NO_ADB", "Không tìm thấy lệnh 'adb' trong PATH. Cài Android platform-tools rồi thử lại.")
                time.sleep(self.poll_interval)
                continue

            if target == "WAITING":
                label = f" (serial={self.serial_override})" if self.serial_override else ""
                self._emit_wait("WAITING", f"Đang chờ device{label}... cắm máy / bật USB debugging rồi bấm Allow.")
                time.sleep(self.poll_interval)
                continue

            if isinstance(target, tuple):
                _, candidates = target
                self._emit_wait("AMBIGUOUS",
                                 f"Có {len(candidates)} device đang cắm ({', '.join(candidates)}) — "
                                 f"chọn 1 máy ở ô chọn device phía trên.")
                time.sleep(self.poll_interval)
                continue

            # target is a concrete serial string
            self._last_wait_state = None
            self.current_serial = target
            self._pkg_resolver.clear()
            self.active_packages = set()
            model = query_device_model(target)
            self.on_device({"state": "connected", "serial": target, "model": model})
            label = f"{model} ({target})" if model else target
            self.on_status(f"Phát hiện device {label}, bắt đầu tail log (tag={self.tag})...")
            self._tail_one_device(target)
            switched = self.preferred_serial and self.preferred_serial != target
            self.current_serial = None
            self.on_device({"state": "disconnected", "serial": target, "model": model})
            if switched:
                self.on_status(f"Đã chuyển sang device khác theo lựa chọn — dừng tail {target}.")
            elif not self._stop.is_set():
                self.on_status(f"Device {target} đã ngắt kết nối, đang chờ device khác...")

    def _emit_wait(self, state, message):
        if self._last_wait_state != state:
            self._last_wait_state = state
            self.on_status(message)
            self.on_device({"state": state.lower(), "serial": None})

    def _spawn_logcat(self, serial, filterspec):
        cmd = ["adb", "-s", serial, "logcat", "-v", "threadtime", "-v", "year"] + filterspec
        return subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                 text=True, bufsize=1)

    def _backfill_rc(self, serial):
        """RemoteConfig is only fetched once per app start, so pick up the last fetch(es) still sitting in the
        logcat buffer before we clear it — otherwise starting the tool after the app would show nothing."""
        try:
            out = subprocess.run(["adb", "-s", serial, "logcat", "-d", "-v", "threadtime", "-v", "year",
                                  f"{self.rc_tag}:D", "*:S"], capture_output=True, text=True, timeout=20).stdout
        except Exception:
            return
        for line in out.splitlines():
            self._handle_rc_line(serial, line)

    def _handle_rc_line(self, serial, line):
        entry = parse_rc_line(line)
        if entry:
            entry["package"] = self._pkg_resolver.resolve(serial, entry["pid"])
            self.on_rc(entry)

    def _tail_one_device(self, serial):
        if self.clear_first:
            self._backfill_rc(serial)
            try:
                subprocess.run(["adb", "-s", serial, "logcat", "-c"], timeout=10, capture_output=True)
            except Exception as e:
                self.on_status(f"Không clear được logcat buffer: {e}")

        try:
            proc_track = self._spawn_logcat(serial, [f"{self.tag}:D", "*:S"])
            proc_error = self._spawn_logcat(serial, ["*:E"])
            proc_rc = self._spawn_logcat(serial, [f"{self.rc_tag}:D", "*:S"])
        except FileNotFoundError:
            self.on_status("Không tìm thấy lệnh 'adb' trong PATH.")
            self._stop.set()
            return

        self._procs = [proc_track, proc_error, proc_rc]

        # Blocking reader threads feed tagged lines into one shared queue; the
        # main loop below consumes with a timeout so it can also poll device
        # liveness and flush grouped error buffers. Doing the blocking
        # readline() on background threads avoids the classic
        # select()-vs-buffered-TextIOWrapper mismatch, where select() can
        # report "not ready" even though lines are already sitting in
        # Python's internal read buffer.
        line_q = queue.Queue()

        def reader(proc, source):
            try:
                for line in proc.stdout:
                    line_q.put((source, line))
            except Exception:
                pass
            finally:
                line_q.put((source, None))  # sentinel: EOF

        threading.Thread(target=reader, args=(proc_track, "track"), daemon=True).start()
        threading.Thread(target=reader, args=(proc_error, "error"), daemon=True).start()
        threading.Thread(target=reader, args=(proc_rc, "rc"), daemon=True).start()

        error_buffers = {}  # (pid, tag) -> {"level","tag","pid","package","lines":[...], "last": ts}

        def flush_stale_errors(force=False):
            now = time.time()
            stale_keys = [k for k, v in error_buffers.items()
                          if force or now - v["last"] >= ERROR_FLUSH_IDLE_SECONDS]
            for k in stale_keys:
                buf = error_buffers.pop(k)
                self.on_error({
                    "time": buf["last"],
                    "log_time": buf["log_time"],
                    "pid": buf["pid"],
                    "tid": buf["tid"],
                    "level": buf["level"],
                    "tag": buf["tag"],
                    "package": buf["package"],
                    "message": "\n".join(buf["lines"]),
                })

        ended = {"track": False, "error": False, "rc": False}
        last_check = time.time()
        try:
            while not self._stop.is_set():
                try:
                    source, line = line_q.get(timeout=1.0)
                except queue.Empty:
                    flush_stale_errors()
                    now = time.time()
                    if now - last_check >= self.poll_interval:
                        last_check = now
                        current = list_authorized_devices()
                        if current is not None and (serial not in current or self._should_switch_away(serial, current)):
                            break  # device unplugged / swapped, or user picked a different one
                    continue

                if line is None:
                    ended[source] = True
                    if any(ended.values()):
                        break  # any logcat process died -> restart all
                    continue

                line = line.rstrip("\n")
                if not line:
                    continue

                if source == "track":
                    parsed = parse_line(line)
                    if parsed:
                        event_name, bundle = parsed
                        header = parse_header(line) or {}
                        pid = header.get("pid")
                        package = self._pkg_resolver.resolve(serial, pid) if pid else None
                        if package:
                            self.active_packages.add(package)
                        self.on_event(line, event_name, bundle, package,
                                      {"log_time": header.get("time"), "pid": pid, "tid": header.get("tid")})
                elif source == "rc":
                    self._handle_rc_line(serial, line)
                else:  # source == "error"
                    parsed = parse_error_line(line)
                    if not parsed:
                        continue
                    package = self._pkg_resolver.resolve(serial, parsed["pid"])
                    if not package or package not in self.active_packages:
                        continue  # not the app under test (or app not seen yet) -> skip
                    key = (parsed["pid"], parsed["tag"])
                    buf = error_buffers.get(key)
                    if buf is None:
                        buf = {"level": parsed["level"], "tag": parsed["tag"],
                               "package": package, "lines": [],
                               "log_time": parsed["log_time"],
                               "pid": parsed["pid"], "tid": parsed["tid"]}
                        error_buffers[key] = buf
                    buf["level"] = parsed["level"] if parsed["level"] == "F" else buf["level"]
                    buf["lines"].append(parsed["message"])
                    buf["last"] = time.time()

                now = time.time()
                if now - last_check >= self.poll_interval:
                    last_check = now
                    current = list_authorized_devices()
                    if current is not None and (serial not in current or self._should_switch_away(serial, current)):
                        break  # device unplugged / swapped, or user picked a different one
        finally:
            flush_stale_errors(force=True)
            for proc in (proc_track, proc_error, proc_rc):
                if proc.poll() is None:
                    proc.terminate()
                    try:
                        proc.wait(timeout=3)
                    except Exception:
                        proc.kill()
