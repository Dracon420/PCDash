"""PCDash - a half-screen system dashboard for PC1's portrait monitor.

A separate, small Flask app on 127.0.0.1:8095. It reads local hardware stats
itself and talks to ArkPanel (127.0.0.1:8090) over its normal HTTP API for
game-server status and start/stop. It never imports or modifies ArkPanel, so
it can be started, stopped or crash without touching the panel or any server.

Run:  pythonw dash.py      (or use launch.ps1, which also opens the window)
"""
from __future__ import annotations

import ctypes
import json
import os
import secrets
import subprocess
import threading
import time
import winreg
from collections import deque
from ctypes import wintypes

import psutil
import requests
from flask import Flask, Response, abort, jsonify, request, send_from_directory

import obs

HERE = os.path.dirname(os.path.abspath(__file__))
PORT = 8095
PANEL = "http://127.0.0.1:8090"
PANEL_SECRETS = r"D:\Servers\ArkPanel\secrets.json"
NO_WINDOW = 0x08000000  # CREATE_NO_WINDOW - keep pythonw from flashing consoles

# A per-run token the page must echo on every POST. The server only listens on
# loopback, but this also stops some other web page in a browser on this PC
# from blind-POSTing "stop server" at localhost.
CSRF = secrets.token_urlsafe(24)

app = Flask(__name__, static_folder=None)

STATE: dict = {"net": {}, "cpu": {}, "gpu": None, "servers": None,
               "servers_error": None, "servers_at": 0}
HIST = {"down": deque(maxlen=90), "up": deque(maxlen=90),
        "cpu": deque(maxlen=90), "gpu": deque(maxlen=90)}
LOCK = threading.Lock()


def log(msg: str) -> None:
    try:
        with open(os.path.join(HERE, "dash.log"), "a", encoding="utf-8") as fh:
            fh.write(time.strftime("%Y-%m-%d %H:%M:%S ") + msg + "\n")
    except OSError:
        pass


# ---------------------------------------------------------------- hardware --

class PERFORMANCE_INFORMATION(ctypes.Structure):
    _fields_ = [("cb", wintypes.DWORD),
                ("CommitTotal", ctypes.c_size_t), ("CommitLimit", ctypes.c_size_t),
                ("CommitPeak", ctypes.c_size_t), ("PhysicalTotal", ctypes.c_size_t),
                ("PhysicalAvailable", ctypes.c_size_t), ("SystemCache", ctypes.c_size_t),
                ("KernelTotal", ctypes.c_size_t), ("KernelPaged", ctypes.c_size_t),
                ("KernelNonpaged", ctypes.c_size_t), ("PageSize", ctypes.c_size_t),
                ("HandleCount", wintypes.DWORD), ("ProcessCount", wintypes.DWORD),
                ("ThreadCount", wintypes.DWORD)]


def commit_gb() -> tuple[float, float]:
    pi = PERFORMANCE_INFORMATION()
    pi.cb = ctypes.sizeof(pi)
    if not ctypes.windll.psapi.GetPerformanceInfo(ctypes.byref(pi), pi.cb):
        return 0.0, 0.0
    return (pi.CommitTotal * pi.PageSize / 2**30, pi.CommitLimit * pi.PageSize / 2**30)


def _primary_nic() -> str | None:
    """The NIC carrying internet traffic: up, not loopback, not Tailscale."""
    stats = psutil.net_if_stats()
    best, best_bytes = None, -1
    counters = psutil.net_io_counters(pernic=True)
    for name, st in stats.items():
        low = name.lower()
        if not st.isup or "loopback" in low or "tailscale" in low or "vethernet" in low:
            continue
        c = counters.get(name)
        total = (c.bytes_recv + c.bytes_sent) if c else 0
        if total > best_bytes:
            best, best_bytes = name, total
    return best


