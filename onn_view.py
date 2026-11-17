"""What the Onn (portrait) monitor shows - switched by KNOMI left/right swipes.

Views, in swipe order:
    split    Stream Control on top, PC Dashboard below   (the PCDash window)
    dash     PC Dashboard only                            (PCDash window, page re-laid out)
    stream   Stream Control only                          (PCDash window, page re-laid out)
    ark      ArkPanel (:8090) full screen                 (PCDash window, an iframe signed in via /desktop/open)
    discord  Discord, docked to the Onn and brought to the front

Windows are raised WITHOUT taking focus (SWP_NOACTIVATE / SW_SHOWNOACTIVATE),
so switching never pulls you out of a game on the main monitor. Discord stays
docked on the Onn underneath PCDash when another view is in front.

Also: volume up/down via the media volume keys (Windows shows its volume pop-up).
"""
from __future__ import annotations

import ctypes
import glob
import os
import subprocess
import threading
import time
from ctypes import wintypes

VIEWS = ["split", "dash", "stream", "ark", "discord"]
VIEW_NAMES = {"split": "Stream + Dashboard", "dash": "Dashboard", "stream": "Stream Control", "ark": "ArkPanel", "discord": "Discord"}
STATE = {"view": "split", "changed": time.time()}
_lock = threading.Lock()
CHANGED = threading.Condition()      # the Onn page long-polls on this


def wait_change(since: float, timeout: float = 25) -> None:
    with CHANGED:
        CHANGED.wait_for(lambda: STATE["changed"] > since, timeout)

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
psapi = ctypes.WinDLL("psapi", use_last_error=True)
user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT]
HWND_TOP = wintypes.HWND(0)
SWP_NOACTIVATE, SWP_SHOWWINDOW, SWP_NOMOVE, SWP_NOSIZE = 0x10, 0x40, 0x2, 0x1
SW_SHOWNOACTIVATE, SW_RESTORE = 4, 9


class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long), ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


class MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", RECT), ("rcWork", RECT), ("dwFlags", wintypes.DWORD)]


def onn_area() -> tuple[int, int, int, int]:
    found = []
    PROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HMONITOR, wintypes.HDC, ctypes.POINTER(RECT), wintypes.LPARAM)

    def cb(h, _dc, _rc, _lp):
        mi = MONITORINFO(); mi.cbSize = ctypes.sizeof(mi)
        user32.GetMonitorInfoW(h, ctypes.byref(mi))
        m, w = mi.rcMonitor, mi.rcWork
        found.append(((w.left, w.top, w.right - w.left, w.bottom - w.top), (m.bottom - m.top) > (m.right - m.left)))
        return True
    user32.EnumDisplayMonitors(None, None, PROC(cb), 0)
    return next((a for a, portrait in found if portrait), found[0][0])


def _process_name(hwnd) -> str:
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    h = kernel32.OpenProcess(0x1000, False, pid.value)          # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return ""
    buf = ctypes.create_unicode_buffer(260)
    size = wintypes.DWORD(260)
    kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size))
    kernel32.CloseHandle(h)
    return os.path.basename(buf.value).lower()


def _windows(match) -> list[int]:
    hits = []
    PROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

    def cb(h, _):
        t = ctypes.create_unicode_buffer(128); user32.GetWindowTextW(h, t, 128)
        c = ctypes.create_unicode_buffer(64); user32.GetClassNameW(h, c, 64)
        if match(h, t.value, c.value):
            hits.append(h)
        return True
    user32.EnumWindows(PROC(cb), 0)
    return hits


def pcdash_window():
    hits = _windows(lambda h, t, c: t == "PCDash" and c.startswith("Chrome_WidgetWin") and user32.IsWindowVisible(h))
    return hits[0] if hits else None


def discord_window():
    hits = _windows(lambda h, t, c: c == "Chrome_WidgetWin_1" and t.endswith("Discord") and _process_name(h) == "discord.exe")
    return hits[0] if hits else None


def _start_discord() -> None:
    for pattern in (r"C:\ProgramData\*\Discord\Update.exe", os.path.expandvars(r"%LOCALAPPDATA%\Discord\Update.exe")):
        for exe in glob.glob(pattern):
            subprocess.Popen([exe, "--processStart", "Discord.exe"], creationflags=0x08000000)
            return


HWND_TOPMOST, HWND_NOTOPMOST, HWND_BOTTOM = wintypes.HWND(-1), wintypes.HWND(-2), wintypes.HWND(1)


# Windows this module ever ShowWindow()-ed itself (Discord must never be one).
_forced: dict[int, bool] = {}


def _raise(hwnd, area=None) -> None:
    """Bring to the front without stealing focus; optionally dock to `area`.

    A plain HWND_TOP from a background process is ignored by Windows (seen
    2026-11-11), so flip the window to topmost and straight back: that lifts it
    above everything without activating it."""
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)
    flags = SWP_NOACTIVATE        # no SWP_SHOWWINDOW: only move/stack windows that are already shown
    if area:
        x, y, w, h = area
        user32.SetWindowPos(hwnd, HWND_TOPMOST, x, y, w, h, flags)
    else:
        user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, flags | SWP_NOMOVE | SWP_NOSIZE)
    user32.SetWindowPos(hwnd, HWND_NOTOPMOST, 0, 0, 0, 0, flags | SWP_NOMOVE | SWP_NOSIZE)


