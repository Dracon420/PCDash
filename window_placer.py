"""Send chosen programs to the MAIN screen when they open (user's request, 2026-11-11).

The Onn (portrait) is the Windows primary display, so everything opens there.
That suits most things, but slicers, CAD and games belong on the main screen.
Windows has no per-app setting for this, so this thread watches for new
top-level windows and moves the ones listed in screens.json, ONCE per window:
drag one back to the Onn and it stays there.

- Maximised windows are re-maximised on the main screen.
- Borderless-fullscreen windows (covering the whole Onn) are resized to cover
  the whole main screen.
- Normal windows keep their size (shrunk to fit if needed) and are centred.
- Hidden, tool and owned (dialog) windows are left alone; dialogs open over
  their parent anyway.
True exclusive-fullscreen games may ignore this and need their own in-game
monitor setting.
"""
from __future__ import annotations

import ctypes
import json
import os
import time
from ctypes import wintypes

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG = os.path.join(HERE, "screens.json")

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT]
user32.GetWindowLongW.restype = ctypes.c_long
user32.MonitorFromWindow.restype = wintypes.HMONITOR
user32.GetWindow.restype = wintypes.HWND
user32.EnumDisplayDevicesW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]

user32.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p
user32.SetThreadDpiAwarenessContext.argtypes = [ctypes.c_void_p]


def _per_monitor_dpi() -> None:
    """Work in real pixels on this thread. The TV runs 4K at 300% scaling while
    the other screens are 100%; without this Windows reports the TV as 1280x720
    and a game "filling" it would be sized to a third of the screen (2026-11-12).
    Per-thread, so it only touches the placer and K-Touch threads."""
    try:
        user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))   # PER_MONITOR_AWARE_V2
    except Exception:  # noqa: BLE001
        pass


SWP_NOZORDER, SWP_NOACTIVATE = 0x4, 0x10
SW_RESTORE, SW_MAXIMIZE, SW_SHOWNOACTIVATE = 9, 3, 4
GWL_EXSTYLE, WS_EX_TOOLWINDOW = -20, 0x80
GW_OWNER = 4
MONITOR_DEFAULTTONEAREST = 2
SETTLE_S = 0.8            # let a new window finish creating/placing itself first


class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long), ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


class MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", RECT), ("rcWork", RECT), ("dwFlags", wintypes.DWORD)]


def _mon_info(hmon) -> MONITORINFO:
    mi = MONITORINFO(); mi.cbSize = ctypes.sizeof(mi)
    user32.GetMonitorInfoW(hmon, ctypes.byref(mi))
    return mi


def monitors() -> list[tuple[int, MONITORINFO]]:
    found = []
    PROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HMONITOR, wintypes.HDC, ctypes.POINTER(RECT), wintypes.LPARAM)

    def cb(h, _dc, _rc, _lp):
        found.append((h, _mon_info(h)))
        return True
    user32.EnumDisplayMonitors(None, None, PROC(cb), 0)
    return found


class MONITORINFOEX(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", RECT), ("rcWork", RECT), ("dwFlags", wintypes.DWORD),
                ("szDevice", wintypes.WCHAR * 32)]


class DISPLAY_DEVICE(ctypes.Structure):
    _fields_ = [("cb", wintypes.DWORD), ("DeviceName", wintypes.WCHAR * 32), ("DeviceString", wintypes.WCHAR * 128),
                ("StateFlags", wintypes.DWORD), ("DeviceID", wintypes.WCHAR * 128), ("DeviceKey", wintypes.WCHAR * 128)]


# The Roku TV's EDID id. The Samsung and the Onn both reach the PC through the
# MX0402 switch, which hands Windows its own EDID for them, so the Samsung is
# found as "the landscape screen that is not the TV" (measured 2026-11-12).
TV_EDID = "RKU0550"
GAME_SCREENS = ("samsung", "tv")
SCREENS = ("samsung", "tv", "onn")
ITEMS = os.path.join(HERE, "games.json")


