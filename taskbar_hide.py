"""Keep Discord's and ArkPanel's taskbar buttons off the taskbar (user, 2026-11-11).

Uses the shell's ITaskbarList::DeleteTab, which removes only the BUTTON. The
window itself is never hidden, restyled or ShowWindow()-ed, which matters for
Discord: forcing its window state from outside leaves a dead, stretched frame
(see onn_view.py). Explorer re-adds a button whenever a window is shown again
(Discord coming back from its tray, say), so this re-applies every 2 s.

Discord keeps its own tray icon. ArkPanel's desktop app has none, so the PCDash
tray menu gets "Show ArkPanel" (show_arkpanel below). On quit, restore() puts the
buttons back so nothing is left unreachable.
"""
from __future__ import annotations

import ctypes
import threading
import time
from ctypes import wintypes

import onn_view

ole32 = ctypes.WinDLL("ole32")
user32 = ctypes.WinDLL("user32", use_last_error=True)


class GUID(ctypes.Structure):
    _fields_ = [("a", ctypes.c_uint32), ("b", ctypes.c_uint16), ("c", ctypes.c_uint16), ("d", ctypes.c_ubyte * 8)]


def _guid(s: str) -> GUID:
    s = s.strip("{}").replace("-", "")
    return GUID(int(s[0:8], 16), int(s[8:12], 16), int(s[12:16], 16),
                (ctypes.c_ubyte * 8)(*bytes.fromhex(s[16:])))


CLSID_TaskbarList = _guid("56FDF344-FD6D-11d0-958A-006097C9A090")
IID_ITaskbarList = _guid("56FDF342-FD6D-11d0-958A-006097C9A090")
_HWND_FN = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, wintypes.HWND)
_VOID_FN = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p)


class _Taskbar:
    """Minimal ITaskbarList (vtable: QI, AddRef, Release, HrInit, AddTab, DeleteTab)."""

    def __init__(self):
        ole32.CoInitialize(None)
        self.p = ctypes.c_void_p()
        hr = ole32.CoCreateInstance(ctypes.byref(CLSID_TaskbarList), None, 1,   # CLSCTX_INPROC_SERVER
                                    ctypes.byref(IID_ITaskbarList), ctypes.byref(self.p))
        if hr != 0:
            raise OSError(f"TaskbarList unavailable (0x{hr & 0xFFFFFFFF:08X})")
        self.vt = ctypes.cast(ctypes.cast(self.p, ctypes.POINTER(ctypes.c_void_p))[0],
                              ctypes.POINTER(ctypes.c_void_p))
        _VOID_FN(self.vt[3])(self.p)                     # HrInit

    def add(self, hwnd):
        _HWND_FN(self.vt[4])(self.p, hwnd)

    def delete(self, hwnd):
        _HWND_FN(self.vt[5])(self.p, hwnd)


def arkpanel_window():
    hits = onn_view._windows(lambda h, t, c: t.startswith("ArkPanel") and user32.IsWindowVisible(h)
                             and not c.startswith("Chrome_WidgetWin"))   # not the Onn's own page
    return hits[0] if hits else None


def targets() -> list[int]:
    out = []
    d = onn_view.discord_window()
    if d and user32.IsWindowVisible(d):
        out.append(d)
    a = arkpanel_window()
    if a:
        out.append(a)
    return out


_hidden: set[int] = set()
_lock = threading.Lock()


def run(log=lambda m: None) -> None:
    try:
        tb = _Taskbar()
    except OSError as exc:
        log(f"taskbar: {exc}")
        return
    run.tb = tb
    log("taskbar: hiding Discord and ArkPanel buttons")
    while True:
        try:
            with _lock:
                for h in targets():
                    tb.delete(h)
                    _hidden.add(h)
            _fit_discord(log)
        except Exception as exc:  # noqa: BLE001 - never take PCDash down
            log(f"taskbar: {exc}")
        time.sleep(2)


def _fit_discord(log) -> None:
    """Keep a SHOWING Discord window filling the Onn in portrait.

    At sign-in Discord restores its last saved size (1296x728 landscape, half
    off the Onn's left edge, seen 2026-11-11 after a reboot) and only got docked
    when someone swiped to the Discord view. Move/size only: no show, no
    activate, no z-order change, so it never steals focus or pops in front, and
    a hidden-to-tray Discord is left alone (never ShowWindow it from outside)."""
    h = onn_view.discord_window()
    if not h or not user32.IsWindowVisible(h) or user32.IsIconic(h):
        return
    x, y, w, hgt = onn_view.onn_area()
    r = onn_view.RECT()
    user32.GetWindowRect(h, ctypes.byref(r))
    if (r.left, r.top, r.right - r.left, r.bottom - r.top) == (x, y, w, hgt):
        return
    if user32.IsZoomed(h):
        return            # maximised = placed on purpose; and no ShowWindow on Discord from outside
    user32.SetWindowPos(h, None, x, y, w, hgt, 0x0010 | 0x0004)   # SWP_NOACTIVATE | SWP_NOZORDER
    log(f"taskbar: fitted Discord to the Onn ({r.right - r.left}x{r.bottom - r.top} -> {w}x{hgt})")


def restore() -> None:
    """Give the buttons back (PCDash quitting)."""
    if not getattr(run, "tb", None):
        return
    try:
        tb = _Taskbar()            # this thread's own COM object
    except OSError:
        return
    with _lock:
        for h in list(_hidden):
            if user32.IsWindow(h):
                try:
                    tb.add(h)
                except Exception:  # noqa: BLE001
                    pass


ARKPANEL_APP = r"D:\Servers\ArkPanel\ArkPanel.py"


def show_arkpanel() -> bool:
    """Bring the ArkPanel desktop app to the front, or start it if it isn't running.

    Used by the tray menu and the dashboard's ArkPanel button (which used to open
    the web version in the default browser). A background process may not take
    the foreground, so lift it with the topmost trick as well."""
    import os
    import subprocess
    import sys
    h = arkpanel_window()
    if h:
        if user32.IsIconic(h):
            user32.ShowWindow(h, 9)                      # SW_RESTORE
        onn_view._raise(h)
        user32.SetForegroundWindow(h)
        return True
    if not os.path.isfile(ARKPANEL_APP):
        return False
    pyw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    subprocess.Popen([pyw, ARKPANEL_APP], cwd=os.path.dirname(ARKPANEL_APP), creationflags=0x08000000)
    return True