def _sink(hwnd) -> None:
    """Send to the bottom of the stack (stays docked on the Onn, just behind)."""
    user32.SetWindowPos(hwnd, HWND_BOTTOM, 0, 0, 0, 0, SWP_NOACTIVATE | SWP_NOMOVE | SWP_NOSIZE)


_last_pc_view = {"view": "split"}      # the PCDash view to go back to after Discord


def _publish(view: str) -> None:
    with _lock:
        STATE["view"], STATE["changed"] = view, time.time()
        if view != "discord":
            _last_pc_view["view"] = view
    with CHANGED:
        CHANGED.notify_all()


user32.GetTopWindow.restype = wintypes.HWND
user32.GetWindow.restype = wintypes.HWND
user32.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]


def _on_onn(hwnd) -> bool:
    r = RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(r)):
        return False
    x, y, w, h = onn_area()
    cx, cy = (r.left + r.right) // 2, (r.top + r.bottom) // 2
    return x <= cx < x + w and y <= cy < y + h


def _front(a, b):
    """Which of two windows sits higher in the z-order (None if neither found)."""
    h = user32.GetTopWindow(None)
    for _ in range(2000):
        if not h:
            return None
        if h == a:
            return a
        if h == b:
            return b
        h = user32.GetWindow(h, 2)            # GW_HWNDNEXT
    return None


def track(log=lambda m: None) -> None:
    """Keep STATE matching what the Onn really shows (2026-11-13).

    Discord can end up in front without PCDash doing it - launching a game
    from the Xbox app did. STATE still said "split", so the next swipe
    stepped split -> dash and the Onn looked like it switched by itself.
    This only corrects STATE; it never moves or raises a window."""
    while True:
        time.sleep(1.5)
        try:
            d, p = discord_window(), pcdash_window()
            if not (d and p and user32.IsWindowVisible(d)) or user32.IsIconic(d) \
                    or not (_on_onn(d) and _on_onn(p)):
                continue
            top = _front(d, p)
            cur = STATE["view"]
            if top == d and cur != "discord":
                _publish("discord")
                log(f"onn: Discord came to the front on its own - view is now discord (was {cur})")
            elif top == p and cur == "discord":
                back = _last_pc_view["view"]
                _publish(back)
                log(f"onn: PCDash came to the front on its own - view is now {back}")
        except Exception as exc:  # noqa: BLE001 - never take PCDash down
            log(f"onn: tracker: {exc}")
            time.sleep(10)


def set_view(view: str, log=lambda m: None, source: str = "") -> str:
    if view not in VIEWS:
        return STATE["view"]
    _publish(view)
    if view == "discord":
        hwnd = discord_window()
        if not (hwnd and user32.IsWindowVisible(hwnd)) or _forced.get(hwnd):
            # Discord is in its tray (or was force-shown by an older version of
            # this code). Never ShowWindow it from outside: Electron still thinks
            # it is hidden, never repaints, and Windows just stretches its last
            # frame - a dead, zoomed-in picture (seen 2026-11-11). Ask Discord to
            # show itself instead (same as clicking its tray icon). That takes
            # focus once; after it is open, switching never does.
            if hwnd and user32.IsWindowVisible(hwnd):
                user32.ShowWindow(hwnd, 0)                    # SW_HIDE: back in sync with Electron
                time.sleep(0.4)
            _forced.pop(hwnd, None)
            _start_discord()
            hwnd = None
            for _ in range(60):
                time.sleep(0.25)
                h = discord_window()
                if h and user32.IsWindowVisible(h):
                    hwnd = h
                    break
            time.sleep(0.5)                                   # let it finish its own show/restore
        if hwnd:
            _raise(hwnd, onn_area())
        else:
            log("onn: Discord window not found")
    else:
        # The PCDash page polls /api/onn/view and re-lays itself out.
        d = discord_window()
        if d and user32.IsWindowVisible(d):
            _sink(d)
        hwnd = pcdash_window()
        if hwnd:
            _raise(hwnd)
    log(f"onn: view -> {view}" + (f" ({source})" if source else ""))
    return view


def step_view(delta: int, log=lambda m: None, source: str = "") -> str:
    i = VIEWS.index(STATE["view"]) if STATE["view"] in VIEWS else 0
    return set_view(VIEWS[(i + delta) % len(VIEWS)], log, source)


# ------------------------------------------------------------------ volume --
VK_VOLUME_DOWN, VK_VOLUME_UP = 0xAE, 0xAF
KEYEVENTF_KEYUP = 0x2


def volume(direction: int, steps: int = 2) -> None:
    """Each media-key press is 2% of Windows' master volume; one swipe = 4%."""
    vk = VK_VOLUME_UP if direction > 0 else VK_VOLUME_DOWN
    for _ in range(steps):
        user32.keybd_event(vk, 0, 0, 0)
        user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)
