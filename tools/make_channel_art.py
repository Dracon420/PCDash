"""Twitch channel art in the same neon style as the stream screens (2026-11-12).

    python tools\\make_channel_art.py

-> stream-screens\\profile-banner.png  1200x480 (Twitch profile banner)
-> stream-screens\\profile-pic.png     800x800  (Twitch shows it as a circle)

Reuses make_stream_screens' helpers; they read the module's W/H, so each
picture sets them first.
"""
from __future__ import annotations

import math
import os
import random
import sys

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import make_stream_screens as ms  # noqa: E402

NAME = "DRACON"
TAGLINE = "GAMING  //  BUILDING  //  SERVERS"
# banner strips: (zoom, vertical position 0=top) to keep a logo out of the shot
BANNER_CROP = {"halo": (2.7, 0.0)}


def banner(gs):
    W, H = ms.W, ms.H = 1200, 480
    img = Image.new("RGB", (W, H), (6, 3, 16))
    # slanted game-art strips, same as the BRB screen
    order = gs if len(gs) < 3 else gs[:-2] + [gs[-1], gs[-2]]
    n = len(order)
    slant = 140
    sw = (W + slant) / n
    for i, (g, pic) in enumerate(order):
        x0 = i * sw - slant / 2
        poly = [(x0 + slant / 2, 0), (x0 + sw + slant / 2, 0), (x0 + sw - slant / 2, H), (x0 - slant / 2, H)]
        xs = [p[0] for p in poly]
        bw = int(max(xs) - min(xs)) + 4
        art = ms.strip_art(g, pic)
        zoom, fy = BANNER_CROP.get(g["id"], (1.0, 0.5))
        sc = max(H / art.height, bw / art.width) * zoom
        art = art.resize((round(art.width * sc), round(art.height * sc)), Image.LANCZOS)
        target = (x0 + sw / 2) - (min(xs) - 2)
        l = int(min(max(0, ms.FOCUS.get(g["id"], 0.5) * art.width - target), art.width - bw))
        t = int((art.height - H) * fy)
        tile = ms.grade(art.crop((l, t, l + bw, t + H)), dark=0.62, amount=0.30)
        m = Image.new("L", (W, H), 0)
        ImageDraw.Draw(m).polygon(poly, fill=255)
        full = Image.new("RGB", (W, H))
        full.paste(tile, (int(min(xs)) - 2, 0))
        img = Image.composite(full, img, m)
    seams = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    sd = ImageDraw.Draw(seams)
    for i in range(1, n):
        x0 = i * sw - slant / 2
        sd.line([(x0 + slant / 2, 0), (x0 - slant / 2, H)], fill=(ms.CYAN if i % 2 else ms.PINK) + (255,), width=3)
    ms.glow_layer(img, seams, (7, 2))
    # dark centre band, sun behind the name
    a = np.asarray(img).astype(np.float32)
    yy = np.arange(H)[:, None]
    xx = np.arange(W)[None, :]
    band = np.exp(-((yy - 240) / 150.0) ** 2) * np.exp(-((xx - W / 2) / 520.0) ** 2) * 0.85
    a = a * (1 - band[..., None]) + np.array((8, 2, 20)) * band[..., None]
    img = Image.fromarray(a.clip(0, 255).astype(np.uint8))
    ms.sun(img, W / 2, 300, 130, alpha=0.55)
    rl = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(rl).line([(W / 2 - 330, 300), (W / 2 + 330, 300)], fill=ms.PINK + (255,), width=3)
    ms.glow_layer(img, rl, (6,))
    ms.neon_text(img, (W / 2, 225), NAME, 150, ms.PINK, spacing=18)
    ms.neon_text(img, (W / 2, 345), TAGLINE, 34, ms.CYAN, spacing=5, core=False)
    ms.brackets(img, ms.CYAN, ln=40, ins=18, w=3)
    img = ms.vignette(img, 0.35)
    return ms.scanlines(img, every=3, alpha=0.9)


