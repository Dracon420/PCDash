"""Feeds the K-Touch (5" WiFi touchscreen) and carries out what is tapped on it.

The K-Touch runs PCDash's own firmware (ktouch-fw, env "app"). Once a second this
POSTs the whole picture to http://<k-touch>/state: PC stats, game servers, OBS
status and the game list. The reply lists actions tapped since the last push:

    launch:<game id>                 start a PC game from games.json
    server:<server id>:<action>      start / stop / restart via ArkPanel
                                     (stop and restart are confirmed on the device)
    obs:<action>                     stream_start/stop (confirmed on the device),
                                     record_start/stop/pause, replay_save,
                                     mic_toggle, cam_toggle
    scene:<name>                     switch OBS scene
    screen:<samsung|tv|toggle>       which screen games open on; moves an open
                                     game there too (window_placer)
    mix:<app id>:<0-100|mute>        per-app volume / mute toggle (audio_mixer);
                                     app id is the exe name, or "master"

The PC always starts the connection, so PCDash itself stays loopback-only.
Settings: ktouch.json (host, fallback_ip, token). Games: games.json.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import time

import requests

import obs

HERE = os.path.dirname(os.path.abspath(__file__))
CFG_PATH = os.path.join(HERE, "ktouch.json")
GAMES_PATH = os.path.join(HERE, "games.json")
KTOUCH_STATE = {"connected": False, "host": None, "fw": None}
NO_WINDOW = 0x08000000


def _load(path: str) -> dict:
    try:
        with open(path, encoding="utf-8-sig") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def _save_ip(ip: str) -> None:
    cfg = _load(CFG_PATH)
    if cfg and cfg.get("fallback_ip") != ip:
        cfg["fallback_ip"] = ip
        with open(CFG_PATH, "w", encoding="utf-8") as fh:
            json.dump(cfg, fh, indent=2)


def _resolve(cfg: dict) -> str | None:
    try:
        return socket.gethostbyname(cfg.get("host") or "pcdash-ktouch.local")
    except OSError:
        return cfg.get("fallback_ip") or None


def _dur(s: float) -> str:
    s = int(s or 0)
    d, h, m = s // 86400, s % 86400 // 3600, s % 3600 // 60
    return f"{d}d {h}h" if d else f"{h}h {m}m" if h else f"{m}m"


def _tc_seconds(tc) -> int:
    try:
        h, m, s = (tc or "0:0:0").split(".")[0].split(":")
        return int(h) * 3600 + int(m) * 60 + int(s)
    except ValueError:
        return 0


def _stream() -> dict:
    with obs.STATE_LOCK:
        st = dict(obs.STATE)
    stream, rec, rb = st.get("stream") or {}, st.get("record") or {}, st.get("replay") or {}
    mic = next((a for a in st.get("audio", []) if "input" in (a.get("kind") or "")), None)
    cam = next((i for i in st.get("items", []) if i.get("kind") == "dshow_input"), None)
    total = stream.get("total") or 0
    return {
        "obs": bool(st.get("connected")),
        "live": bool(stream.get("active")), "live_s": int((stream.get("duration_ms") or 0) / 1000),
        "rec": bool(rec.get("active")), "paused": bool(rec.get("paused")), "rec_s": _tc_seconds(rec.get("timecode")),
        "clips": bool(rb.get("active")),
        "mic_muted": bool(mic and mic.get("muted")), "cam": bool(cam and cam.get("enabled")),
        "scene": st.get("scene") or "", "scenes": st.get("scenes") or [],
        "bitrate": round(stream.get("mbps") or 0, 1),
        "drop": round((stream.get("skipped") or 0) / total * 100, 1) if total else 0,
    }


_found_ids: dict[str, str] = {}      # games.json "find" -> AppID, once it is installed
_find_at = 0.0


def _resolve_finds(items: list) -> None:
    """Fill in the AppID for entries that only say "find" (a game still
    downloading, like Halo on 2026-11-12). Checked every 5 minutes."""
    global _find_at
    wanted = [i for i in items if not i.get("appid") and i.get("find") and i["id"] not in _found_ids]
    if not wanted or time.monotonic() - _find_at < 300:
        return
    _find_at = time.monotonic()
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command",
                              "Get-StartApps | ForEach-Object { $_.Name + [char]9 + $_.AppID }"],
                             capture_output=True, text=True, timeout=30, creationflags=NO_WINDOW).stdout
    except (OSError, subprocess.SubprocessError):
        return
    apps = [ln.split("\t", 1) for ln in out.splitlines() if "\t" in ln]
    for it in wanted:
        hit = next((aid for name, aid in apps
                    if it["find"].lower() in name.lower() and "!" in aid), None)
        if hit:
            _found_ids[it["id"]] = hit


def _items() -> list[dict]:
    """Every K-Touch tile: games then apps, each tagged with its folder."""
    j = _load(GAMES_PATH)
    out = [dict(g, kind="game") for g in j.get("games", [])] + [dict(a, kind="app") for a in j.get("apps", [])]
    _resolve_finds(out)
    for it in out:
        if not it.get("appid") and it["id"] in _found_ids:
            it["appid"] = _found_ids[it["id"]]
    return out


def _payload(token: str, pc, servers) -> dict:
    items = _items()
    tile = ("id", "name", "short", "color")
    games = [{k: g.get(k) for k in tile} for g in items if g["kind"] == "game"]
    apps = [{k: a.get(k) for k in tile} for a in items if a["kind"] == "app"]
    srv = []
    for s in servers():
        srv.append({"id": s["id"], "name": s["name"], "game": s["game"],
                    "host": "this PC" if s.get("host") == "HOME-SERVER-1" else (s.get("host") or ""),
                    "on": s["running"], "players": s.get("players"), "note": s.get("note") or "",
                    "uptime": _dur(s.get("uptime"))})
    # "token" first: the device checks it before parsing anything else.
    return {"token": token, "time": time.strftime("%I:%M %p").lstrip("0"),
            "pc": pc(), "servers": srv, "stream": _stream(), "games": games, "apps": apps,
            "screen": _screen(items), "mixer": _mixer()}


def _mixer() -> list[dict]:
    try:
        import audio_mixer
        return audio_mixer.snapshot()[:9]
    except Exception:  # noqa: BLE001 - no audio device must never stop the push
        return []


_have_at, _have = 0.0, {}


def _screen(items: list) -> dict:
    """Screen tab: the all-games choice, each tile's screen, which screens exist."""
    global _have_at, _have
    import window_placer
    if time.monotonic() - _have_at > 10:
        try:
            _have = window_placer.screen_monitors()
        except Exception:  # noqa: BLE001
            _have = {}
        _have_at = time.monotonic()
    return {"game": window_placer.game_screen(),
            "assign": {i["id"]: window_placer.screen_for(i["id"], i["kind"]) for i in items},
            "tv": "tv" in _have, "samsung": "samsung" in _have, "onn": "onn" in _have}


