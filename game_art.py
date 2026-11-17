"""Game tile art for the K-Touch's Games tab.

Source, in order of preference:
  1. games.json "image": a picture file you choose (any size, any format Pillow reads)
  2. game-art\\<id>.jpg / .png / .webp next to this file - drop any picture in
  3. the game's own splash screen (1920x1080) from its Xbox/Store package manifest
     (ARK and Palworld's are just the Unreal Engine logo, Minecraft's is Mojang's,
     so those three have pictures in game-art\\ instead)

Each tile is cropped to fill 246x160 (centre crop, never stretched), shaded
darker toward the bottom so the white name stays readable, and converted to
the panel's native RGB565 little-endian. Results are cached in cache\\tiles\\.
"""
from __future__ import annotations

import functools
import json
import os
import subprocess
import xml.etree.ElementTree as ET

from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "cache", "tiles")
TILE_W, TILE_H = 246, 160
FOLDER_W, FOLDER_H = 384, 340     # the two folder tiles on the K-Touch Apps tab (make_tile_art.py)
NO_WINDOW = 0x08000000


@functools.lru_cache(maxsize=64)
def _install_location(pfn: str) -> str | None:
    """Where a Store/Xbox package lives on disk (Get-AppxPackage)."""
    ps = (f"(Get-AppxPackage | Where-Object PackageFamilyName -eq '{pfn}' | "
          f"Select-Object -First 1).InstallLocation")
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True,
                             text=True, timeout=30, creationflags=NO_WINDOW).stdout.strip()
        return out or None
    except (OSError, subprocess.SubprocessError):
        return None


def _splash_path(appid: str) -> str | None:
    pfn, _, app_id = appid.partition("!")
    loc = _install_location(pfn)
    if not loc:
        return None
    try:
        root = ET.parse(os.path.join(loc, "AppxManifest.xml")).getroot()
    except (OSError, ET.ParseError):
        return None
    # Namespaced attributes vary between manifest versions; just look for any
    # SplashScreen / Wide310x150Logo / Square150x150Logo "Image"/attribute.
    wanted = []
    for el in root.iter():
        tag = el.tag.split("}")[-1]
        if tag == "SplashScreen" and el.get("Image"):
            wanted.append(el.get("Image"))
        if tag == "DefaultTile" and el.get("Wide310x150Logo"):
            wanted.append(el.get("Wide310x150Logo"))
    for rel in wanted:
        base, ext = os.path.splitext(os.path.join(loc, rel))
        folder, stem = os.path.dirname(base), os.path.basename(base)
        try:
            files = [f for f in os.listdir(folder) if f.lower().startswith(stem.lower()) and f.lower().endswith(ext.lower())]
        except OSError:
            continue
        if files:   # biggest scale variant
            return max((os.path.join(folder, f) for f in files), key=os.path.getsize)
    return None


ART_DIR = os.path.join(HERE, "game-art")


def _art_override(game_id: str) -> str | None:
    for ext in (".jpg", ".jpeg", ".png", ".webp"):
        p = os.path.join(ART_DIR, game_id + ext)
        if os.path.isfile(p):
            return p
    return None


def source_path(game: dict) -> str | None:
    if game.get("folder"):
        p = os.path.join(ART_DIR, f"_folder_{game['folder']}.png")
        return p if os.path.isfile(p) else None
    # Only Store/Xbox packages ("Family!App") have a manifest splash; desktop
    # apps' Start-menu ids ("{GUID}\path.exe") would just cost a PowerShell call.
    appid = game.get("appid") or ""
    return game.get("image") or _art_override(game["id"]) or ("!" in appid and _splash_path(appid)) or None


