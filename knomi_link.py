"""Pushes OBS status to the KNOMI 2 stream screen and runs its touch actions.

The KNOMI runs the PCDash stream firmware (knomi-fw, env knomiv2_stream). Once
a second this POSTs JSON to http://<knomi>/stream. The reply lists touch actions
queued on the screen since the last push, which are carried out here via OBS.
The PC always starts the connection, so PCDash itself stays loopback-only.

Settings live in knomi.json: host, fallback_ip, token, up_max_mbps.
Going live is deliberately not one of the actions.
"""
from __future__ import annotations

import json
import os
import socket
import queue
import threading
import time

import requests

import obs

HERE = os.path.dirname(os.path.abspath(__file__))
CFG_PATH = os.path.join(HERE, "knomi.json")
KNOMI_STATE = {"connected": False, "host": None, "fw": None, "error": None}


def _cfg() -> dict:
    try:
        with open(CFG_PATH, encoding="utf-8-sig") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def _resolve(cfg: dict) -> str | None:
    """knomi.local via mDNS, else the last known IP."""
    host = cfg.get("host") or "knomi.local"
    try:
        return socket.gethostbyname(host)
    except OSError:
        return cfg.get("fallback_ip")


def _payload(cfg: dict, upload_mbps) -> dict:
    with obs.STATE_LOCK:
        st = dict(obs.STATE)
    stream, rec, rb = st.get("stream") or {}, st.get("record") or {}, st.get("replay") or {}
    mic = next((a for a in st.get("audio", []) if "input" in (a.get("kind") or "")), None)
    cam = next((i for i in st.get("items", []) if i.get("kind") == "dshow_input"), None)
    ms = lambda v: int((v or 0) / 1000)  # noqa: E731
    return {
        "token": cfg.get("token", ""),
        "obs": bool(st.get("connected")),
        "live": bool(stream.get("active")), "reconnecting": bool(stream.get("reconnecting")),
        "live_s": ms(stream.get("duration_ms")),
        "rec": bool(rec.get("active")), "paused": bool(rec.get("paused")),
        "rec_s": _tc_seconds(rec.get("timecode")),
        "clips": bool(rb.get("active")),
        "mic_muted": bool(mic and mic.get("muted")),
        "cam": bool(cam and cam.get("enabled")),
        "scene": st.get("scene") or "",
        "up": round(upload_mbps or 0.0, 2),
        "up_max": float(cfg.get("up_max_mbps", 38)),
    }


def _tc_seconds(tc) -> int:
    try:
        h, m, s = (tc or "0:0:0").split(".")[0].split(":")
        return int(h) * 3600 + int(m) * 60 + int(s)
    except ValueError:
        return 0


def _do(action: str, log) -> None:
    with obs.STATE_LOCK:
        st = dict(obs.STATE)
    try:
        if action == "save_clip":
            obs.act("replay_save", {})
        elif action in ("scene_next", "scene_prev"):
            scenes = st.get("scenes") or []
            if len(scenes) > 1 and st.get("scene") in scenes:
                i = scenes.index(st["scene"]) + (1 if action == "scene_next" else -1)
                obs.act("scene", {"name": scenes[i % len(scenes)]})
        elif action == "mic_toggle":
            mic = next((a for a in st.get("audio", []) if "input" in (a.get("kind") or "")), None)
            if mic:
                obs.act("mute", {"name": mic["name"]})
        elif action in ("vol_up", "vol_down"):        # swipe up / down (2026-11-11)
            import onn_view
            onn_view.volume(+1 if action == "vol_up" else -1)
        elif action in ("view_next", "view_prev"):    # swipe left / right: what the Onn shows
            import onn_view
            onn_view.step_view(+1 if action == "view_next" else -1, log,
                               f"KNOMI {action}")
            return
        else:
            return
        log(f"knomi: {action}")
    except obs.OBSError as exc:
        log(f"knomi: {action} failed: {exc}")


# Swipes are only picked up when the PC checks in, so check in often (the
# KNOMI redraws on its own 250 ms timer, so this costs no extra drawing). Actions
# run on their own thread: a Discord switch can take seconds and must not hold
# up the next check-in (laggy view changes, 2026-11-11).
POLL_S = 0.3
_actions: "queue.Queue[str]" = queue.Queue()


def _worker(log) -> None:
    while True:
        a = _actions.get()
        try:
            _do(a, log)
        except Exception as exc:  # noqa: BLE001
            log(f"knomi: {a} failed: {exc}")


def run(log, get_upload) -> None:
    threading.Thread(target=_worker, args=(log,), daemon=True, name="knomi-actions").start()
    session = requests.Session()
    ip, ip_at, was_ok = None, 0.0, None
    while True:
        cfg = _cfg()
        if not cfg.get("token"):
            time.sleep(10)
            continue
        if ip is None or time.monotonic() - ip_at > 300:
            ip, ip_at = _resolve(cfg), time.monotonic()
        try:
            r = session.post(f"http://{ip}/stream", json=_payload(cfg, get_upload()), timeout=2)
            if r.status_code == 200:
                body = r.json()
                KNOMI_STATE.update(connected=True, host=ip, fw=body.get("fw"), error=None)
                if was_ok is not True:
                    log(f"knomi: connected at {ip} ({body.get('fw')})")
                was_ok = True
                for a in body.get("actions", []):
                    _actions.put(a)
            else:
                # 404 = still the stock firmware (no /stream route); 401 = token mismatch.
                KNOMI_STATE.update(connected=False, host=ip, error=f"HTTP {r.status_code}")
                if was_ok is not False:
                    log(f"knomi: {ip} answered HTTP {r.status_code}")
                was_ok = False
                time.sleep(9)
        except (requests.RequestException, ValueError) as exc:
            KNOMI_STATE.update(connected=False, error=type(exc).__name__)
            if was_ok is not False:
                log(f"knomi: unreachable ({type(exc).__name__})")
            was_ok = False
            ip = None          # re-resolve next time (DHCP may have moved it)
            time.sleep(4)
        time.sleep(POLL_S)