def _edid_of(hmon) -> str:
    mi = MONITORINFOEX(); mi.cbSize = ctypes.sizeof(mi)
    user32.GetMonitorInfoW(hmon, ctypes.byref(mi))
    dd = DISPLAY_DEVICE(); dd.cb = ctypes.sizeof(dd)
    if user32.EnumDisplayDevicesW(mi.szDevice, 0, ctypes.byref(dd), 1):   # 1 = interface name (has the EDID id)
        return dd.DeviceID
    return ""


def _landscape(mi) -> bool:
    return (mi.rcMonitor.right - mi.rcMonitor.left) > (mi.rcMonitor.bottom - mi.rcMonitor.top)


def screen_monitors() -> dict:
    """{"samsung"|"tv"|"onn": (hmon, mi)} for whichever are connected."""
    _per_monitor_dpi()
    out = {}
    for h, mi in monitors():
        if not _landscape(mi):
            key = "onn"                                 # the portrait Onn
        else:
            key = "tv" if TV_EDID in _edid_of(h).upper() else "samsung"
        area = (mi.rcMonitor.right - mi.rcMonitor.left) * (mi.rcMonitor.bottom - mi.rcMonitor.top)
        if key not in out or area > out[key][2]:
            out[key] = (h, mi, area)
    return {k: (h, mi) for k, (h, mi, _a) in out.items()}


def main_monitor():
    """Where slicers / CAD go: the Samsung, else any landscape screen."""
    s = screen_monitors()
    return s.get("samsung") or s.get("tv")


def game_screen() -> str:
    """The screen games are set to open on: "samsung" or "tv" (screens.json)."""
    try:
        with open(CONFIG, encoding="utf-8-sig") as fh:
            v = json.load(fh).get("game_screen", "samsung")
    except (OSError, ValueError):
        v = "samsung"
    return v if v in GAME_SCREENS else "samsung"


def game_monitor():
    s = screen_monitors()
    return s.get(game_screen()) or s.get("samsung") or s.get("tv")


def _read_cfg() -> dict:
    try:
        with open(CONFIG, encoding="utf-8-sig") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def assignments() -> dict:
    """{item id: screen} as chosen on the K-Touch Screen tab (screens.json "assign")."""
    a = _read_cfg().get("assign") or {}
    return {k: v for k, v in a.items() if v in SCREENS}


def screen_for(item_id: str, kind: str) -> str:
    """Where one game/app goes. Unlisted games follow the all-games choice;
    unlisted apps go to the Samsung. Games never go to the Onn (user, 2026-11-15)."""
    a = assignments()
    if item_id and item_id in a and not (kind == "game" and a[item_id] not in GAME_SCREENS):
        return a[item_id]
    return game_screen() if kind == "game" else "samsung"


def target_for(item_id: str, kind: str):
    s = screen_monitors()
    return s.get(screen_for(item_id, kind)) or s.get("samsung") or s.get("tv")


def set_screen(target: str, which: str, log=lambda m: None) -> str:
    """K-Touch Screen tab: send one item (by id), or "games" (every game, and
    any game not in games.json), to `which`. Saved, and anything of it that is
    open right now is moved there at once."""
    if which not in SCREENS:
        return f"unknown screen {which!r}"
    rules = load_rules()
    j = _read_cfg()
    a = j.setdefault("assign", {})
    if target == "games":
        if which in GAME_SCREENS:
            j["game_screen"] = which
        for it in rules.get("items", []):
            if it["kind"] == "game":
                a[it["id"]] = which
    else:
        a[target] = which
    with open(CONFIG, "w", encoding="utf-8") as fh:
        json.dump(j, fh, indent=2)
    mon = screen_monitors().get(which)
    if not mon:
        log(f"placer: {target} -> {which}, but that screen is not connected")
        return "not connected"
    hmon, mi = mon
    moved = []
    for h in _candidates():
        path = _process_path(h)
        ident = identify(path, rules)
        if not ident:
            continue
        iid, kind = ident
        if not ((target == "games" and kind == "game") or iid == target):
            continue
        has_bars = kind == "game" and borderless_on() and user32.GetWindowLongW(h, GWL_STYLE) & WS_CAPTION
        if user32.MonitorFromWindow(h, MONITOR_DEFAULTTONEAREST) == hmon and not has_bars:
            continue
        how = place_game(h, mi) if kind == "game" else move_to(h, mi)
        moved.append(f"{os.path.basename(path)} ({how})")
    log(f"placer: {target} -> {which}; moved {', '.join(moved) or 'nothing (not open)'}")
    return f"moved {len(moved)}"


