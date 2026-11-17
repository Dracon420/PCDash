"""Minimal OBS WebSocket v5 client (sync, stdlib socket + wsproto).

The password is read at runtime from OBS's own settings on this PC
(%APPDATA%\\obs-studio\\user.ini, [OBSWebSocket]), so it never has to be copied
anywhere. Only ever connects to 127.0.0.1.
"""
from __future__ import annotations

import base64
import configparser
import hashlib
import json
import os
import socket
import threading
import time
import uuid

from wsproto import ConnectionType, WSConnection
from wsproto.events import (AcceptConnection, CloseConnection, Ping, RejectConnection,
                            Request, TextMessage)

OBS_INI = os.path.join(os.environ.get("APPDATA", ""), "obs-studio", "user.ini")
OBS_EXE = r"C:\Program Files\obs-studio\bin\64bit\obs64.exe"


OBS_WS_JSON = os.path.join(os.environ.get("APPDATA", ""), "obs-studio", "plugin_config",
                           "obs-websocket", "config.json")


def ws_settings() -> dict:
    """Port / password / enabled, straight from OBS's config. Never logged.

    OBS 33 keeps these in plugin_config\\obs-websocket\\config.json; some other
    builds use an [OBSWebSocket] section in user.ini. Try the JSON first.
    """
    try:
        with open(OBS_WS_JSON, encoding="utf-8-sig") as fh:
            j = json.load(fh)
        return {"enabled": bool(j.get("server_enabled")), "port": int(j.get("server_port") or 4455),
                "auth": bool(j.get("auth_required", True)), "password": j.get("server_password") or ""}
    except (OSError, ValueError):
        pass
    cp = configparser.ConfigParser(strict=False, interpolation=None)
    cp.optionxform = str
    try:
        cp.read(OBS_INI, encoding="utf-8-sig")
    except (OSError, configparser.Error):
        return {"enabled": False}
    if not cp.has_section("OBSWebSocket"):
        return {"enabled": False}
    s = cp["OBSWebSocket"]
    return {"enabled": s.get("ServerEnabled", "false").lower() == "true",
            "port": int(s.get("ServerPort", "4455") or 4455),
            "auth": s.get("AuthRequired", "true").lower() == "true",
            "password": s.get("ServerPassword", "")}


class OBSError(Exception):
    pass


class RequestFailed(OBSError):
    """One request failed or was slow; the connection itself is still fine."""


