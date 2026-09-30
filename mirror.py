"""Screen mirror + remote control for the connected device.

- stream():  raw H.264 from `adb exec-out screenrecord`, decoded in the browser with WebCodecs
- send_input(): tap / swipe / key through one persistent `adb shell` per device
- enable/restore_show_touches(): toggles Android's "Show taps" (physical touches only; taps sent from the web UI
  are drawn by the page itself)
- screenshot() / save_upload(): files go straight to ~/Desktop
"""
import os
import re
import shutil
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path

LONG_EDGE = 1280      # downscale so the longest edge is at most this many px
BIT_RATE = 8_000_000
TIME_LIMIT = 180      # screenrecord's hard cap per run; we restart it seamlessly
SAVE_DIR = Path.home() / "Desktop"
KEYCODES = {"back": 4, "home": 3, "recents": 187, "power": 26}
UPLOAD_EXTS = {"mp4", "webm"}


def screen_size(serial):
    """Real (width, height) of the device screen, honouring `wm size` overrides."""
    out = subprocess.run(["adb", "-s", serial, "shell", "wm", "size"],
                         capture_output=True, text=True, timeout=10).stdout
    sizes = re.findall(r"(\d+)x(\d+)", out)
    if not sizes:
        raise RuntimeError("không đọc được kích thước màn hình (adb shell wm size)")
    w, h = sizes[-1]  # last line is "Override size" when present, else "Physical size"
    return int(w), int(h)


def stream_size(w, h):
    """Encoder size: scaled to LONG_EDGE, each side a multiple of 16 (encoder requirement)."""
    scale = min(1.0, LONG_EDGE / max(w, h))
    snap = lambda v: max(16, int(round(v * scale / 16)) * 16)
    return snap(w), snap(h)


def info(serial):
    w, h = screen_size(serial)
    sw, sh = stream_size(w, h)
    return {"serial": serial, "width": w, "height": h, "stream_width": sw, "stream_height": sh}


def _kill_remote_screenrecord(serial):
    """`adb exec-out screenrecord` does NOT reliably kill the actual screenrecord process running
    on the DEVICE when the local adb client is killed/dies — exec-out allocates no pty and doesn't
    forward signals, so the remote process is routinely left running (confirmed on-device: multiple
    stuck `screenrecord` in futex_wait_queue_me, all fighting over the one hardware video encoder).
    Left unchecked, every reconnect/restart adds one more zombie until the encoder is so contended
    that NO stream can get a keyframe out — which looks exactly like "recording freezes, then jumps
    ahead" once whichever zombie finally exits frees the encoder up. Best-effort, always safe to run
    even when nothing is stuck: never raises, never blocks the caller on a slow/dead device."""
    try:
        subprocess.run(["adb", "-s", serial, "shell", "pkill", "-9", "screenrecord"],
                       capture_output=True, timeout=5)
    except Exception:
        pass


# Per-serial "who's in charge" registry, so a brand-new call can always immediately pre-empt
# whatever came before it rather than waiting for it — see stream()'s docstring for why a wait
# (even a lock) isn't good enough here: the previous run can be genuinely, indefinitely stuck
# (confirmed cause: a video ad or anything else on-device decoding video contends with
# screenrecord for the same hardware codec — nothing to do with our own code — and once that
# happens, the stuck run isn't going to write() anything that would let it notice its client is
# long gone), so waiting for it to notice and unwind can mean waiting forever.
_active = {}
_active_guard = threading.Lock()


def stream(serial, write):
    """Pipe H.264 to `write(bytes)` until it raises (client gone) or screenrecord keeps failing.

    Pre-empts on every call: a client reconnecting (page reload with the panel remembered open,
    "Kết nối lại", a dropped-then-retried fetch) can call this again before the OLD call has
    finished noticing its connection is dead — confirmed by watching two `adb exec-out
    screenrecord` running at once, born seconds apart, both feeding THIS function, fighting over
    the same hardware encoder. So every call immediately kills whatever the last call for this
    serial left running (locally and — see _kill_remote_screenrecord — on the device) before
    touching anything else, and an old call still in its read loop notices it's been superseded
    the moment its (just-killed) subprocess's pipe hits EOF, and quietly stops instead of retrying."""
    with _active_guard:
        prev = _active.get(serial)
        my_epoch = (prev["epoch"] + 1) if prev else 1
        _active[serial] = {"proc": None, "epoch": my_epoch}
    if prev and prev["proc"] is not None:
        try:
            prev["proc"].kill()
        except Exception:
            pass
    _kill_remote_screenrecord(serial)

    def superseded():
        with _active_guard:
            return _active.get(serial, {}).get("epoch") != my_epoch

    try:
        sw, sh = stream_size(*screen_size(serial))
        cmd = ["adb", "-s", serial, "exec-out", "screenrecord", "--output-format=h264",
               "--size", f"{sw}x{sh}", "--bit-rate", str(BIT_RATE),
               "--time-limit", str(TIME_LIMIT), "-"]
        quick_failures = 0
        while quick_failures < 3:
            if superseded():
                return
            started = time.time()
            got_data = False
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            with _active_guard:
                if _active.get(serial, {}).get("epoch") != my_epoch:
                    proc.kill()
                    proc.wait()
                    return  # a newer call raced in right as we spawned — let it own the device
                _active[serial]["proc"] = proc
            try:
                fd = proc.stdout.fileno()
                while True:
                    chunk = os.read(fd, 65536)
                    if not chunk:
                        break
                    got_data = True
                    write(chunk)
            finally:
                proc.kill()
                proc.wait()
                if not superseded():
                    _kill_remote_screenrecord(serial)  # see docstring — the local kill alone isn't enough
            if superseded():
                return
            # Normal end is the 180s cap: loop and restart. A run that dies fast means a real failure.
            quick_failures = quick_failures + 1 if (not got_data or time.time() - started < 5) else 0
    finally:
        with _active_guard:
            if _active.get(serial, {}).get("epoch") == my_epoch:
                del _active[serial]