def sampler() -> None:
    """Once a second: CPU and network rates."""
    psutil.cpu_percent(percpu=True)
    nic = _primary_nic()
    last = psutil.net_io_counters(pernic=True)
    last_t = time.monotonic()
    while True:
        time.sleep(1)
        try:
            now = psutil.net_io_counters(pernic=True)
            t = time.monotonic()
            dt = max(t - last_t, 0.001)
            if nic not in now:
                nic = _primary_nic()
            net = {}
            for name in (nic, "Tailscale"):
                if name and name in now and name in last:
                    net[name] = {
                        "down_mbps": (now[name].bytes_recv - last[name].bytes_recv) * 8 / dt / 1e6,
                        "up_mbps": (now[name].bytes_sent - last[name].bytes_sent) * 8 / dt / 1e6,
                    }
            last, last_t = now, t
            per = psutil.cpu_percent(percpu=True)
            freq = psutil.cpu_freq()
            with LOCK:
                STATE["net"] = {"nic": nic, "ifaces": net}
                STATE["cpu"] = {"total": sum(per) / len(per), "per": per,
                                "mhz": freq.current if freq else None}
                main = net.get(nic) or {}
                HIST["down"].append(round(main.get("down_mbps", 0), 2))
                HIST["up"].append(round(main.get("up_mbps", 0), 2))
                HIST["cpu"].append(round(STATE["cpu"]["total"], 1))
        except Exception as exc:  # noqa: BLE001
            log(f"sampler: {exc}")


GPU_FIELDS = ["name", "temperature.gpu", "utilization.gpu", "memory.used",
              "memory.total", "fan.speed", "power.draw", "power.limit",
              "clocks.gr", "clocks.mem", "utilization.encoder"]


def gpu_poller() -> None:
    """nvidia-smi in loop mode: one long-lived process, a line every 2 s."""
    while True:
        try:
            proc = subprocess.Popen(
                ["nvidia-smi", "--query-gpu=" + ",".join(GPU_FIELDS),
                 "--format=csv,noheader,nounits", "-l", "2"],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                text=True, creationflags=NO_WINDOW)
            for line in proc.stdout:
                parts = [p.strip() for p in line.split(",")]
                if len(parts) != len(GPU_FIELDS):
                    continue

                def num(v):
                    try:
                        return float(v)
                    except ValueError:
                        return None
                g = {"name": parts[0], "temp": num(parts[1]), "util": num(parts[2]),
                     "mem_used": num(parts[3]), "mem_total": num(parts[4]),
                     "fan": num(parts[5]), "power": num(parts[6]),
                     "power_limit": num(parts[7]), "clock": num(parts[8]),
                     "mem_clock": num(parts[9]), "encoder": num(parts[10])}
                with LOCK:
                    STATE["gpu"] = g
                    HIST["gpu"].append(g["util"] or 0)
        except Exception as exc:  # noqa: BLE001
            log(f"gpu: {exc}")
        time.sleep(10)


DRIVES = ["C:\\", "D:\\", "F:\\", "I:\\", "T:\\"]


def disks() -> list[dict]:
    out = []
    for d in DRIVES:
        try:
            u = psutil.disk_usage(d)
        except OSError:
            continue
        out.append({"drive": d[:2], "free_gb": u.free / 2**30, "total_gb": u.total / 2**30,
                    "percent": u.percent})
    return out


def top_processes(n: int = 5) -> list[dict]:
    rows = []
    for p in psutil.process_iter(["name", "memory_info"]):
        mi = p.info.get("memory_info")
        if mi:
            rows.append((mi.rss, p.info["name"] or "?"))
    # Fold same-named processes (msedge.exe x 20) into one line.
    folded: dict[str, list] = {}
    for rss, name in rows:
        f = folded.setdefault(name, [0, 0])
        f[0] += rss
        f[1] += 1
    best = sorted(folded.items(), key=lambda kv: kv[1][0], reverse=True)[:n]
    return [{"name": k, "gb": v[0] / 2**30, "count": v[1]} for k, v in best]


# ----------------------------------------------------------------- servers --

