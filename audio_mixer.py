"""Per-app volume and mute - the Windows volume mixer, for the K-Touch PC tab.

Talks to Windows Core Audio directly through ctypes (no pycaw/comtypes, so
nothing to install). Every active playback device is read, and each app's
sessions are grouped by program, so "Discord" is one row even though Discord
plays through several processes and sometimes a second device.

    snapshot()          -> [{"id", "name", "vol", "mute"}, ...]  (master first)
    set_volume(id, pct) -> sets that app (or "master") to pct 0-100
    toggle_mute(id)

id is the program's exe name in lower case (e.g. "discord.exe"), or "master".
"""
from __future__ import annotations

import ctypes
import os
import threading
import time
from ctypes import wintypes

ole32 = ctypes.WinDLL("ole32")
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
HRESULT = ctypes.c_long


class GUID(ctypes.Structure):
    _fields_ = [("a", ctypes.c_uint32), ("b", ctypes.c_uint16), ("c", ctypes.c_uint16), ("d", ctypes.c_ubyte * 8)]

    def __init__(self, s: str):
        super().__init__()
        ole32.CLSIDFromString(ctypes.c_wchar_p("{" + s + "}"), ctypes.byref(self))


CLSID_MMDeviceEnumerator = GUID("BCDE0395-E52F-467C-8E3D-C4579291692E")
IID_IMMDeviceEnumerator = GUID("A95664D2-9614-4F35-A746-DE8DB63617E6")
IID_IAudioSessionManager2 = GUID("77AA99A0-1BD6-484F-8BC7-2C654C9A9B6F")
IID_IAudioSessionControl2 = GUID("BFB7FF88-7239-4FC9-8FA2-07C950BE9C6D")
IID_ISimpleAudioVolume = GUID("87CE5498-68D6-44E5-9215-6DA47EF883D8")
IID_IAudioEndpointVolume = GUID("5CDF2C82-841E-4546-9722-0CF74078229A")
CLSCTX_ALL = 0x17
eRender, eConsole, DEVICE_STATE_ACTIVE = 0, 0, 1
AudioSessionStateExpired = 2


def _call(ptr, index: int, *args, argtypes=()):
    """Call method `index` of a COM interface pointer's vtable."""
    vtbl = ctypes.cast(ptr, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    fn = ctypes.WINFUNCTYPE(HRESULT, ctypes.c_void_p, *argtypes)(vtbl[index])
    hr = fn(ptr, *args)
    if hr < 0:
        raise OSError(f"COM call {index} failed: 0x{hr & 0xFFFFFFFF:08X}")
    return hr


def _release(ptr) -> None:
    if ptr:
        vtbl = ctypes.cast(ptr, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(vtbl[2])(ptr)


def _qi(ptr, iid: GUID):
    out = ctypes.c_void_p()
    _call(ptr, 0, ctypes.byref(iid), ctypes.byref(out), argtypes=(ctypes.c_void_p, ctypes.c_void_p))
    return out


def _coinit() -> None:
    ole32.CoInitializeEx(None, 0)       # COINIT_MULTITHREADED; harmless if already set up


PV = ctypes.c_void_p
_VP = (ctypes.c_void_p,)


def _devices() -> list:
    """Every active playback device (IMMDevice pointers; caller releases)."""
    enum = ctypes.c_void_p()
    hr = ole32.CoCreateInstance(ctypes.byref(CLSID_MMDeviceEnumerator), None, CLSCTX_ALL,
                                ctypes.byref(IID_IMMDeviceEnumerator), ctypes.byref(enum))
    if hr < 0:
        raise OSError(f"CoCreateInstance: 0x{hr & 0xFFFFFFFF:08X}")
    coll = ctypes.c_void_p()
    try:
        _call(enum, 3, eRender, DEVICE_STATE_ACTIVE, ctypes.byref(coll),
              argtypes=(ctypes.c_int, wintypes.DWORD, PV))
        n = wintypes.UINT()
        _call(coll, 3, ctypes.byref(n), argtypes=_VP)
        out = []
        for i in range(n.value):
            dev = ctypes.c_void_p()
            _call(coll, 4, i, ctypes.byref(dev), argtypes=(wintypes.UINT, PV))
            out.append(dev)
        return out
    finally:
        _release(coll)
        _release(enum)


def _default_device():
    enum = ctypes.c_void_p()
    hr = ole32.CoCreateInstance(ctypes.byref(CLSID_MMDeviceEnumerator), None, CLSCTX_ALL,
                                ctypes.byref(IID_IMMDeviceEnumerator), ctypes.byref(enum))
    if hr < 0:
        raise OSError(f"CoCreateInstance: 0x{hr & 0xFFFFFFFF:08X}")
    try:
        dev = ctypes.c_void_p()
        _call(enum, 4, eRender, eConsole, ctypes.byref(dev), argtypes=(ctypes.c_int, ctypes.c_int, PV))
        return dev
    finally:
        _release(enum)


def _activate(dev, iid: GUID):
    out = ctypes.c_void_p()
    _call(dev, 3, ctypes.byref(iid), CLSCTX_ALL, None, ctypes.byref(out),
          argtypes=(PV, wintypes.DWORD, PV, PV))
    return out


def _exe_path(pid: int) -> str:
    h = kernel32.OpenProcess(0x1000, False, pid)          # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(520)
        size = wintypes.DWORD(520)
        kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size))
        return buf.value
    finally:
        kernel32.CloseHandle(h)


