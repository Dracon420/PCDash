"""AFK scene artwork: every game's key art as one cinematic panorama, no text (2026-11-12).

    python tools\\make_afk.py   -> stream-screens\\afk.png (1920x1080)

Panels blend into each other along soft diagonal seams, each centred on its
subject (FOCUS in make_stream_screens), with a faint neon line on each seam.
Uses the logo-free store art; Halo has none, so its splash is zoomed onto
Master Chief's helmet, above the logo.
"""
from __future__ import annotations

import os
import sys

import numpy as np
from PIL import Image, ImageEnhance, ImageFilter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import make_stream_screens as ms  # noqa: E402

W, H = 1920, 1080
FEATHER = 110          # px of soft blend across each seam
SLANT = 300            # how far each seam leans, top to bottom
# zoom, vertical position (0 = top): keeps a baked-in logo out of the shot
CROP = {"halo": (2.3, 0.0)}
ORDER = ["ark", "palworld", "halo", "minecraft", "oncehuman", "subnautica2"]


def panel(g, pic, centre, width):
    art = ms.strip_art(g, pic)
    zoom, fy = CROP.get(g["id"], (1.0, 0.5))
    sc = max(H / art.height, width / art.width) * zoom
    art = art.resize((round(art.width * sc), round(art.height * sc)), Image.LANCZOS)
    left = centre - width / 2
    l = int(min(max(0, ms.FOCUS.get(g["id"], 0.5) * art.width - width / 2), art.width - width))
    t = int((art.height - H) * fy)
    crop = art.crop((l, t, l + width, t + H))
    crop = ImageEnhance.Color(crop).enhance(1.12)
    crop = ImageEnhance.Contrast(crop).enhance(1.06)
    full = np.zeros((H, W, 3), np.float32)
    x0 = int(round(left))
    a, b = max(0, x0), min(W, x0 + width)
    full[:, a:b] = np.asarray(crop, np.float32)[:, a - x0:b - x0]
    return full


def main():
    by_id = {g["id"]: (g, p) for g, p in ms.games()}
    order = [by_id[i] for i in ORDER if i in by_id] + [v for k, v in by_id.items() if k not in ORDER]
    n = len(order)
    step = W / n
    yy = np.arange(H, dtype=np.float32)[:, None]
    xx = np.arange(W, dtype=np.float32)[None, :]
    out = None
    seams = []
    for i, (g, pic) in enumerate(order):
        width = int(step + SLANT + FEATHER * 2 + 40)
        img = panel(g, pic, (i + 0.5) * step, width)
        if out is None:
            out = img
            continue
        bx = i * step + SLANT * (0.5 - yy / H)          # seam leans like the BRB strips
        alpha = np.clip((xx - bx) / FEATHER + 0.5, 0, 1)
        alpha = alpha * alpha * (3 - 2 * alpha)          # smoothstep
        out = out * (1 - alpha[..., None]) + img * alpha[..., None]
        seams.append(i * step)

    # cinematic grade: gentle violet lift in the shadows, darker top and bottom
    lum = out.mean(axis=2, keepdims=True) / 255
    out = out + (1 - lum) * np.array((14, 4, 26), np.float32)
    edge = 1 - 0.45 * np.clip((np.abs(yy - H / 2) / (H / 2) - 0.55) / 0.45, 0, 1) ** 1.5
    out = out * edge[..., None]
    img = Image.fromarray(out.clip(0, 255).astype(np.uint8))

    # faint neon light along each seam
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    from PIL import ImageDraw
    d = ImageDraw.Draw(layer)
    for k, x in enumerate(seams):
        c = ms.CYAN if k % 2 == 0 else ms.PINK
        d.line([(x + SLANT / 2, 0), (x - SLANT / 2, H)], fill=c + (120,), width=2)
    for r in (14, 4):
        g = layer.filter(ImageFilter.GaussianBlur(r))
        img.paste(g, (0, 0), g)
    img.paste(layer, (0, 0), layer)
    img = ms.vignette(img, 0.32)
    os.makedirs(ms.OUT, exist_ok=True)
    img.save(os.path.join(ms.OUT, "afk.png"))
    print("ok")


if __name__ == "__main__":
    main()