def _panel_auth():
    try:
        with open(PANEL_SECRETS, encoding="utf-8-sig") as fh:
            s = json.load(fh)
        return (s["panel_user"], s["panel_password"])
    except (OSError, KeyError, ValueError) as exc:
        log(f"secrets: {exc}")
        return None


def _get(path: str, timeout: float = 15):
    r = requests.get(PANEL + path, auth=_panel_auth(), timeout=timeout)
    r.raise_for_status()
    return r.json()


def fetch_servers() -> list[dict]:
    """One flat list across games, in the shape the page draws."""
    out = []
    status = _get("/api/cluster/status", timeout=30)
    try:
        players = _get("/api/cluster/players").get("servers", {})
    except Exception:  # noqa: BLE001
        players = {}
    for s in status.get("servers", []):
        p = players.get(s["name"], {})
        out.append({
            "id": f"ark:{s['name']}", "game": "ARK", "name": s.get("display_name") or s["name"],
            "host": s.get("host"), "running": bool(s.get("running")),
            "state": s.get("state"), "note": s.get("maintenance"),
            "players": p.get("count") if p.get("known") else None,
            "player_names": p.get("names") or [],
            "mem_gb": s.get("private_gb") or s.get("memory_gb"),
            "uptime": s.get("uptime_seconds") or 0,
        })
    try:
        pw = _get("/api/palworld")
        if pw.get("enabled"):
            out.append({
                "id": "palworld", "game": "Palworld", "name": pw.get("display_name", "Palworld"),
                "host": pw.get("host"), "running": bool(pw.get("running")),
                "state": "running" if pw.get("running") else "stopped", "note": None,
                "players": len(pw.get("players") or []),
                "player_names": [x.get("name", "?") if isinstance(x, dict) else str(x)
                                 for x in (pw.get("players") or [])],
                "mem_gb": pw.get("private_gb"), "uptime": pw.get("uptime_seconds") or 0,
            })
    except Exception as exc:  # noqa: BLE001
        log(f"palworld status: {exc}")
    try:
        mc = _get("/api/games/minecraft/servers/minecraft/status")
        out.append({
            "id": "minecraft", "game": "Minecraft", "name": "Minecraft (Family World)",
            "host": "HOME-SERVER-1", "running": bool(mc.get("running")),
            "state": "running" if mc.get("running") else "stopped", "note": None,
            "players": None, "player_names": [],
            "mem_gb": mc.get("memory_gb"), "uptime": mc.get("uptime_seconds") or 0,
        })
    except Exception as exc:  # noqa: BLE001
        log(f"minecraft status: {exc}")
    return out


def server_poller() -> None:
    while True:
        try:
            servers = fetch_servers()
            with LOCK:
                STATE["servers"], STATE["servers_error"] = servers, None
                STATE["servers_at"] = time.time()
        except Exception as exc:  # noqa: BLE001
            with LOCK:
                STATE["servers_error"] = f"ArkPanel not answering ({type(exc).__name__})"
        time.sleep(10)


# Only start / stop / restart are exposed. Everything riskier (kill, restore,
# mods, map switches) stays in ArkPanel itself, where it has its own guards.
SAFE_ACTIONS = {"start", "stop", "restart"}


def control_path(server_id: str, action: str) -> str | None:
    if action not in SAFE_ACTIONS:
        return None
    if server_id.startswith("ark:"):
        name = server_id[4:]
        if not name.isalnum():
            return None
        return f"/api/server/{name}/{action}"
    if server_id == "palworld":
        # The panel's Palworld route: it sets the wanted-flag before a stop and
        # does RCON Save -> Shutdown, never a kill.
        return f"/api/palworld/control/{action}"
    if server_id == "minecraft":
        return f"/api/games/minecraft/servers/minecraft/{action}"
    return None


# ---------------------------------------------------------------- settings --