def tile_image(game: dict) -> Image.Image | None:
    src = source_path(game)
    if not src or not os.path.isfile(src):
        return None
    img = Image.open(src).convert("RGB")
    if game.get("folder"):            # folder art is drawn finished, at its own size
        return _cover(img, FOLDER_W, FOLDER_H)
    # Cover-crop: scale so the tile is filled, crop the middle.
    scale = max(TILE_W / img.width, TILE_H / img.height)
    img = img.resize((max(TILE_W, round(img.width * scale)), max(TILE_H, round(img.height * scale))), Image.LANCZOS)
    left, top = (img.width - TILE_W) // 2, (img.height - TILE_H) // 2
    img = img.crop((left, top, left + TILE_W, top + TILE_H))
    # Darken the bottom 45% progressively so the name reads on any artwork.
    shade = Image.new("L", (TILE_W, TILE_H), 0)
    d = ImageDraw.Draw(shade)
    start = int(TILE_H * 0.55)
    for y in range(start, TILE_H):
        d.line([(0, y), (TILE_W, y)], fill=int(200 * (y - start) / (TILE_H - start)))
    img = Image.composite(Image.new("RGB", img.size, (0, 0, 0)), img, shade)
    return img


def _cover(img: Image.Image, w: int, h: int) -> Image.Image:
    s = max(w / img.width, h / img.height)
    img = img.resize((max(w, round(img.width * s)), max(h, round(img.height * s))), Image.LANCZOS)
    left, top = (img.width - w) // 2, (img.height - h) // 2
    return img.crop((left, top, left + w, top + h))


def to_rgb565(img: Image.Image) -> bytes:
    out = bytearray(img.width * img.height * 2)
    px = img.load()
    i = 0
    for y in range(img.height):
        for x in range(img.width):
            r, g, b = px[x, y]
            v = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
            out[i] = v & 0xFF
            out[i + 1] = v >> 8
            i += 2
    return bytes(out)


def tile_key(game: dict) -> str | None:
    """Changes whenever the game's source picture changes (path or modified time)."""
    src = source_path(game)
    if not src or not os.path.isfile(src):
        return None
    return f"{src}|{os.path.getmtime(src)}"


def tile_bytes(game: dict) -> bytes | None:
    """RGB565 tile for one game, built once and cached on disk."""
    os.makedirs(CACHE, exist_ok=True)
    src = source_path(game)
    if not src or not os.path.isfile(src):
        return None
    # Cache key follows the source file and its modification time, so swapping
    # the picture in game-art\ rebuilds the tile (Python's hash() is per-run).
    import hashlib
    stamp = f"{src}|{os.path.getmtime(src)}".encode()
    key = f"{game['id']}-{hashlib.sha1(stamp).hexdigest()[:10]}"
    path = os.path.join(CACHE, key + ".rgb565")
    if os.path.isfile(path):
        with open(path, "rb") as fh:
            return fh.read()
    img = tile_image(game)
    if img is None:
        return None
    img.save(os.path.join(CACHE, game["id"] + ".png"))   # human-viewable copy
    data = to_rgb565(img)
    with open(path, "wb") as fh:
        fh.write(data)
    return data


if __name__ == "__main__":
    # Preview: build every tile and make a contact sheet in cache\tiles\preview.png
    with open(os.path.join(HERE, "games.json"), encoding="utf-8-sig") as fh:
        games = json.load(fh)["games"]
    sheet = Image.new("RGB", (3 * (TILE_W + 12) + 12, 2 * (TILE_H + 12) + 12), (11, 14, 20))
    for i, g in enumerate(games[:6]):
        img = tile_image(g)
        print(g["id"], "OK" if img else "no art found")
        if img:
            img.save(os.path.join(CACHE, g["id"] + ".png")) if os.path.isdir(CACHE) else None
            sheet.paste(img, (12 + (i % 3) * (TILE_W + 12), 12 + (i // 3) * (TILE_H + 12)))
    os.makedirs(CACHE, exist_ok=True)
    sheet.save(os.path.join(CACHE, "preview.png"))
    print("preview:", os.path.join(CACHE, "preview.png"))