class OBSClient:
    def __init__(self):
        self.sock: socket.socket | None = None
        self.ws: WSConnection | None = None
        self.lock = threading.Lock()          # serialises socket writes
        self.waiting: dict[str, list] = {}    # requestId -> [Event, response]
        self.connected = False
        self.last_error: str | None = None
        self._buf: list[str] = []

    # -- transport ------------------------------------------------------
    def _send_json(self, obj: dict) -> None:
        # wsproto's state is not thread-safe: the poller, the preview and button
        # presses all send, so framing and writing happen under one lock.
        with self.lock:
            self.sock.sendall(self.ws.send(TextMessage(data=json.dumps(obj))))

    def _recv_messages(self):
        """Blocking read; yields complete JSON messages."""
        while True:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise OBSError("OBS closed the connection")
            self.ws.receive_data(chunk)
            for ev in self.ws.events():
                if isinstance(ev, TextMessage):
                    self._buf.append(ev.data)
                    if ev.message_finished:
                        msg, self._buf = "".join(self._buf), []
                        yield json.loads(msg)
                elif isinstance(ev, Ping):
                    with self.lock:
                        self.sock.sendall(self.ws.send(ev.response()))
                elif isinstance(ev, CloseConnection):
                    raise OBSError("OBS closed the connection")

    def connect(self) -> None:
        cfg = ws_settings()
        if not cfg.get("enabled"):
            raise OBSError("websocket_off")
        self.sock = socket.create_connection(("127.0.0.1", cfg["port"]), timeout=3)
        self.ws = WSConnection(ConnectionType.CLIENT)
        self.sock.sendall(self.ws.send(Request(host=f"127.0.0.1:{cfg['port']}", target="/",
                                               subprotocols=["obswebsocket.json"])))
        # Handshake
        accepted = False
        while not accepted:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise OBSError("handshake failed")
            self.ws.receive_data(chunk)
            for ev in self.ws.events():
                if isinstance(ev, AcceptConnection):
                    accepted = True
                elif isinstance(ev, RejectConnection):
                    raise OBSError("OBS rejected the connection")
                elif isinstance(ev, TextMessage):
                    self._buf.append(ev.data)  # Hello can arrive with the upgrade
        msgs = self._recv_messages()
        if self._buf:
            hello, self._buf = json.loads("".join(self._buf)), []
        else:
            hello = next(msgs)
        if hello.get("op") != 0:
            raise OBSError("unexpected hello")
        ident = {"rpcVersion": 1, "eventSubscriptions": 0}
        auth = hello["d"].get("authentication")
        if auth:
            pw = cfg.get("password", "")
            secret = base64.b64encode(hashlib.sha256((pw + auth["salt"]).encode()).digest()).decode()
            ident["authentication"] = base64.b64encode(
                hashlib.sha256((secret + auth["challenge"]).encode()).digest()).decode()
        self._send_json({"op": 1, "d": ident})
        reply = next(msgs)
        if reply.get("op") != 2:
            raise OBSError("authentication failed")
        self.sock.settimeout(None)
        self.connected = True
        self.last_error = None
        threading.Thread(target=self._reader, args=(msgs,), daemon=True, name="obs-reader").start()

    def _reader(self, msgs) -> None:
        try:
            for m in msgs:
                if m.get("op") == 7:
                    rid = m["d"].get("requestId")
                    slot = self.waiting.get(rid)
                    if slot:
                        slot[1] = m["d"]
                        slot[0].set()
        except Exception as exc:  # noqa: BLE001
            self.last_error = str(exc)
        finally:
            self.close()

    def close(self) -> None:
        self.connected = False
        try:
            if self.sock:
                self.sock.close()
        except OSError:
            pass
        for slot in list(self.waiting.values()):
            slot[0].set()

    # -- requests -------------------------------------------------------
    def call(self, request_type: str, data: dict | None = None, timeout: float = 5):
        if not self.connected:
            raise OBSError("not connected")
        rid = uuid.uuid4().hex
        slot = [threading.Event(), None]
        self.waiting[rid] = slot
        try:
            self._send_json({"op": 6, "d": {"requestType": request_type, "requestId": rid,
                                            "requestData": data or {}}})
            if not slot[0].wait(timeout) or slot[1] is None:
                if not self.connected:
                    raise OBSError("not connected")
                raise RequestFailed(f"{request_type}: no reply")
            st = slot[1].get("requestStatus", {})
            if not st.get("result"):
                raise RequestFailed(st.get("comment") or f"{request_type} failed (code {st.get('code')})")
            return slot[1].get("responseData") or {}
        finally:
            self.waiting.pop(rid, None)


# ---------------------------------------------------------------- state --

CLIENT = OBSClient()
STATE: dict = {"connected": False, "reason": "starting"}
STATE_LOCK = threading.Lock()
_last_bytes: dict = {}


def _bitrate(key: str, total_bytes, active: bool):
    now = time.monotonic()
    prev = _last_bytes.get(key)
    _last_bytes[key] = (now, total_bytes)
    if not active or not prev or total_bytes is None:
        return None
    dt = now - prev[0]
    return max(0.0, (total_bytes - prev[1]) * 8 / dt / 1e6) if dt > 0 else None


def _obs_running() -> bool:
    import psutil
    return any((p.info["name"] or "").lower() == "obs64.exe" for p in psutil.process_iter(["name"]))


