"""Feeds OBS status to the HBB key box so its lights follow the stream.

The HBB (RP2040 running CircuitPython, see hbb/code.py) exposes two USB serial
ports: the console and a "data" port. Once a second this writes one line to the
data port:

    L1 R0 P0 B1 M0 C1 X1 Y0
    L live  R recording  P rec paused  B replay buffer on  M mic muted
    C webcam shown  X key-6 scene on air  Y key-7 scene on air

Which scenes keys 6 and 7 light up for is set in hbb.json next to this file.
If the box is unplugged this just keeps looking for it; nothing else waits on it.
"""
from __future__ import annotations

import json
import os
import time

import serial
import serial.tools.list_ports

import obs

HERE = os.path.dirname(os.path.abspath(__file__))
CFG_PATH = os.path.join(HERE, "hbb.json")
CIRCUITPY_VID = 0x239A
DEFAULT_CFG = {"key6_scene": "Gaming", "key7_scene": "Be Right Back"}


def _cfg() -> dict:
    try:
        with open(CFG_PATH, encoding="utf-8-sig") as fh:
            return {**DEFAULT_CFG, **json.load(fh)}
    except (OSError, ValueError):
        return dict(DEFAULT_CFG)


def find_data_port() -> str | None:
    """CircuitPython lists console first and data second; take the data one."""
    ports = [p for p in serial.tools.list_ports.comports() if p.vid == CIRCUITPY_VID]
    if not ports:
        return None
    for p in ports:  # interface 2 = the data CDC (0 = console)
        if (p.location or "").endswith(".2") or "data" in (p.interface or "").lower():
            return p.device
    if len(ports) >= 2:
        return sorted(ports, key=lambda p: int("".join(c for c in p.device if c.isdigit()) or 0))[-1].device
    return None


def status_line() -> str:
    with obs.STATE_LOCK:
        st = dict(obs.STATE)
    if not st.get("connected"):
        return "L0 R0 P0 B0 M0 C0 X0 Y0"
    cfg = _cfg()
    stream, rec, rb = st.get("stream") or {}, st.get("record") or {}, st.get("replay") or {}
    mic = next((a for a in st.get("audio", []) if "input" in (a.get("kind") or "")), None)
    # Key 5's light follows the source named in hbb.json (whatever F17 is bound to
    # in OBS); with no name set, the first webcam in the scene.
    want = cfg.get("key5_source")
    cam = next((i for i in st.get("items", []) if (i.get("name") == want if want
                                                  else i.get("kind") == "dshow_input")), None)
    scene = st.get("scene")
    b = lambda v: 1 if v else 0  # noqa: E731
    return (f"L{b(stream.get('active'))} R{b(rec.get('active'))} P{b(rec.get('paused'))} "
            f"B{b(rb.get('active'))} M{b(mic and mic.get('muted'))} C{b(cam and cam.get('enabled'))} "
            f"X{b(scene == cfg['key6_scene'])} Y{b(scene == cfg['key7_scene'])}")


HBB_STATE = {"port": None, "connected": False}


def run(log) -> None:
    port = None
    while True:
        try:
            if port is None:
                name = find_data_port()
                if not name:
                    HBB_STATE.update(port=None, connected=False)
                    time.sleep(5)
                    continue
                port = serial.Serial(name, timeout=0, write_timeout=1)
                HBB_STATE.update(port=name, connected=True)
                log(f"hbb: connected on {name}")
            port.write((status_line() + "\n").encode())
            time.sleep(0.5)
        except (serial.SerialException, OSError) as exc:
            if port is not None:
                log(f"hbb: lost ({exc})")
            try:
                if port:
                    port.close()
            except Exception:  # noqa: BLE001
                pass
            port = None
            HBB_STATE.update(port=None, connected=False)
            time.sleep(3)