def set_game_screen(which: str, log=lambda m: None) -> str:
    """Older K-Touch action (screen:samsung / screen:tv / screen:toggle): all games."""
    if which == "toggle":
        which = "tv" if game_screen() == "samsung" else "samsung"
    return set_screen("games", which, log)


FRAME_HOST = "applicationframehost.exe"


def _class_name(hwnd) -> str:
    buf = ctypes.create_unicode_buffer(128)
    user32.GetClassNameW(hwnd, buf, 128)
    return buf.value


def _process_path(hwnd) -> str:
    """The program a window belongs to. Store apps like the Xbox app draw
    inside an ApplicationFrameWindow owned by ApplicationFrameHost.exe, so
    "xboxpcapp.exe" never matched (2026-11-13); the real app is the child
    window from another process, so report that one instead."""
    path = _exe_of(hwnd)
    if path.lower().endswith(FRAME_HOST) and _class_name(hwnd) == "ApplicationFrameWindow":
        own = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(own))
        hosted = []
        PROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

        def cb(ch, _):
            p = wintypes.DWORD()
            user32.GetWindowThreadProcessId(ch, ctypes.byref(p))
            if p.value != own.value:
                hosted.append(ch)
                return False
            return True
        user32.EnumChildWindows(hwnd, PROC(cb), 0)
        if hosted:
            return _exe_of(hosted[0]) or path
    return path


def _exe_of(hwnd) -> str:
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    h = kernel32.OpenProcess(0x1000, False, pid.value)      # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return ""
    buf = ctypes.create_unicode_buffer(1024)
    size = wintypes.DWORD(1024)
    kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size))
    kernel32.CloseHandle(h)
    return buf.value


def load_rules() -> dict:
    try:
        with open(CONFIG, encoding="utf-8-sig") as fh:
            j = json.load(fh)
    except (OSError, ValueError):
        return {"enabled": False}
    m = j.get("main_screen") or {}
    items = []
    try:
        with open(ITEMS, encoding="utf-8-sig") as fh:
            g = json.load(fh)
        for kind, key in (("game", "games"), ("app", "apps")):
            for it in g.get(key, []):
                if it.get("id"):
                    items.append({"id": it["id"], "kind": kind,
                                  "match": [x.lower() for x in it.get("match", [])]})
    except (OSError, ValueError):
        pass
    return {"enabled": bool(j.get("enabled", True)),
            "exe": {e.lower() for e in m.get("exe", [])},
            "path": [p.lower() for p in m.get("path_contains", [])],
            "except": [p.lower() for p in m.get("except_path_contains", [])],
            "items": items}


def identify(path: str, rules: dict):
    """(item id, "game"|"app") for a window's exe path, or None to leave it be.
    games.json entries first (by "match": a path piece with a backslash, else
    an exe name); then the catch-all game folders / exe list in screens.json,
    which give id "" (follows the all-games / Samsung default)."""
    p = path.lower()
    if not p or not rules.get("enabled") or any(x in p for x in rules["except"]):
        return None
    exe = os.path.basename(p)
    for it in rules.get("items", []):
        for m in it["match"]:
            if (m in p) if (chr(92) in m) else (m == exe):
                return it["id"], it["kind"]
    if any(x in p for x in rules["path"]):
        return "", "game"
    if exe in rules["exe"]:
        return "", "app"
    return None