PLANS = {"balanced": "381b4222-f694-41f0-9685-ff5bb260df2e",
         "high": "8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c",
         "saver": "a1841308-3541-4fab-bc81-f71556f20b4a"}


def active_plan() -> str | None:
    out = subprocess.run(["powercfg", "/getactivescheme"], capture_output=True,
                         text=True, creationflags=NO_WINDOW).stdout.lower()
    for key, guid in PLANS.items():
        if guid in out:
            return key
    return None


GAMEBAR = r"Software\Microsoft\GameBar"


def game_mode() -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, GAMEBAR) as k:
            return bool(winreg.QueryValueEx(k, "AutoGameModeEnabled")[0])
    except OSError:
        return True  # absent means Windows' default, which is on


def set_game_mode(on: bool) -> None:
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, GAMEBAR) as k:
        winreg.SetValueEx(k, "AutoGameModeEnabled", 0, winreg.REG_DWORD, 1 if on else 0)


LAUNCH = {
    "taskmgr": ["taskmgr.exe"],
    "sound": ["explorer.exe", "ms-settings:sound"],
    "display": ["explorer.exe", "ms-settings:display"],
    "gamebar": ["explorer.exe", "ms-gamebar:"],
    "arkpanel": ["explorer.exe", "http://127.0.0.1:8090/"],
    "sunshine": ["explorer.exe", "https://localhost:47990/"],
    "stream": ["powershell.exe", "-NoProfile", "-WindowStyle", "Hidden", "-ExecutionPolicy", "Bypass",
               "-File", os.path.join(HERE, "launch.ps1"), "-Stream"],
}


# ------------------------------------------------------------------ routes --

def _check_csrf():
    if request.headers.get("X-PCDash") != CSRF:
        abort(403)


@app.get("/")
def index():
    with open(os.path.join(HERE, "static", "index.html"), encoding="utf-8") as fh:
        html = fh.read()
    return html.replace("__CSRF__", CSRF), 200, {"Cache-Control": "no-store"}


@app.get("/static/<path:p>")
def static_files(p):
    return send_from_directory(os.path.join(HERE, "static"), p)


@app.get("/api/stats")
def api_stats():
    vm = psutil.virtual_memory()
    c_used, c_limit = commit_gb()
    with LOCK:
        body = {
            "cpu": STATE["cpu"], "net": STATE["net"], "gpu": STATE["gpu"],
            "hist": {k: list(v) for k, v in HIST.items()},
        }
    body["mem"] = {"used_gb": (vm.total - vm.available) / 2**30, "total_gb": vm.total / 2**30,
                   "percent": vm.percent, "commit_gb": c_used, "commit_limit_gb": c_limit}
    body["disks"] = disks()
    body["uptime"] = time.time() - psutil.boot_time()
    body["top"] = top_processes()
    body["devices"] = devices_status()
    return jsonify(body)


# Wireless devices PCDash talks to. The K-Touch runs on a battery it cannot
# measure (no gauge, no charge pin - probed 2026-11-11), so the only warning
# possible is "it has gone quiet". A short grace period hides reboots/updates.
DEVICE_GRACE_S = 20
_dev_seen: dict[str, float] = {}
_started = time.time()


def devices_status() -> dict:
    import ktouch_link
    out = {}
    for name, st in (("ktouch", ktouch_link.KTOUCH_STATE),):
        now = time.time()
        if st.get("connected"):
            _dev_seen[name] = now
        last = _dev_seen.get(name, _started)
        out[name] = {"online": bool(st.get("connected")),
                     "offline_s": 0 if st.get("connected") else round(now - last),
                     "warn": not st.get("connected") and now - last > DEVICE_GRACE_S}
    return out


@app.get("/api/servers")
def api_servers():
    with LOCK:
        return jsonify({"servers": STATE["servers"], "error": STATE["servers_error"],
                        "at": STATE["servers_at"]})