def poll_once() -> dict:
    c = CLIENT
    st = {"connected": True}
    stream = c.call("GetStreamStatus")
    rec = c.call("GetRecordStatus")
    stats = c.call("GetStats")
    scene = c.call("GetCurrentProgramScene").get("currentProgramSceneName")
    scenes = [s["sceneName"] for s in reversed(c.call("GetSceneList").get("scenes", []))]
    try:
        rb = c.call("GetReplayBufferStatus").get("outputActive")
        rb_available = True
    except OBSError:
        rb, rb_available = False, False
    st["stream"] = {"active": stream.get("outputActive"), "reconnecting": stream.get("outputReconnecting"),
                    "timecode": stream.get("outputTimecode"), "duration_ms": stream.get("outputDuration"),
                    "congestion": stream.get("outputCongestion"),
                    "skipped": stream.get("outputSkippedFrames"), "total": stream.get("outputTotalFrames"),
                    "mbps": _bitrate("stream", stream.get("outputBytes"), stream.get("outputActive"))}
    st["record"] = {"active": rec.get("outputActive"), "paused": rec.get("outputPaused"),
                    "timecode": rec.get("outputTimecode"),
                    "mbps": _bitrate("record", rec.get("outputBytes"), rec.get("outputActive"))}
    st["replay"] = {"available": rb_available, "active": rb}
    st["stats"] = {"fps": stats.get("activeFps"), "cpu": stats.get("cpuUsage"),
                   "render_skipped": stats.get("renderSkippedFrames"),
                   "render_total": stats.get("renderTotalFrames"),
                   "output_skipped": stats.get("outputSkippedFrames"),
                   "output_total": stats.get("outputTotalFrames"),
                   "frame_ms": stats.get("averageFrameRenderTime")}
    st["scene"], st["scenes"] = scene, scenes

    # Audio inputs: anything that has a mute state.
    audio = []
    for inp in c.call("GetInputList").get("inputs", []):
        name = inp["inputName"]
        try:
            muted = c.call("GetInputMute", {"inputName": name}).get("inputMuted")
        except OBSError:
            continue  # video-only source
        audio.append({"name": name, "muted": muted, "kind": inp.get("inputKind")})
    st["audio"] = audio

    # Sources in the live scene, so the webcam can be shown/hidden.
    items = []
    if scene:
        try:
            scene_items = c.call("GetSceneItemList", {"sceneName": scene}).get("sceneItems", [])
        except RequestFailed:      # scene renamed/removed mid-poll
            scene_items = []
        for it in scene_items:
            items.append({"id": it["sceneItemId"], "name": it["sourceName"],
                          "enabled": it["sceneItemEnabled"], "kind": it.get("inputKind")})
    st["items"] = items
    return st


# The replay buffer runs only while streaming or recording (user's choice,
# 2026-11-11: keep GPU encoder use to a minimum when idle). HBB key 3 is bound
# to Save Replay, so during a session it always has the last 30 s ready.
AUTO_REPLAY = True
STOP_GRACE_S = 15        # keep it a little after a session ends, for a last clip
_replay_next_start = 0.0
_session_ended_at: float | None = None


def _ensure_replay(st: dict, log) -> None:
    global _replay_next_start, _session_ended_at
    rb = st.get("replay") or {}
    if not AUTO_REPLAY or not rb.get("available"):
        return
    now = time.monotonic()
    in_session = bool((st.get("stream") or {}).get("active") or (st.get("record") or {}).get("active"))

    if in_session:
        _session_ended_at = None
        if not rb.get("active") and now >= _replay_next_start:
            # OBS can take ~10 s to bring the buffer up; don't re-ask meanwhile.
            # A refusal may pop an error box in OBS, so back off further on failure.
            _replay_next_start = now + 20
            try:
                CLIENT.call("StartReplayBuffer")
                log("obs: replay buffer started (session began)")
            except OBSError as exc:
                _replay_next_start = now + 300
                log(f"obs: replay buffer start failed: {exc}")
        return

    # Not streaming or recording.
    if rb.get("active"):
        if _session_ended_at is None:
            _session_ended_at = now
        if now - _session_ended_at >= STOP_GRACE_S:
            try:
                CLIENT.call("StopReplayBuffer")
                log("obs: replay buffer stopped (no stream or recording)")
            except OBSError as exc:
                log(f"obs: replay buffer stop failed: {exc}")
            _session_ended_at = now   # don't re-issue every second while it winds down
    else:
        _session_ended_at = None