def _kind(path: str, rules: dict) -> str | None:
    ident = identify(path, rules)
    return ident[1] if ident else None


def wants_main(path: str, rules: dict) -> bool:
    return _kind(path, rules) is not None


def _candidates() -> list[int]:
    out = []
    PROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

    def cb(h, _):
        if (user32.IsWindowVisible(h) and not user32.GetWindow(h, GW_OWNER)
                and not (user32.GetWindowLongW(h, GWL_EXSTYLE) & WS_EX_TOOLWINDOW)
                and user32.GetWindowTextLengthW(h) > 0):
            out.append(h)
        return True
    user32.EnumWindows(PROC(cb), 0)
    return out


GWL_STYLE = -16
WS_FRAME_BITS = 0x00C00000 | 0x00040000 | 0x00080000 | 0x00020000 | 0x00010000   # caption, sizing border, sysmenu, min, max
WS_CAPTION = 0x00C00000
SWP_FRAMECHANGED = 0x20
user32.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_long]


def borderless_on() -> bool:
    try:
        with open(CONFIG, encoding="utf-8-sig") as fh:
            return bool(json.load(fh).get("games_borderless", True))
    except (OSError, ValueError):
        return True


def make_borderless(hwnd, mi) -> str:
    """Turn a game that opened as an ordinary window (title bar, taskbar
    showing) into borderless fullscreen on `mi`'s screen. The user found the
    bars distracting (2026-11-12). Borderless rather than exclusive fullscreen
    on purpose: exclusive can't be moved by the K-Touch Game Screen tile and
    minimises whenever the Onn dashboard is touched."""
    if user32.IsZoomed(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)
    style = user32.GetWindowLongW(hwnd, GWL_STYLE)
    user32.SetWindowLongW(hwnd, GWL_STYLE, style & ~WS_FRAME_BITS)
    m = mi.rcMonitor
    user32.SetWindowPos(hwnd, None, m.left, m.top, m.right - m.left, m.bottom - m.top,
                        SWP_NOZORDER | SWP_FRAMECHANGED)
    return "borderless fullscreen"


def place_game(hwnd, mi) -> str:
    """Games: windowed ones become borderless fullscreen; the rest just move."""
    if borderless_on() and user32.GetWindowLongW(hwnd, GWL_STYLE) & WS_CAPTION:
        return make_borderless(hwnd, mi)
    return move_to(hwnd, mi)


def move_to(hwnd, mi: MONITORINFO) -> str:
    """Move one window onto the monitor described by `mi`. Returns how."""
    cur = _mon_info(user32.MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST))
    r = RECT(); user32.GetWindowRect(hwnd, ctypes.byref(r))
    w, h = r.right - r.left, r.bottom - r.top
    cm, tw, tm = cur.rcMonitor, mi.rcWork, mi.rcMonitor
    flags = SWP_NOZORDER | SWP_NOACTIVATE
    if user32.IsZoomed(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)
        user32.SetWindowPos(hwnd, None, tw.left + 40, tw.top + 40, min(w, tw.right - tw.left - 80),
                            min(h, tw.bottom - tw.top - 80), flags)
        user32.ShowWindow(hwnd, SW_MAXIMIZE)
        return "maximised"
    if (r.left, r.top, r.right, r.bottom) == (cm.left, cm.top, cm.right, cm.bottom):
        user32.SetWindowPos(hwnd, None, tm.left, tm.top, tm.right - tm.left, tm.bottom - tm.top, flags)
        return "fullscreen"
    w, h = min(w, tw.right - tw.left), min(h, tw.bottom - tw.top)
    x = tw.left + (tw.right - tw.left - w) // 2
    y = tw.top + (tw.bottom - tw.top - h) // 2
    user32.SetWindowPos(hwnd, None, x, y, w, h, flags)
    return "centred"


def live_list() -> list[int]:
    return list(_candidates())