def _launch_game(game_id: str, log) -> None:
    g = next((g for g in _items() if g.get("id") == game_id), None)
    if not g or not g.get("appid"):
        log(f"ktouch: can't launch {game_id!r} - not installed yet, or no appid in games.json")
        return
    # Xbox / Store apps start through their AppsFolder entry.
    subprocess.Popen(["explorer.exe", f"shell:AppsFolder\\{g['appid']}"], creationflags=NO_WINDOW)
    log(f"ktouch: launched {g['name']}")


OBS_DIRECT = {"stream_start", "stream_stop", "record_start", "record_stop", "record_pause", "replay_save"}


def web_state(pc, servers) -> dict:
    """The same picture the K-Touch gets, for the phone web remote (remote.py)."""
    p = _payload("", pc, servers)
    p.pop("token", None)
    return p


SCREENS = {"samsung", "tv", "onn"}


def valid_action(action: str, state: dict) -> bool:
    """Only what the K-Touch itself can send, against the current state."""
    kind, _, rest = (action or "").partition(":")
    ids = {g["id"] for g in state.get("games", []) + state.get("apps", [])}
    if kind == "launch":
        return rest in ids
    if kind == "server":
        sid, _, act = rest.rpartition(":")
        return act in {"start", "stop", "restart"} and sid in {s["id"] for s in state.get("servers", [])}
    if kind == "obs":
        return rest in OBS_DIRECT | {"mic_toggle", "cam_toggle"}
    if kind == "scene":
        return rest in (state.get("stream") or {}).get("scenes", [])
    if kind == "screen":
        target, _, which = rest.rpartition(":")
        return which in SCREENS and (target == "games" or target in ids)
    if kind == "mix":
        app, _, val = rest.rpartition(":")
        return app in {m["id"] for m in state.get("mixer", [])} and (val == "mute" or (val.isdigit() and int(val) <= 100))
    return False


