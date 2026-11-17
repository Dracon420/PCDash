"""Game-art screensaver, run by PCDash (2026-11-12).

Why not a real .scr: Smart App Control is ON on PC1 and blocks any self-built,
unsigned program (a compiled GameArt.scr was refused). So PCDash does the job:

* idle >= idle_minutes (GetLastInputInfo), the screen is NOT being held awake by
  a video/game (system ES_DISPLAY_REQUIRED bit clear), no fullscreen app and the PC
  is not locked (SHQueryUserNotificationState == accepts notifications)
* -> one Edge --app window per monitor (own profile, saver-profile) showing
  /saver?i=<n>&o=portrait|landscape, made borderless + topmost over the whole monitor
* while a games.json game is running it waits game_idle_minutes instead (controller
  play does not count as input to Windows, so we read controllers via XInput)
* any key / mouse / controller input -> every saver Edge process is ended (only those with the
  saver-profile on their command line; the PCDash window has its own profile).

Settings: saver.json (enabled, idle_minutes, slide_seconds). Art: game-art\\store\\<game>\\*.jpg
"""
from __future__ import annotations

import ctypes
import json
import os
import random
import subprocess
import threading
import time
from ctypes import wintypes

import psutil

HERE = os.path.dirname(os.path.abspath(__file__))
ART = os.path.join(HERE, "game-art", "store")
CONF = os.path.join(HERE, "saver.json")
PROFILE = os.path.join(os.environ.get("LOCALAPPDATA", HERE), "PCDash", "saver-profile")
DEFAULTS = {"enabled": True, "idle_minutes": 10, "game_idle_minutes": 30, "slide_seconds": 20}

user32, kernel32, shell32 = ctypes.windll.user32, ctypes.windll.kernel32, ctypes.windll.shell32
GWL_STYLE = -16
WS_CAPTION, WS_THICKFRAME = 0x00C00000, 0x00040000
HWND_TOPMOST = wintypes.HWND(-1)
SWP_FRAMECHANGED, SWP_SHOWWINDOW = 0x0020, 0x0040
user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT]
user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
user32.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_long]

_lock = threading.Lock()
_active = False
_force = threading.Event()


def conf() -> dict:
    c = dict(DEFAULTS)
    try:
        with open(CONF, encoding="utf-8-sig") as fh:
            c.update(json.load(fh))
    except (OSError, ValueError):
        pass
    return c


# ---------- what the page needs ----------

def art_list() -> list[dict]:
    names = {}
    try:
        with open(os.path.join(HERE, "games.json"), encoding="utf-8-sig") as fh:
            names = {g["id"]: g["name"] for g in json.load(fh)["games"]}
    except (OSError, ValueError, KeyError):
        pass
    out = []
    if not os.path.isdir(ART):
        return out
    for game in sorted(os.listdir(ART)):
        d = os.path.join(ART, game)
        if not os.path.isdir(d):
            continue
        for f in sorted(os.listdir(d)):
            stem, ext = os.path.splitext(f.lower())
            if ext not in (".jpg", ".jpeg", ".png", ".webp") or stem == "boxart":
                continue
            out.append({"url": f"/saver/art/{game}/{f}", "game": game,
                        "title": names.get(game, game), "portrait": stem == "poster"})
    return out


# ---------- conditions ----------

class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]


def last_input_tick() -> int:
    li = LASTINPUTINFO(ctypes.sizeof(LASTINPUTINFO), 0)
    user32.GetLastInputInfo(ctypes.byref(li))
    return li.dwTime


def idle_seconds() -> float:
    # Read the input tick FIRST: if a key/mouse event lands between the two calls the
    # old order gave a negative gap that wrapped to ~49 days and fired the saver
    # instantly while someone was actively playing (2026-11-14).
    last = last_input_tick()
    gap = (kernel32.GetTickCount() - last) & 0xFFFFFFFF
    if gap > 0x7FFFFFFF:          # still "negative" -> input just happened
        gap = 0
    return min(gap / 1000.0, time.monotonic() - _pad_last)


# ---------- controllers ----------
# Windows does not count gamepad input as activity (GetLastInputInfo stays put), so
# a controller-only game session looked idle. Poll XInput ourselves.

