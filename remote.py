"""PCDash web remote - the K-Touch on your phone, over WiFi or Tailscale (2026-11-12).

PCDash starts this as python.exe (dash.py start_remote) and it quits when
PCDash does. It listens on port 8096 on every interface. Windows Firewall
already allows python.exe on private networks, and both the home network and
Tailscale are private.

Open it once with the key, which sets a cookie, and add it to the home screen:
    http://192.168.1.10:8096/?k=<key from remote.json>
    http://100.64.0.99:8096/?k=<key>          (Tailscale, from anywhere)

It can do only what the K-Touch can: everything goes through PCDash's
loopback /api/remote/* routes, which check each action (ktouch_link.valid_action).
Delete remote.json to make a new key; old phones then need the new link.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import secrets
import socket
import threading
import time

import psutil
import requests
from flask import Flask, Response, abort, jsonify, redirect, request, send_from_directory

HERE = os.path.dirname(os.path.abspath(__file__))
CFG = os.path.join(HERE, "remote.json")
LOG = os.path.join(HERE, "remote.log")
DASH = "http://127.0.0.1:8095"
PORT = 8096
COOKIE = "pcdash_remote"


def log(msg: str) -> None:
    try:
        with open(LOG, "a", encoding="utf-8") as fh:
            fh.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")
    except OSError:
        pass


def key() -> str:
    try:
        with open(CFG, encoding="utf-8") as fh:
            k = json.load(fh).get("key")
        if k:
            return k
    except (OSError, ValueError):
        pass
    k = secrets.token_urlsafe(18)
    with open(CFG, "w", encoding="utf-8") as fh:
        json.dump({"key": k, "port": PORT}, fh, indent=2)
    log("made a new key in remote.json")
    return k


KEY = key()
app = Flask(__name__)
session = requests.Session()


def _authed() -> bool:
    return secrets.compare_digest(request.cookies.get(COOKIE, ""), KEY)


@app.before_request
def gate():
    if request.path in ("/manifest.webmanifest", "/icon.png"):
        return None
    k = request.args.get("k")
    if k is not None:
        if not secrets.compare_digest(k, KEY):
            time.sleep(1)                       # slow down guessing
            abort(403)
        # Not a 302: a link tapped in another app makes the whole redirect chain
        # cross-site, so the browser would hold the new cookie back from "/".
        # A same-origin script navigation sends it. Lax (not Strict) for the same
        # reason; POSTs still need the X-PCDash-Remote header.
        log(f"{request.remote_addr} signed in ({request.user_agent.string[:60]})")
        resp = Response('<!doctype html><meta name="viewport" content="width=device-width">'
                        '<body style="background:#040509;color:#e8f6ff;font-family:sans-serif">Signed in...'
                        '<script>location.replace("/")</script>', 200,
                        {"Content-Type": "text/html", "Cache-Control": "no-store"})
        resp.set_cookie(COOKIE, KEY, max_age=400 * 86400, httponly=True, samesite="Lax")
        return resp
    if not _authed():
        log(f"{request.remote_addr} not signed in: {request.path}")
        return Response("PCDash remote: open the link with the key once on this device.",
                        403, {"Content-Type": "text/plain"})
    # Actions need a header a cross-site page can't send without a CORS preflight.
    if request.method == "POST" and request.headers.get("X-PCDash-Remote") != "1":
        abort(403)
    return None


@app.get("/")
def index():
    return send_from_directory(os.path.join(HERE, "static"), "remote.html", max_age=0)


@app.get("/manifest.webmanifest")
def manifest():
    return jsonify({"name": "PCDash Remote", "short_name": "PCDash", "start_url": "/",
                    "display": "standalone", "background_color": "#07031a", "theme_color": "#07031a",
                    "icons": [{"src": "/icon.png", "sizes": "512x512", "type": "image/png"}]})


@app.get("/icon.png")
def icon():
    return send_from_directory(os.path.join(HERE, "static"), "remote-icon.png", max_age=86400)


def _dash(method: str, path: str, **kw):
    return session.request(method, DASH + path, headers={"X-Remote-Key": KEY}, timeout=10, **kw)


@app.get("/api/state")
def state():
    try:
        r = _dash("GET", "/api/remote/state")
        return Response(r.content, r.status_code, {"Content-Type": "application/json", "Cache-Control": "no-store"})
    except requests.RequestException:
        return jsonify({"error": "PCDash is not answering"}), 502


@app.post("/api/do")
def do():
    action = str((request.get_json(silent=True) or {}).get("action") or "")[:120]
    try:
        r = _dash("POST", "/api/remote/do", json={"action": action})
        log(f"{request.remote_addr} {action} -> {r.status_code}")
        return Response(r.content, r.status_code, {"Content-Type": "application/json"})
    except requests.RequestException:
        return jsonify({"error": "PCDash is not answering"}), 502


# ------------------------------------------------------------------ tiles --
_tiles: dict[str, tuple[float, bytes]] = {}
TILE_W, TILE_H = 480, 300


@app.get("/tile/<tid>.jpg")
def tile(tid: str):
    import game_art
    from PIL import Image
    with open(os.path.join(HERE, "games.json"), encoding="utf-8-sig") as fh:
        j = json.load(fh)
    item = next((g for g in j.get("games", []) + j.get("apps", []) if g.get("id") == tid), None)
    if not item:
        abort(404)
    src = game_art.source_path(item)
    if not src or not os.path.isfile(src):
        abort(404)
    mt = os.path.getmtime(src)
    hit = _tiles.get(tid)
    if not hit or hit[0] != mt:
        img = Image.open(src).convert("RGB")
        s = max(TILE_W / img.width, TILE_H / img.height)
        img = img.resize((max(TILE_W, round(img.width * s)), max(TILE_H, round(img.height * s))), Image.LANCZOS)
        l, t = (img.width - TILE_W) // 2, (img.height - TILE_H) // 2
        buf = io.BytesIO()
        img.crop((l, t, l + TILE_W, t + TILE_H)).save(buf, "JPEG", quality=82)
        hit = _tiles[tid] = (mt, buf.getvalue())
    return Response(hit[1], 200, {"Content-Type": "image/jpeg", "Cache-Control": "max-age=86400"})


# --------------------------------------------------------------- lifetime --
def watch_parent(pid: int) -> None:
    """Quit when PCDash does, so a restarted PCDash starts a fresh copy."""
    while psutil.pid_exists(pid):
        time.sleep(3)
    log("PCDash gone - stopping")
    os._exit(0)


def wait_for_port() -> bool:
    """An old copy may still hold the port for a few seconds after a restart."""
    for _ in range(30):
        s = socket.socket()
        try:
            s.bind(("0.0.0.0", PORT))
            return True
        except OSError:
            time.sleep(1)
        finally:
            s.close()
    return False


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--parent", type=int)
    a = ap.parse_args()
    if a.parent:
        threading.Thread(target=watch_parent, args=(a.parent,), daemon=True).start()
    if not wait_for_port():
        log(f"port {PORT} is busy - not started")
        return
    log(f"listening on 0.0.0.0:{PORT}")
    from waitress import serve
    serve(app, host="0.0.0.0", port=PORT, threads=6)


if __name__ == "__main__":
    main()