def server_control(server_id: str, action: str, source: str = "dashboard") -> tuple[int, dict]:
    """Start/stop/restart one game server through ArkPanel. Shared by the page and the K-Touch."""
    path = control_path(server_id, action)
    if not path:
        return 400, {"error": "not allowed"}
    log(f"control ({source}): {action} {server_id} -> {path}")
    try:
        r = requests.post(PANEL + path, auth=_panel_auth(), json={}, timeout=30)
        try:
            body = r.json()
        except ValueError:
            body = {"raw": r.text[:300]}
        return (200 if r.ok else 502), {"status": r.status_code, "body": body}
    except requests.RequestException as exc:
        return 502, {"error": str(exc)}


@app.post("/api/servers/<path:server_id>/<action>")
def api_server_control(server_id, action):
    _check_csrf()
    code, body = server_control(server_id, action)
    return jsonify(body), code


def pc_snapshot() -> dict:
    """Compact PC stats for the K-Touch's PC tab."""
    vm = psutil.virtual_memory()
    with LOCK:
        cpu, net, gpu = dict(STATE["cpu"]), dict(STATE["net"]), STATE["gpu"] or {}
    nic = (net.get("ifaces") or {}).get(net.get("nic")) or {}
    return {
        "cpu": round(cpu.get("total") or 0, 1), "cpu_ghz": round((cpu.get("mhz") or 0) / 1000, 2),
        "gpu": gpu.get("util") or 0, "gpu_t": gpu.get("temp") or 0, "fan": gpu.get("fan") or 0,
        "vram": round((gpu.get("mem_used") or 0) / 1024, 1), "vram_total": round((gpu.get("mem_total") or 0) / 1024),
        "ram_used": round((vm.total - vm.available) / 2**30, 1), "ram_total": round(vm.total / 2**30, 1),
        "down": round(nic.get("down_mbps") or 0, 2), "up": round(nic.get("up_mbps") or 0, 2),
    }


def servers_snapshot() -> list[dict]:
    with LOCK:
        return list(STATE["servers"] or [])


@app.get("/api/settings")
def api_settings():
    return jsonify({"plan": active_plan(), "game_mode": game_mode()})


@app.post("/api/settings")
def api_settings_set():
    _check_csrf()
    data = request.get_json(silent=True) or {}
    if "plan" in data:
        guid = PLANS.get(data["plan"])
        if not guid:
            return jsonify({"error": "unknown plan"}), 400
        subprocess.run(["powercfg", "/setactive", guid], creationflags=NO_WINDOW)
        log(f"power plan -> {data['plan']}")
    if "game_mode" in data:
        set_game_mode(bool(data["game_mode"]))
        log(f"game mode -> {bool(data['game_mode'])}")
    return api_settings()


@app.post("/api/launch/<what>")
def api_launch(what):
    _check_csrf()
    if what == "arkpanel":                       # the desktop APP, not the web page
        import taskbar_hide
        ok = taskbar_hide.show_arkpanel()
        return jsonify({"ok": ok}), (200 if ok else 404)
    cmd = LAUNCH.get(what)
    if not cmd:
        return jsonify({"error": "unknown"}), 400
    subprocess.Popen(cmd, creationflags=NO_WINDOW)
    return jsonify({"ok": True})


# --------------------------------------------------------------------- OBS --

@app.get("/api/onn/view")
def api_onn_view():
    import onn_view
    since = request.args.get("since", type=float)
    if since is not None and request.args.get("boot") == BOOT_ID:
        onn_view.wait_change(since)          # answers the moment the view changes
    return jsonify({"view": onn_view.STATE["view"], "name": onn_view.VIEW_NAMES.get(onn_view.STATE["view"]),
                    "boot": BOOT_ID, "changed": onn_view.STATE["changed"]})


# Changes every time PCDash starts. The Onn window stays open across restarts,
# so its page reloads itself when this changes and picks up new views/code
# (2026-11-11: an old onn.html didn't know the "ark" view).
BOOT_ID = secrets.token_hex(4)