class _XPAD(ctypes.Structure):
    _fields_ = [("buttons", wintypes.WORD), ("lt", ctypes.c_ubyte), ("rt", ctypes.c_ubyte),
                ("lx", ctypes.c_short), ("ly", ctypes.c_short), ("rx", ctypes.c_short), ("ry", ctypes.c_short)]


class _XSTATE(ctypes.Structure):
    _fields_ = [("packet", wintypes.DWORD), ("pad", _XPAD)]


try:
    _xinput = ctypes.windll.xinput1_4
except OSError:
    _xinput = None
_pad_seen: dict[int, tuple] = {}
_pad_last = -1e9                  # monotonic time of the last real controller input
_DEAD = 9000                      # stick deadzone, so a drifting stick is not "input"


def _q(v: int) -> int:
    return 0 if abs(v) < _DEAD else (1 if v > 0 else -1)


def poll_pads() -> bool:
    """True if any controller had real input (button, trigger, stick past deadzone)
    since the last call. Also updates the idle clock."""
    global _pad_last
    if _xinput is None:
        return False
    hit = False
    st = _XSTATE()
    for i in range(4):
        if _xinput.XInputGetState(i, ctypes.byref(st)) != 0:
            _pad_seen.pop(i, None)
            continue
        p = st.pad
        key = (p.buttons, p.lt > 30, p.rt > 30, _q(p.lx), _q(p.ly), _q(p.rx), _q(p.ry))
        if i in _pad_seen and _pad_seen[i] != key:
            hit = True
        _pad_seen[i] = key
    if hit:
        _pad_last = time.monotonic()
    return hit


def game_running() -> bool:
    """True while any game from games.json is running: it is being played even if
    only a controller is in use, so never cover it with the saver."""
    try:
        with open(os.path.join(HERE, "games.json"), encoding="utf-8-sig") as fh:
            matches = []
            for g in json.load(fh).get("games", []):
                m = g.get("match") or []
                matches += [x.lower() for x in ([m] if isinstance(m, str) else m)]
    except (OSError, ValueError, AttributeError):
        return False
    for pr in psutil.process_iter(["exe"]):
        exe = (pr.info.get("exe") or "").lower()
        if exe and any(x in exe for x in matches):
            return True
    return False


def display_held() -> bool:
    """True while any program asks Windows to keep the display on (video players,
    games, browsers playing video) - the same thing that stops Windows' own saver."""
    v = ctypes.c_ulong()
    ctypes.windll.powrprof.CallNtPowerInformation(16, None, 0, ctypes.byref(v), 4)
    return bool(v.value & 0x2)


def notify_state() -> int:
    st = ctypes.c_int(0)
    shell32.SHQueryUserNotificationState(ctypes.byref(st))
    return st.value


def desktop_free() -> bool:
    """False when locked, a fullscreen game/D3D app or presentation mode is up."""
    st = ctypes.c_int(0)
    if shell32.SHQueryUserNotificationState(ctypes.byref(st)) != 0:
        return False
    return st.value == 5          # QUNS_ACCEPTS_NOTIFICATIONS


# ---------- monitors / windows ----------

class MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]


def monitors() -> list[tuple[int, int, int, int]]:
    rects = []
    proto = ctypes.WINFUNCTYPE(ctypes.c_int, wintypes.HMONITOR, wintypes.HDC, ctypes.POINTER(wintypes.RECT), wintypes.LPARAM)

    def cb(hmon, hdc, lprc, lp):
        mi = MONITORINFO(ctypes.sizeof(MONITORINFO))
        user32.GetMonitorInfoW(hmon, ctypes.byref(mi))
        r = mi.rcMonitor
        rects.append((r.left, r.top, r.right - r.left, r.bottom - r.top))
        return 1
    user32.EnumDisplayMonitors(None, None, proto(cb), 0)
    return rects


def _find_window(title: str):
    found = []
    proto = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    buf = ctypes.create_unicode_buffer(256)

    def cb(h, lp):
        user32.GetWindowTextW(h, buf, 256)
        if buf.value.startswith(title) and user32.IsWindowVisible(h):
            found.append(h)
        return True
    user32.EnumWindows(proto(cb), 0)
    return found[0] if found else None