def _sessions() -> list[tuple[str, str, ctypes.c_void_p, str]]:
    """(exe id, exe path, ISimpleAudioVolume, session instance id) for every
    live app session.
    Caller releases the volume pointers."""
    out = []
    for dev in _devices():
        mgr = enum = None
        try:
            mgr = _activate(dev, IID_IAudioSessionManager2)
            enum = ctypes.c_void_p()
            _call(mgr, 5, ctypes.byref(enum), argtypes=_VP)
            n = ctypes.c_int()
            _call(enum, 3, ctypes.byref(n), argtypes=_VP)
            for i in range(n.value):
                ctl = ctypes.c_void_p()
                _call(enum, 4, i, ctypes.byref(ctl), argtypes=(ctypes.c_int, PV))
                ctl2 = None
                try:
                    ctl2 = _qi(ctl, IID_IAudioSessionControl2)
                    state = ctypes.c_int()
                    _call(ctl2, 3, ctypes.byref(state), argtypes=_VP)
                    pid = wintypes.DWORD()
                    _call(ctl2, 14, ctypes.byref(pid), argtypes=_VP)
                    if state.value == AudioSessionStateExpired or not pid.value:
                        continue                       # gone, or the "System sounds" session
                    path = _exe_path(pid.value)
                    if not path:
                        continue
                    inst = ctypes.c_wchar_p()
                    _call(ctl2, 13, ctypes.byref(inst), argtypes=_VP)
                    iid = inst.value or ""
                    ole32.CoTaskMemFree(inst)
                    out.append((os.path.basename(path).lower(), path, _qi(ctl, IID_ISimpleAudioVolume), iid))
                except OSError:
                    continue
                finally:
                    _release(ctl2)
                    _release(ctl)
        except OSError:
            pass
        finally:
            _release(enum)
            _release(mgr)
            _release(dev)
    return out


# Friendly names. Games are named from games.json ("match" is part of their path).
NAMES = {"discord.exe": "Discord", "chrome.exe": "Chrome", "msedge.exe": "Edge", "firefox.exe": "Firefox",
         "spotify.exe": "Spotify", "obs64.exe": "OBS", "xboxpcapp.exe": "Xbox app", "steam.exe": "Steam",
         "steamwebhelper.exe": "Steam", "vlc.exe": "VLC", "plex.exe": "Plex", "explorer.exe": "Windows"}


def _games() -> list[tuple[str, str]]:
    try:
        import json
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "games.json"), encoding="utf-8-sig") as fh:
            j = json.load(fh)
        out = []
        for g in j.get("games", []) + j.get("apps", []):
            m = g.get("match") or []
            for one in ([m] if isinstance(m, str) else m):   # "match" may be one string or a list
                out.append((one.lower(), g["name"]))
        return out
    except (OSError, ValueError, KeyError, AttributeError):
        return []


def _name(exe: str, path: str, games) -> str:
    low = path.lower()
    for match, name in games:
        if match in low:
            return name
    if exe in NAMES:
        return NAMES[exe]
    base = exe[:-4] if exe.endswith(".exe") else exe
    for tail in ("-win64-shipping", "-wingdk-shipping", "-shipping", "_x64", "64"):
        if base.endswith(tail) and len(base) > len(tail):
            base = base[: -len(tail)]
            break
    return base[:1].upper() + base[1:]


def _master():
    dev = _default_device()
    try:
        return _activate(dev, IID_IAudioEndpointVolume)
    finally:
        _release(dev)


_lock = threading.Lock()
_cache: tuple[float, list] = (0.0, [])