# ---- input: one long-lived `adb shell` per device, commands are just written to its stdin ----

class _Shell:
    def __init__(self, serial):
        self.serial = serial
        self.lock = threading.Lock()
        self.proc = None

    def run(self, line):
        with self.lock:
            for attempt in (1, 2):
                if self.proc is None or self.proc.poll() is not None:
                    self.proc = subprocess.Popen(["adb", "-s", self.serial, "shell"], stdin=subprocess.PIPE,
                                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                try:
                    self.proc.stdin.write((line + "\n").encode())
                    self.proc.stdin.flush()
                    return
                except (BrokenPipeError, OSError):
                    self.proc = None
                    if attempt == 2:
                        raise


_shells = {}


def _shell(serial):
    if serial not in _shells:
        _shells[serial] = _Shell(serial)
    return _shells[serial]


def send_input(serial, cmd):
    """cmd: {"type": "tap", x, y} | {"type": "swipe", x1, y1, x2, y2, ms} | {"type": "key", name}.
    Every value is coerced to int / looked up in KEYCODES, so nothing user-supplied reaches the shell as text."""
    kind = cmd.get("type")
    w, h = screen_size(serial)
    clamp = lambda v, hi: max(0, min(hi - 1, int(v)))
    if kind == "tap":
        line = f"input tap {clamp(cmd['x'], w)} {clamp(cmd['y'], h)}"
    elif kind == "swipe":
        ms = max(50, min(5000, int(cmd.get("ms", 300))))
        line = (f"input swipe {clamp(cmd['x1'], w)} {clamp(cmd['y1'], h)} "
                f"{clamp(cmd['x2'], w)} {clamp(cmd['y2'], h)} {ms}")
    elif kind == "key":
        line = f"input keyevent {KEYCODES[cmd['name']]}"
    else:
        raise ValueError("type không hợp lệ")
    _shell(serial).run(line)


# ---- Show taps (Developer options) — restored to the previous value when recording ends ----

_prev_touches = {}


def _adb_settings(serial, *args):
    return subprocess.run(["adb", "-s", serial, "shell", "settings", *args],
                          capture_output=True, text=True, timeout=10).stdout.strip()


def enable_show_touches(serial):
    if serial not in _prev_touches:
        prev = _adb_settings(serial, "get", "system", "show_touches")
        _prev_touches[serial] = prev if prev in ("0", "1") else "0"
    _adb_settings(serial, "put", "system", "show_touches", "1")


def restore_show_touches():
    """Put back the original value on every device we changed (also covers a device switched mid-recording)."""
    for serial in list(_prev_touches):
        try:
            _adb_settings(serial, "put", "system", "show_touches", _prev_touches.pop(serial))
        except Exception:
            pass


# ---- files -> Desktop ----

def _stamp(serial):
    return f"{serial}-{datetime.now().strftime('%Y%m%d-%H%M%S')}"


def screenshot(serial):
    """Full-resolution PNG (adb screencap), saved on the Desktop."""
    png = subprocess.run(["adb", "-s", serial, "exec-out", "screencap", "-p"],
                         capture_output=True, timeout=20).stdout
    if not png.startswith(b"\x89PNG"):
        raise RuntimeError("adb screencap không trả về ảnh (màn hình bị khoá bảo mật?)")
    path = SAVE_DIR / f"screenshot-{_stamp(serial)}.png"
    path.write_bytes(png)
    return path


def save_upload(serial, ext, read_chunk):
    """Stream an uploaded recording to the Desktop, then always pass it through ffmpeg once before
    handing back the final path — Chromium's own MediaRecorder mp4 output routinely writes the moov
    atom (duration/seek index) at the END of the file or with a broken duration, which plays back
    fine but leaves QuickTime's Trim (and most other trimming) unable to select a range at all.
    webm is fully transcoded to mp4 (the browser only records webm when it can't do mp4 natively,
    so there's no faster remux option); mp4 gets a fast lossless remux (-c copy, no re-encode)."""
    if ext not in UPLOAD_EXTS:
        raise ValueError("định dạng không hợp lệ")
    path = SAVE_DIR / f"record-{_stamp(serial)}.{ext}"
    with open(path, "wb") as f:
        while True:
            chunk = read_chunk()
            if not chunk:
                break
            f.write(chunk)
    if not shutil.which("ffmpeg"):
        return path
    fixed = path if ext == "mp4" else path.with_suffix(".mp4")
    tmp = fixed.with_name(fixed.stem + ".fixing.mp4")
    if ext == "mp4":
        cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", str(path),
               "-c", "copy", "-movflags", "+faststart", str(tmp)]
    else:
        cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", str(path), "-r", "30",
               "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "veryfast", "-crf", "20",
               "-movflags", "+faststart", str(tmp)]
    done = subprocess.run(cmd, capture_output=True, timeout=600)
    if done.returncode == 0:
        tmp.replace(fixed)
        if fixed != path:
            path.unlink()
        return fixed
    tmp.unlink(missing_ok=True)
    return path  # ffmpeg failed (corrupt input, etc.) — still give back the raw recording rather than nothing
