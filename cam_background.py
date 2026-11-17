"""Webcam background that follows the game being played (user's idea, 2026-11-11).

The webcam in OBS has NVIDIA Background Removal on it, so the room behind the
user is transparent, and an image source called "Cam Background" sits right
behind it. This thread watches which game is running and points that image at
a matching picture:

    backgrounds\\<game id>.jpg|png|webp   your own picture, used exactly as is
    game-art\\<game id>.jpg|png|webp      the K-Touch tile art: a square is cut
                                          from the middle, softened and darkened
                                          so you stand out against it
    backgrounds\\default.*                when no listed game is running
    (generated)                           a plain dark theme gradient otherwise

Which process belongs to which game is in backgrounds.json ("match": pieces of
the program's path). Prepared pictures are cached in cache\\cam-bg\\.
"""
from __future__ import annotations

import json
import os
import time

import psutil
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter

import obs

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG = os.path.join(HERE, "backgrounds.json")
OWN_DIR = os.path.join(HERE, "backgrounds")
ART_DIR = os.path.join(HERE, "game-art")
CACHE = os.path.join(HERE, "cache", "cam-bg")
SOURCE = "Cam Background"
EXTS = (".jpg", ".jpeg", ".png", ".webp")
SIZE = 1080                      # square, the webcam crop is square


def load() -> dict:
    try:
        with open(CONFIG, encoding="utf-8-sig") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {"enabled": False}


def running_game(games: dict) -> str | None:
    """First game in backgrounds.json whose path pieces match a running process."""
    paths = []
    for p in psutil.process_iter(["exe"]):
        exe = (p.info.get("exe") or "").lower()
        if exe:
            paths.append(exe)
    for gid, g in games.items():
        pieces = [m.lower() for m in g.get("match", [])]
        if any(m in exe for exe in paths for m in pieces):
            return gid
    return None


def _find(folder: str, name: str) -> str | None:
    for ext in EXTS:
        p = os.path.join(folder, name + ext)
        if os.path.isfile(p):
            return p
    return None


def _prepared(src: str, key: str) -> str:
    """Square, softened, slightly darker copy of a picture (cached)."""
    os.makedirs(CACHE, exist_ok=True)
    out = os.path.join(CACHE, f"{key}.jpg")
    if os.path.isfile(out) and os.path.getmtime(out) >= os.path.getmtime(src):
        return out
    im = Image.open(src).convert("RGB")
    w, h = im.size
    s = min(w, h)
    im = im.crop(((w - s) // 2, (h - s) // 2, (w - s) // 2 + s, (h - s) // 2 + s)).resize((SIZE, SIZE), Image.LANCZOS)
    im = im.filter(ImageFilter.GaussianBlur(4))
    im = ImageEnhance.Brightness(im).enhance(0.78)
    im.save(out, quality=90)
    return out


def _gradient() -> str:
    """Fallback: a dark cyan-to-violet gradient in the PCDash theme."""
    os.makedirs(CACHE, exist_ok=True)
    out = os.path.join(CACHE, "_gradient.jpg")
    if os.path.isfile(out):
        return out
    im = Image.new("RGB", (SIZE, SIZE))
    d = ImageDraw.Draw(im)
    for y in range(SIZE):
        t = y / (SIZE - 1)
        d.line([(0, y), (SIZE, y)], fill=(int(4 + 40 * t), int(30 * (1 - t) + 8), int(40 + 50 * t)))
    im.save(out, quality=92)
    return out


def picture_for(gid: str | None) -> str:
    if gid:
        own = _find(OWN_DIR, gid)
        if own:
            return own
        art = _find(ART_DIR, gid)
        if art:
            return _prepared(art, gid)
    own = _find(OWN_DIR, "default")
    return own or _gradient()


def run(log=lambda m: None) -> None:
    cfg = load()
    if not cfg.get("enabled"):
        log("cam-bg: off (backgrounds.json)")
        return
    os.makedirs(OWN_DIR, exist_ok=True)
    current = None
    log("cam-bg: following the running game")
    while True:
        try:
            cfg = load()                                   # edits apply without a restart
            pic = picture_for(running_game(cfg.get("games", {})))
            if pic != current and obs.CLIENT.connected:
                obs.CLIENT.call("SetInputSettings", {"inputName": cfg.get("source", SOURCE),
                                                     "inputSettings": {"file": pic}})
                log(f"cam-bg: {os.path.basename(pic)}")
                current = pic
            elif not obs.CLIENT.connected:
                current = None                             # re-apply once OBS is back
        except obs.OBSError as exc:
            if "No source was found" not in str(exc):
                log(f"cam-bg: {exc}")
            current = None
        except Exception as exc:  # noqa: BLE001 - never take PCDash down
            log(f"cam-bg: {exc}")
        time.sleep(3)