def snapshot(max_age: float = 1.5) -> list[dict]:
    global _cache
    with _lock:
        if time.monotonic() - _cache[0] < max_age:
            return _cache[1]
        _coinit()
        rows: dict[str, dict] = {}
        games = _games()
        try:
            ep = _master()
            try:
                v, m = ctypes.c_float(), wintypes.BOOL()
                _call(ep, 9, ctypes.byref(v), argtypes=_VP)
                _call(ep, 15, ctypes.byref(m), argtypes=_VP)
                rows["master"] = {"id": "master", "name": "Master", "vol": round(v.value * 100), "mute": bool(m.value)}
            finally:
                _release(ep)
        except OSError:
            pass
        for exe, path, vol, _inst in _sessions():
            try:
                v, m = ctypes.c_float(), wintypes.BOOL()
                _call(vol, 4, ctypes.byref(v), argtypes=_VP)
                _call(vol, 6, ctypes.byref(m), argtypes=_VP)
                r = rows.get(exe)
                if r is None:
                    rows[exe] = {"id": exe, "name": _name(exe, path, games), "vol": round(v.value * 100), "mute": bool(m.value)}
                else:                                    # several sessions: loudest wins, muted only if all are
                    r["vol"] = max(r["vol"], round(v.value * 100))
                    r["mute"] = r["mute"] and bool(m.value)
            except OSError:
                pass
            finally:
                _release(vol)
        # Apps with a saved setting (and Discord) stay listed while silent, showing
        # what will be applied the moment they next make a sound (2026-11-14).
        wishes = _wishes()
        for exe in set(wishes) | PINNED:
            if exe not in rows:
                w = wishes.get(exe, {})
                rows[exe] = {"id": exe, "name": w.get("name") or NAMES.get(exe) or exe,
                             "vol": w.get("vol", 100), "mute": w.get("mute", False), "idle": True}
        out = list(rows.values())
        out.sort(key=lambda r: (r["id"] != "master", r["name"].lower()))
        _cache = (time.monotonic(), out)
        return out


def _apply(app_id: str, volume: float | None = None, mute: bool | None = None) -> bool:
    global _cache
    with _lock:
        _coinit()
        done = False
        if app_id == "master":
            ep = _master()
            try:
                if volume is not None:
                    _call(ep, 7, ctypes.c_float(volume), None, argtypes=(ctypes.c_float, PV))
                if mute is not None:
                    _call(ep, 14, wintypes.BOOL(mute), None, argtypes=(wintypes.BOOL, PV))
                done = True
            finally:
                _release(ep)
        else:
            for exe, _path, vol, _inst in _sessions():
                try:
                    if exe == app_id:
                        if volume is not None:
                            _call(vol, 3, ctypes.c_float(volume), None, argtypes=(ctypes.c_float, PV))
                        if mute is not None:
                            _call(vol, 5, wintypes.BOOL(mute), None, argtypes=(wintypes.BOOL, PV))
                        done = True
                except OSError:
                    pass
                finally:
                    _release(vol)
        _cache = (0.0, [])                               # next snapshot reads it fresh
        return done


def set_volume(app_id: str, pct: int) -> bool:
    pct = max(0, min(100, int(pct)))
    _remember(app_id, vol=pct)
    return _apply(app_id, volume=pct / 100.0) or app_id != "master"


def toggle_mute(app_id: str) -> bool:
    cur = next((r for r in snapshot(0) if r["id"] == app_id), None)
    if not cur:
        return False
    _remember(app_id, mute=not cur["mute"])
    _apply(app_id, mute=not cur["mute"])
    return True


# ------------------------------------------------------ remembered settings --
# Discord opens a brand-new audio session for every notification ping and drops
# it a few seconds later, so it was missing from the mixer most of the time and
# a volume set on one ping never reached the next (2026-11-14). Whatever is set
# here is saved in mixer.json and put on every NEW session of that app as soon as
# it appears (watch() checks every 0.3 s). Sessions already seen are left alone,
# so a change made in Windows' own mixer isn't fought.
PINNED = {"discord.exe"}
WISH_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mixer.json")
_wish_lock = threading.Lock()


def _wishes() -> dict:
    try:
        import json
        with open(WISH_PATH, encoding="utf-8-sig") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def _remember(app_id: str, **kw) -> None:
    if app_id == "master":
        return
    import json
    with _wish_lock:
        w = _wishes()
        cur = w.setdefault(app_id, {})
        cur.update(kw)
        if "name" not in cur:
            cur["name"] = next((r["name"] for r in _cache[1] if r["id"] == app_id), NAMES.get(app_id, app_id))
        with open(WISH_PATH, "w", encoding="utf-8") as fh:
            json.dump(w, fh, indent=2)


def watch(log=lambda m: None) -> None:
    seen: set[str] = set()
    _coinit()
    while True:
        try:
            wishes = _wishes()
            now_ids = set()
            with _lock:
                for exe, _path, vol, inst in _sessions():
                    try:
                        now_ids.add(inst)
                        if inst in seen or exe not in wishes:
                            continue
                        w = wishes[exe]
                        if "vol" in w:
                            _call(vol, 3, ctypes.c_float(w["vol"] / 100.0), None, argtypes=(ctypes.c_float, PV))
                        if "mute" in w:
                            _call(vol, 5, wintypes.BOOL(w["mute"]), None, argtypes=(wintypes.BOOL, PV))
                    except OSError:
                        pass
                    finally:
                        _release(vol)
            seen = now_ids                          # forget sessions that have gone
        except Exception as exc:  # noqa: BLE001 - never take PCDash down
            log(f"mixer: watch: {exc}")
            time.sleep(10)
        time.sleep(0.3)


if __name__ == "__main__":
    for r in snapshot():
        print(f"{r['name']:<24} {r['id']:<32} {r['vol']:>3}%  {'MUTED' if r['mute'] else ''}")