def _do(action: str, log, server_control, source: str = "k-touch") -> None:
    kind, _, rest = action.partition(":")
    try:
        if kind == "launch":
            _launch_game(rest, log)
        elif kind == "server":
            sid, _, act = rest.rpartition(":")
            code, body = server_control(sid, act, source=source)
            if code != 200:
                log(f"ktouch: server {act} {sid} failed: {body}")
        elif kind == "screen":
            import window_placer
            target, _, which = rest.rpartition(":")
            if target:                       # screen:<item id | games>:<samsung|tv|onn>
                window_placer.set_screen(target, which, log)
            else:                            # older firmware: screen:<samsung|tv|toggle>
                window_placer.set_game_screen(which, log)
        elif kind == "mix":
            import audio_mixer
            app, _, val = rest.rpartition(":")
            if val == "mute":
                audio_mixer.toggle_mute(app)
            else:
                audio_mixer.set_volume(app, int(val))
            log(f"{source}: volume {app} -> {val}")
        elif kind == "scene":
            obs.act("scene", {"name": rest})
            log(f"ktouch: scene {rest}")
        elif kind == "obs":
            with obs.STATE_LOCK:
                st = dict(obs.STATE)
            if rest in OBS_DIRECT:
                obs.act(rest, {})
            elif rest == "mic_toggle":
                mic = next((a for a in st.get("audio", []) if "input" in (a.get("kind") or "")), None)
                if mic:
                    obs.act("mute", {"name": mic["name"]})
            elif rest == "cam_toggle":
                cam = next((i for i in st.get("items", []) if i.get("kind") == "dshow_input"), None)
                if cam:
                    obs.act("item", {"id": cam["id"], "enabled": not cam["enabled"]})
            else:
                return
            log(f"ktouch: obs {rest}")
    except Exception as exc:  # noqa: BLE001 - one bad tap must never stop the link
        log(f"ktouch: {action} failed: {exc}")


_img_failed: dict[str, float] = {}
_img_sent: dict[str, str | None] = {}    # game id -> tile_key of the picture last sent


def _send_missing_image(session, ip: str, token: str, have, log) -> None:
    """Send one tile picture the K-Touch doesn't have yet (one per push, so the
    status updates never stall). It keeps them in memory only, so after it
    restarts its list comes back empty and they are all sent again."""
    if have is None:          # older firmware without picture support
        return
    have = set(filter(None, str(have).split(",")))
    import game_art
    folders = [{"id": "_fgames", "folder": "games"}, {"id": "_fapps", "folder": "apps"}]
    for g in folders + _items():
        gid = g.get("id")
        if not gid or time.monotonic() < _img_failed.get(gid, 0):
            continue
        # Skip only if the K-Touch has it AND it is the current picture. A changed
        # game-art file (or a PCDash restart) re-sends it; the K-Touch replaces it in place.
        try:
            key = game_art.tile_key(g)
        except Exception:  # noqa: BLE001
            key = None
        if gid in have and (key is None or _img_sent.get(gid) == key):
            continue
        try:
            data = game_art.tile_bytes(g)
        except Exception as exc:  # noqa: BLE001
            log(f"ktouch: no tile art for {gid}: {exc}")
            data = None
        if not data:
            _img_failed[gid] = time.monotonic() + 600   # colour tile; retry in 10 min
            continue
        try:
            r = session.post(f"http://{ip}/img", params={"id": gid, "t": token}, data=data,
                             headers={"Content-Type": "application/octet-stream"}, timeout=10)
            if r.status_code != 200:
                log(f"ktouch: picture {gid} refused: HTTP {r.status_code} {r.text[:40]}")
                _img_failed[gid] = time.monotonic() + 300
            else:
                _img_sent[gid] = key
                log(f"ktouch: sent picture {gid}")
        except requests.RequestException as exc:
            log(f"ktouch: picture {gid} failed: {type(exc).__name__}")
        return


def run(log, pc, servers, server_control) -> None:
    session = requests.Session()
    ip, ip_at, was_ok = None, 0.0, None
    while True:
        cfg = _load(CFG_PATH)
        if not cfg.get("token"):
            time.sleep(10)
            continue
        if ip is None or time.monotonic() - ip_at > 300:
            ip, ip_at = _resolve(cfg), time.monotonic()
        if not ip:
            KTOUCH_STATE.update(connected=False)
            time.sleep(10)
            continue
        try:
            # Compact separators matter: the device looks for the exact text "token":"...
            body_txt = json.dumps(_payload(cfg["token"], pc, servers), separators=(",", ":"))
            r = session.post(f"http://{ip}/state", data=body_txt.encode(),
                             headers={"Content-Type": "application/json"}, timeout=3)
            if r.status_code == 200:
                body = r.json()
                KTOUCH_STATE.update(connected=True, host=ip, fw=body.get("fw"))
                if was_ok is not True:
                    log(f"ktouch: connected at {ip} ({body.get('fw')})")
                    _save_ip(ip)
                was_ok = True
                for a in body.get("actions", []):
                    _do(a, log, server_control)
                _send_missing_image(session, ip, cfg["token"], body.get("imgs"), log)
            else:
                KTOUCH_STATE.update(connected=False, host=ip)
                if was_ok is not False:
                    log(f"ktouch: {ip} answered HTTP {r.status_code}")
                was_ok = False
                time.sleep(9)
        except (requests.RequestException, ValueError) as exc:
            KTOUCH_STATE.update(connected=False)
            if was_ok is not False:
                log(f"ktouch: unreachable ({type(exc).__name__})")
            was_ok = False
            ip = None
            time.sleep(4)
        time.sleep(1)