def poller(log) -> None:
    slow = 0
    while True:
        try:
            if not CLIENT.connected:
                cfg = ws_settings()
                if not _obs_running():
                    raise OBSError("obs_closed")
                if not cfg.get("enabled"):
                    raise OBSError("websocket_off")
                CLIENT.connect()
                log("obs: connected")
            st = poll_once()
            _ensure_replay(st, log)
            with STATE_LOCK:
                STATE.clear()
                STATE.update(st)
            slow = 0
            time.sleep(1)
        except RequestFailed as exc:
            # A scene switch can make OBS answer slowly or refuse one request.
            # That used to drop the whole connection (3 s of "Connecting..." and
            # a frozen preview); now keep the last state and simply poll again.
            slow += 1
            if slow >= 5:
                log(f"obs: {exc} (5 in a row, reconnecting)")
                CLIENT.close()
                slow = 0
            time.sleep(0.5)
        except Exception as exc:  # noqa: BLE001
            reason = str(exc)
            if reason not in ("obs_closed", "websocket_off"):
                if STATE.get("reason") != reason:
                    log(f"obs: {reason}")
            CLIENT.close()
            with STATE_LOCK:
                STATE.clear()
                STATE.update({"connected": False, "reason": reason})
            time.sleep(3)


def screenshot_jpeg(width: int = 1280, quality: int = 90) -> bytes | None:
    with STATE_LOCK:
        scene = STATE.get("scene")
    if not CLIENT.connected or not scene:
        return None
    d = CLIENT.call("GetSourceScreenshot", {"sourceName": scene, "imageFormat": "jpg",
                                             "imageWidth": max(320, min(width, 1920)),
                                             "imageCompressionQuality": max(50, min(quality, 95))},
                    timeout=2.5)
    data = d.get("imageData", "")
    return base64.b64decode(data.split(",", 1)[1]) if "," in data else None


ACTIONS = {
    "stream_start": "StartStream", "stream_stop": "StopStream",
    "record_start": "StartRecord", "record_stop": "StopRecord",
    "record_pause": "ToggleRecordPause",
    "replay_start": "StartReplayBuffer", "replay_stop": "StopReplayBuffer",
    "replay_save": "SaveReplayBuffer",
}


def act(action: str, arg: dict) -> dict:
    if action in ACTIONS:
        return CLIENT.call(ACTIONS[action])
    if action == "scene":
        out = CLIENT.call("SetCurrentProgramScene", {"sceneName": str(arg["name"])})
        with STATE_LOCK:       # so the next preview frame asks for the new scene
            if STATE.get("connected"):
                STATE["scene"] = str(arg["name"])
        return out
    if action == "mute":
        return CLIENT.call("ToggleInputMute", {"inputName": str(arg["name"])})
    if action == "item":
        with STATE_LOCK:
            scene = STATE.get("scene")
        return CLIENT.call("SetSceneItemEnabled", {"sceneName": scene, "sceneItemId": int(arg["id"]),
                                                   "sceneItemEnabled": bool(arg["enabled"])})
    if action == "folder":
        path = CLIENT.call("GetRecordDirectory").get("recordDirectory")
        if path and os.path.isdir(path):
            os.startfile(path)  # noqa: S606 - opens Explorer on OBS's own recording folder
        return {"path": path}
    raise OBSError("unknown action")