@app.post("/api/onn/view")
def api_onn_set_view():
    _check_csrf()
    import onn_view
    v = onn_view.set_view(str((request.get_json(silent=True) or {}).get("view", "")), log,
                          request.headers.get("X-Source") or "PCDash page")
    return jsonify({"view": v})


@app.get("/ark-open")
def ark_open():
    """Sign the Onn's ArkPanel view in, the way ArkPanel's own desktop app does.

    /desktop/open trades the desktop_key from secrets.json for a session and only
    accepts it from loopback. PCDash itself only listens on loopback, and anything
    that can reach it can already read secrets.json. The key goes in a redirect,
    never into a page.
    """
    import urllib.parse
    try:
        with open(PANEL_SECRETS, encoding="utf-8-sig") as fh:
            key = (json.load(fh).get("desktop_key") or "").strip()
    except (OSError, ValueError):
        key = ""
    url = "http://127.0.0.1:8090/"
    if key:
        url += "desktop/open?key=" + urllib.parse.quote(key)
    return Response(status=302, headers={"Location": url, "Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})


@app.get("/onn")
def onn_page():
    """Both views in one window: Stream Control on top, PC Dashboard below."""
    return send_from_directory(os.path.join(HERE, "static"), "onn.html", max_age=0)


@app.get("/stream")
def stream_page():
    with open(os.path.join(HERE, "static", "stream.html"), encoding="utf-8") as fh:
        html = fh.read()
    return html.replace("__CSRF__", CSRF), 200, {"Cache-Control": "no-store"}


@app.get("/saver")
def saver_page():
    return send_from_directory(os.path.join(HERE, "static"), "saver.html", max_age=0)


@app.get("/api/saver/art")
def api_saver_art():
    import saver
    return jsonify({"art": saver.art_list(), "slide_seconds": saver.conf().get("slide_seconds", 20)})


@app.get("/saver/art/<game>/<name>")
def saver_art_file(game, name):
    import saver
    return send_from_directory(os.path.join(saver.ART, game), name, max_age=86400)


@app.post("/api/saver/start")
def api_saver_start():
    _check_csrf()
    import saver
    saver.start_now()
    return jsonify({"ok": True})


@app.get("/api/obs/status")
def api_obs_status():
    with obs.STATE_LOCK:
        body = dict(obs.STATE)
    with LOCK:
        net = STATE["net"]
    body["upload_mbps"] = ((net.get("ifaces") or {}).get(net.get("nic")) or {}).get("up_mbps")
    return jsonify(body)


@app.get("/api/obs/preview.jpg")
def api_obs_preview():
    try:
        img = obs.screenshot_jpeg(int(request.args.get("w", 1280)), int(request.args.get("q", 90)))
    except obs.OBSError:
        img = None
    if not img:
        return Response(status=204)
    return Response(img, mimetype="image/jpeg", headers={"Cache-Control": "no-store"})


@app.post("/api/obs/launch")
def api_obs_launch():
    _check_csrf()
    if not os.path.isfile(obs.OBS_EXE):
        return jsonify({"error": "OBS is not installed"}), 404
    # OBS must start from its own bin folder or it cannot find its locale files.
    subprocess.Popen([obs.OBS_EXE], cwd=os.path.dirname(obs.OBS_EXE))
    log("obs: launched")
    return jsonify({"ok": True})


@app.post("/api/obs/<action>")
def api_obs_action(action):
    _check_csrf()
    try:
        out = obs.act(action, request.get_json(silent=True) or {})
        log(f"obs: {action}")
        return jsonify({"ok": True, "result": out})
    except (obs.OBSError, KeyError, ValueError) as exc:
        return jsonify({"error": str(exc)}), 400


# ---------------------------------------------------------- phone remote --
# remote.py (a separate python.exe, LAN + Tailscale on 8096) is the phone
# version of the K-Touch. It reaches PCDash only through these two loopback
# routes, with the key from remote.json. PCDash itself stays loopback-only.
REMOTE_CFG = os.path.join(HERE, "remote.json")


def _remote_key() -> str:
    try:
        with open(REMOTE_CFG, encoding="utf-8") as fh:
            return json.load(fh).get("key") or ""
    except (OSError, ValueError):
        return ""


def _check_remote():
    key = _remote_key()
    if (request.remote_addr != "127.0.0.1" or not key
            or not secrets.compare_digest(request.headers.get("X-Remote-Key", ""), key)):
        abort(403)


@app.get("/api/remote/state")
def api_remote_state():
    _check_remote()
    import ktouch_link
    return jsonify(ktouch_link.web_state(pc_snapshot, servers_snapshot))


@app.post("/api/remote/do")
def api_remote_do():
    _check_remote()
    import ktouch_link
    action = str((request.get_json(silent=True) or {}).get("action") or "")
    state = ktouch_link.web_state(pc_snapshot, servers_snapshot)
    if not ktouch_link.valid_action(action, state):
        return jsonify({"error": "not allowed"}), 400
    log(f"web remote: {action}")
    ktouch_link._do(action, log, server_control, source="web remote")
    return jsonify({"ok": True})


def start_remote() -> None:
    """Start remote.py as python.exe (Windows Firewall already allows python.exe
    on private networks; pythonw.exe, which runs PCDash, has no rule)."""
    import sys
    exe = os.path.join(os.path.dirname(sys.executable), "python.exe")
    if not os.path.isfile(exe):
        log("web remote: python.exe not found next to pythonw.exe - not started")
        return
    try:
        subprocess.Popen([exe, os.path.join(HERE, "remote.py"), "--parent", str(os.getpid())],
                         cwd=HERE, creationflags=NO_WINDOW,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError as exc:
        log(f"web remote: could not start: {exc}")


def main() -> None:
    start_remote()
    for fn in (sampler, gpu_poller, server_poller):
        threading.Thread(target=fn, daemon=True, name=fn.__name__).start()
    threading.Thread(target=obs.poller, args=(log,), daemon=True, name="obs").start()
    try:
        import hbb_link
        threading.Thread(target=hbb_link.run, args=(log,), daemon=True, name="hbb").start()
    except ImportError as exc:  # pyserial missing - the box just won't light up
        log(f"hbb: disabled ({exc})")

    def upload_now():
        with LOCK:
            net = STATE["net"]
        return ((net.get("ifaces") or {}).get(net.get("nic")) or {}).get("up_mbps")

    import knomi_link
    threading.Thread(target=knomi_link.run, args=(log, upload_now), daemon=True, name="knomi").start()

    import ktouch_link
    threading.Thread(target=ktouch_link.run, daemon=True, name="ktouch",
                     args=(log, pc_snapshot, servers_snapshot, server_control)).start()

    import cam_background                    # webcam background follows the running game
    threading.Thread(target=cam_background.run, args=(log,), daemon=True, name="cam-bg").start()

    import window_placer                     # slicers / CAD / games -> main screen
    threading.Thread(target=window_placer.run, args=(log,), daemon=True, name="placer").start()

    import onn_view                          # keep the Onn's view in step with what is really in front
    threading.Thread(target=onn_view.track, args=(log,), daemon=True, name="onn-track").start()

    import audio_mixer                       # saved per-app volumes go on each new sound (Discord pings)
    threading.Thread(target=audio_mixer.watch, args=(log,), daemon=True, name="mixer").start()

    import saver                             # game-art screensaver (Smart App Control blocks a .scr)
    threading.Thread(target=saver.run, args=(log, PORT), daemon=True, name="saver").start()
    log(f"PCDash starting on 127.0.0.1:{PORT}")
    try:
        from waitress import serve  # type: ignore
        serve(app, host="127.0.0.1", port=PORT, threads=8)
    except ImportError:
        # waitress not installed - Flask's own server is fine for one viewer.
        app.run(host="127.0.0.1", port=PORT, threaded=True)


if __name__ == "__main__":
    main()