def run(log=lambda m: None) -> None:
    _per_monitor_dpi()
    rules = load_rules()
    if not rules.get("enabled"):
        log("placer: off (screens.json)")
        return
    seen: dict[int, float] = {}          # hwnd -> first seen (monotonic); -1 = done
    # Windows already open when PCDash starts are left where they are.
    for h in _candidates():
        seen[h] = -1
    log(f"placer: watching {len(rules['items'])} games/apps + {len(rules['path'])} game folders")
    rules_at = time.monotonic()
    screens_at, screens, back_at = 0.0, set(screen_monitors()), 0.0
    while True:
        try:
            now = time.monotonic()
            # A screen switched off (TV at bedtime) makes Windows push its windows
            # onto another screen. When a screen comes back, send games/apps that
            # belong there back to it (2026-11-15).
            if now - screens_at > 2:
                screens_at = now
                cur = set(screen_monitors())
                if cur - screens:
                    log(f"placer: screen back: {', '.join(sorted(cur - screens))}")
                    back_at = now + 4             # let Windows finish rearranging first
                screens = cur
                # Games must never sit on the Onn: if Windows pushed one there (a
                # screen dropped out), move it off at once (user, 2026-11-15).
                onn = cur and screen_monitors().get("onn")
                if onn and not back_at:
                    for h in live_list():
                        if user32.MonitorFromWindow(h, MONITOR_DEFAULTTONEAREST) != onn[0]:
                            continue
                        ident = identify(_process_path(h), rules)
                        if ident and ident[1] == "game":
                            target = target_for(*ident)
                            if target and target[0] != onn[0]:
                                how = place_game(h, target[1])
                                log(f"placer: {os.path.basename(_process_path(h))} off the Onn -> {screen_for(*ident)} ({how})")
            if back_at and now >= back_at:
                back_at = 0.0
                for h in live_list():
                    ident = identify(_process_path(h), rules)
                    if not ident:
                        continue
                    target = target_for(*ident)
                    if target and user32.MonitorFromWindow(h, MONITOR_DEFAULTTONEAREST) != target[0]:
                        how = place_game(h, target[1]) if ident[1] == "game" else move_to(h, target[1])
                        log(f"placer: {os.path.basename(_process_path(h))} back -> {screen_for(*ident)} ({how})")
            if now - rules_at > 5:              # pick up games.json edits without a restart
                rules, rules_at = load_rules(), now
            live = set(_candidates())
            for h in list(seen):
                # Forget hidden windows too, not only destroyed ones: apps that
                # "close" to the tray (the Xbox app) come back with the same
                # window, which counted as already placed and stayed on the Onn.
                if h not in live:
                    seen.pop(h, None)
            targets: dict = {}
            for h in live:
                t = seen.get(h)
                if t == -1:
                    continue
                if t is None:
                    seen[h] = now
                    continue
                if now - t < SETTLE_S:
                    continue
                path = _process_path(h)
                if path.lower().endswith(FRAME_HOST) and now - t < 10:
                    continue      # a Store app's frame shows before its app attaches - look again
                seen[h] = -1
                ident = identify(path, rules)
                if not ident:
                    continue
                iid, kind = ident
                if ident not in targets:
                    targets[ident] = target_for(iid, kind)
                target = targets[ident]
                if not target:
                    continue
                hmon, mi = target
                has_bars = (kind == "game" and borderless_on()
                            and user32.GetWindowLongW(h, GWL_STYLE) & WS_CAPTION)
                if user32.MonitorFromWindow(h, MONITOR_DEFAULTTONEAREST) == hmon and not has_bars:
                    continue                     # already there, nothing to fix
                how = place_game(h, mi) if kind == "game" else move_to(h, mi)
                where = screen_for(iid, kind)
                log(f"placer: {os.path.basename(path)} -> {where} ({how})")
        except Exception as exc:  # noqa: BLE001 - never take PCDash down
            log(f"placer: {exc}")
            time.sleep(5)
        time.sleep(0.4)