def _edge() -> str:
    for base in (os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles"), os.environ.get("LOCALAPPDATA")):
        p = os.path.join(base or "", "Microsoft", "Edge", "Application", "msedge.exe")
        if os.path.isfile(p):
            return p
    return "msedge.exe"


def _saver_procs():
    key = PROFILE.lower()
    for p in psutil.process_iter(["name", "cmdline"]):
        try:
            if (p.info["name"] or "").lower() == "msedge.exe" and key in " ".join(p.info["cmdline"] or []).lower():
                yield p
        except (psutil.Error, TypeError):
            continue


def _close_all() -> None:
    procs = list(_saver_procs())
    for p in procs:
        try:
            p.kill()
        except psutil.Error:
            pass
    psutil.wait_procs(procs, timeout=3)


def _open_all(port: int, log) -> None:
    os.makedirs(PROFILE, exist_ok=True)
    seed = random.randrange(1 << 30)
    mons = monitors()
    for i, (x, y, w, h) in enumerate(mons):
        o = "portrait" if h > w else "landscape"
        url = f"http://127.0.0.1:{port}/saver?i={i}&o={o}&seed={seed}"
        # Kiosk = true fullscreen, no frame, and always InPrivate: no Microsoft
        # account sign-in or sync (a normal profile signed itself in on the first try).
        # One profile folder per screen, so each kiosk window is its own browser.
        subprocess.Popen([_edge(), "--kiosk", url, "--edge-kiosk-type=fullscreen",
                          f"--user-data-dir={os.path.join(PROFILE, f's{i}')}", "--no-first-run",
                          "--no-default-browser-check", "--disable-features=Translate",
                          f"--window-position={x},{y}", f"--window-size={w},{h}"],
                         creationflags=0x08000000)
    # Place each window over its monitor, borderless and above everything.
    deadline = time.monotonic() + 15
    placed = set()
    while len(placed) < len(mons) and time.monotonic() < deadline:
        for i, (x, y, w, h) in enumerate(mons):
            if i in placed:
                continue
            hwnd = _find_window(f"GameArt-{i}")
            if hwnd:
                user32.SetWindowPos(hwnd, HWND_TOPMOST, x, y, w, h, SWP_SHOWWINDOW)
                placed.add(i)
        time.sleep(0.2)
    log(f"saver: on, {len(placed)}/{len(mons)} screens")


def start_now() -> None:
    """Tray / API: start right away (ends on the next key or mouse move)."""
    _force.set()


def is_active() -> bool:
    return _active


def run(log, port: int) -> None:
    global _active
    _close_all()                                  # leftovers from a crash
    while True:
        c = conf()
        forced = False
        for _ in range(8):                        # 2 s, polling controllers as we wait
            poll_pads()
            if _force.wait(0.25):
                forced = True
                break
        if not _active:
            # A running game only raises the wait (game_idle_minutes): it may be left
            # open overnight, and controller input already counts as activity.
            need = 60 * float(c.get("idle_minutes", 10))
            idle = idle_seconds()
            game = idle >= need and game_running()
            if game:
                need = max(need, 60 * float(c.get("game_idle_minutes", 30)))
            # A borderless game makes Windows report "busy" (QUNS 2); that is the
            # idle game itself, so it may be covered. A lock still always wins.
            want = forced or (c.get("enabled", True) and idle >= need and not display_held()
                              and (desktop_free() or (game and notify_state() == 2)))
            if not want:
                continue
            _force.clear()
            if forced:
                time.sleep(1.0)                   # let the click that asked for it settle
            mark = last_input_tick()
            with _lock:
                _active = True
                try:
                    _open_all(port, log)
                except Exception as exc:  # noqa: BLE001
                    log(f"saver: failed to open: {exc}")
            mark = last_input_tick() if forced else mark
            # Stay up until any key or mouse input, then close everything.
            while True:
                if last_input_tick() != mark or poll_pads() or not desktop_free_or_fullscreen_ours():
                    break
                time.sleep(0.15)
            with _lock:
                _close_all()
                _active = False
            log("saver: off")
            _force.clear()


def desktop_free_or_fullscreen_ours() -> bool:
    """While our windows are up Windows may report 'busy' (fullscreen); only a lock
    (QUNS_NOT_PRESENT = 1) should end the saver here."""
    st = ctypes.c_int(0)
    shell32.SHQueryUserNotificationState(ctypes.byref(st))
    return st.value != 1