def profile_pic():
    W, H = ms.W, ms.H = 800, 800
    horizon = 500
    t = (np.arange(H)[:, None] / horizon).clip(0, 1) ** 1.5
    top, bot = np.array((8, 3, 26)), np.array((100, 16, 90))
    a = np.repeat((top + (bot - top) * t)[:, None, :], W, axis=1).reshape(H, W, 3)
    a[horizon:] = np.array((14, 3, 28))
    img = Image.fromarray(a.astype(np.uint8))
    rnd = random.Random(3)
    d = ImageDraw.Draw(img)
    for _ in range(90):
        x, y = rnd.uniform(0, W), rnd.uniform(0, horizon - 60) * rnd.uniform(0.3, 1)
        r = rnd.choice((1.5, 2, 2.5))
        b = int(170 + 85 * rnd.random())
        d.ellipse([x - r, y - r, x + r, y + r], fill=(b, b, min(255, b + 20)))
    ms.sun(img, W / 2, horizon, 230)
    floor = Image.new("RGBA", (W, H - horizon), (14, 3, 28, 255))
    img.paste(floor, (0, horizon), floor)
    # grid scaled to this canvas
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    gd = ImageDraw.Draw(layer)
    for i in range(-14, 15):
        gd.line([(W / 2 + i * 110, H + 100), (W / 2 + i * 9, horizon)], fill=ms.PINK + (190,), width=3)
    for k in range(1, 9):
        y = horizon + (H - horizon) * (k / 8) ** 2.0
        gd.line([(0, y), (W, y)], fill=ms.PINK + (int(80 + 140 * k / 8),), width=3)
    ga = np.asarray(layer.getchannel("A")).astype(np.float32) * np.clip((np.arange(H) - horizon) / 40, 0, 1)[:, None]
    layer.putalpha(Image.fromarray(ga.astype(np.uint8)))
    ms.glow_layer(img, layer, (5, 2))
    hl = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(hl).line([(0, horizon), (W, horizon)], fill=(255, 150, 200, 255), width=5)
    ms.glow_layer(img, hl, (12, 4))
    # hexagon HUD frame with the D monogram, inside the circle Twitch crops to
    cx, cy, R = W / 2, 400, 250
    hexl = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    hd = ImageDraw.Draw(hexl)
    pts = [(cx + R * math.cos(math.radians(a)), cy + R * math.sin(math.radians(a))) for a in range(-90, 270, 60)]
    fill = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(fill).polygon(pts, fill=(8, 2, 22, 70))
    img.paste(fill, (0, 0), fill)
    hd.line(pts + [pts[0]], fill=ms.CYAN + (255,), width=8, joint="curve")
    r2 = R - 26
    pts2 = [(cx + r2 * math.cos(math.radians(a)), cy + r2 * math.sin(math.radians(a))) for a in range(-90, 270, 60)]
    hd.line(pts2 + [pts2[0]], fill=ms.VIOLET + (200,), width=3, joint="curve")
    ms.glow_layer(img, hexl, (16, 5))
    ms.neon_text(img, (cx, cy - 34), "D", 330, ms.PINK)
    ms.neon_text(img, (cx, cy + 142), NAME, 54, ms.CYAN, spacing=9, core=False)
    img = ms.vignette(img, 0.3)
    return ms.scanlines(img, every=3, alpha=0.92)


def main():
    gs = ms.games()
    os.makedirs(ms.OUT, exist_ok=True)
    banner(gs).save(os.path.join(ms.OUT, "profile-banner.png"))
    pp = profile_pic()
    pp.save(os.path.join(ms.OUT, "profile-pic.png"))
    # circle preview of how Twitch shows it
    m = Image.new("L", pp.size, 0)
    ImageDraw.Draw(m).ellipse([0, 0, pp.width - 1, pp.height - 1], fill=255)
    prev = Image.new("RGBA", pp.size, (0, 0, 0, 0))
    prev.paste(pp, (0, 0), m)
    prev.save(os.path.join(ms.OUT, "profile-pic-circle-preview.png"))
    print("ok:", ms.OUT)


if __name__ == "__main__":
    main()
