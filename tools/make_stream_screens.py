"""Stream screens in the K-Touch neon style, from the game art on this PC (2026-11-12).

    python tools\\make_stream_screens.py

-> stream-screens\\brb.png      OBS "Be Right Back" scene background (1920x1080)
-> stream-screens\\offline.png  Twitch offline / video-player banner (1920x1080)

Game pictures come from games.json via game_art.source_path (the games' own
key art, already on the PC). Re-run after adding a game; newest game is last.
"""
from __future__ import annotations

import json
import math
import os
import random
import sys

import numpy as np
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
import game_art  # noqa: E402

OUT = os.path.join(HERE, "stream-screens")
W, H = 1920, 1080
FONT = r"C:\Windows\Fonts\AGENCYB.TTF"
CYAN = (0, 240, 255)
VIOLET = (177, 76, 255)
PINK = (255, 42, 109)
YELLOW = (252, 238, 10)


def _mix(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def games():
    with open(os.path.join(HERE, "games.json"), encoding="utf-8-sig") as fh:
        gs = json.load(fh)["games"]
    out = []
    for g in gs:
        try:
            p = game_art.source_path(g)
        except Exception:  # noqa: BLE001
            p = None
        if p and os.path.isfile(p):
            out.append((g, Image.open(p).convert("RGB")))
    return out


# BRB strips: logo-free store art, cropped around the subject (fraction across).
FOCUS = {"ark": 0.42, "minecraft": 0.45, "oncehuman": 0.30, "palworld": 0.62, "subnautica2": 0.50, "halo": 0.55}


def strip_art(g, pic):
    p = os.path.join(HERE, "game-art", "store", g["id"], "superheroart.jpg")
    return Image.open(p).convert("RGB") if os.path.isfile(p) else pic


def cover(im, size, fx=0.5):
    w, h = size
    s = max(w / im.width, h / im.height)
    r = im.resize((round(im.width * s), round(im.height * s)), Image.LANCZOS)
    l = int(min(max(0, r.width * fx - w / 2), r.width - w))
    t = (r.height - h) // 2
    return r.crop((l, t, l + w, t + h))


def grade(im, dark=0.55, tint=(40, 10, 70), amount=0.35):
    """Darken and pull toward the theme violet so mixed art sits together."""
    im = ImageEnhance.Color(im).enhance(1.15)
    a = np.asarray(im).astype(np.float32) * dark
    a = a * (1 - amount) + np.array(tint, np.float32) * amount + a * amount * 0.6
    return Image.fromarray(a.clip(0, 255).astype(np.uint8))


def glow_layer(img, layer, radii=(12, 4)):
    for r in radii:
        g = layer.filter(ImageFilter.GaussianBlur(r))
        img.paste(g, (0, 0), g)
    img.paste(layer, (0, 0), layer)


def neon_text(img, xy, text, size, color, anchor="mm", spacing=0, core=True):
    f = ImageFont.truetype(FONT, size)
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    if spacing:
        # manual letter spacing, centred on xy
        widths = [d.textlength(c, font=f) for c in text]
        total = sum(widths) + spacing * (len(text) - 1)
        x = xy[0] - total / 2 if anchor[0] == "m" else xy[0]
        for c, wdt in zip(text, widths):
            d.text((x, xy[1]), c, font=f, fill=color + (255,), anchor="l" + anchor[1])
            x += wdt + spacing
    else:
        d.text(xy, text, font=f, fill=color + (255,), anchor=anchor)
    glow_layer(img, layer, (size * 0.18, size * 0.06))
    if core:
        c = layer.copy()
        a = c.getchannel("A").filter(ImageFilter.MinFilter(5 if size > 80 else 3))
        white = Image.new("RGBA", img.size, (255, 240, 248, 255))
        white.putalpha(a)
        img.paste(white, (0, 0), white)


def brackets(img, color, ln=70, ins=36, w=5):
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    for x, y, sx, sy in ((ins, ins, 1, 1), (W - ins, ins, -1, 1), (ins, H - ins, 1, -1), (W - ins, H - ins, -1, -1)):
        d.line([(x, y), (x + sx * ln, y)], fill=color + (255,), width=w)
        d.line([(x, y), (x, y + sy * ln)], fill=color + (255,), width=w)
    glow_layer(img, layer, (8,))


def scanlines(img, every=3, alpha=0.88):
    a = np.asarray(img).astype(np.float32)
    a[::every] *= alpha
    return Image.fromarray(a.clip(0, 255).astype(np.uint8))


def vignette(img, strength=0.55):
    y, x = np.ogrid[-1:1:H * 1j, -1:1:W * 1j]
    v = 1 - strength * np.clip((x ** 2 * 0.8 + y ** 2) - 0.25, 0, 1)
    a = np.asarray(img).astype(np.float32) * v[..., None]
    return Image.fromarray(a.clip(0, 255).astype(np.uint8))


def perspective_grid(img, horizon, color, alpha=170, fade_to=None):
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    for i in range(-30, 31):
        d.line([(W / 2 + i * 150, H + 200), (W / 2 + i * 9, horizon)], fill=color + (alpha,), width=2)
    for k in range(1, 16):
        y = horizon + (H - horizon) * (k / 15) ** 2.2
        d.line([(0, y), (W, y)], fill=color + (int(alpha * (0.35 + 0.65 * k / 15)),), width=2)
    a = np.asarray(layer.getchannel("A")).astype(np.float32)
    ramp = np.clip((np.arange(H) - horizon) / 90, 0, 1)[:, None]
    layer.putalpha(Image.fromarray((a * ramp).astype(np.uint8)))
    glow_layer(img, layer, (5, 2))


def sun(img, cx, horizon, r, alpha=1.0):
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    for y in range(int(horizon - r), int(horizon)):
        t = (y - (horizon - r)) / r
        if t > 0.45:
            period = r * 0.09
            if (y - (horizon - r)) % period < period * (0.12 + 0.5 * (t - 0.45) / 0.55):
                continue
        half = math.sqrt(max(0, r * r - (horizon - y) ** 2))
        d.line([(cx - half, y), (cx + half, y)], fill=_mix(YELLOW, PINK, t) + (int(255 * alpha),))
    glow_layer(img, layer, (40, 12))


def card(pic, size, angle, border, glow=True):
    cw, ch = size
    p = cover(pic, size).convert("RGBA")
    d = ImageDraw.Draw(p)
    d.rectangle([0, 0, cw - 1, ch - 1], outline=border + (255,), width=4)
    pad = 60
    canvas = Image.new("RGBA", (cw + pad * 2, ch + pad * 2), (0, 0, 0, 0))
    if glow:
        sh = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        ImageDraw.Draw(sh).rectangle([pad - 4, pad - 4, pad + cw + 4, pad + ch + 4], fill=border + (190,))
        sh = sh.filter(ImageFilter.GaussianBlur(22))
        canvas.paste(sh, (0, 0), sh)
    canvas.paste(p, (pad, pad), p)
    return canvas.rotate(angle, resample=Image.BICUBIC, expand=True)


# --------------------------------------------------------------------- BRB --
def brb(gs):
    img = Image.new("RGB", (W, H), (6, 3, 16))
    # Slanted strips of game art across the whole frame.
    # the newest game (last) gets the second-to-last slot: the edge slots are half off-screen
    gs = gs if len(gs) < 3 else gs[:-2] + [gs[-1], gs[-2]]
    n = len(gs)
    slant = 260
    sw = (W + slant) / n
    for i, (g, pic) in enumerate(gs):
        x0 = i * sw - slant / 2
        poly = [(x0 + slant / 2, 0), (x0 + sw + slant / 2, 0), (x0 + sw - slant / 2, H), (x0 - slant / 2, H)]
        xs = [p[0] for p in poly]
        bw = int(max(xs) - min(xs)) + 4
        # put the subject (FOCUS, fraction across the art) on the strip's centre line
        art = strip_art(g, pic)
        sc = max(H / art.height, bw / art.width)
        art = art.resize((round(art.width * sc), round(art.height * sc)), Image.LANCZOS)
        target = (x0 + sw / 2) - (min(xs) - 2)
        l = int(min(max(0, FOCUS.get(g["id"], 0.5) * art.width - target), art.width - bw))
        t = (art.height - H) // 2
        tile = grade(art.crop((l, t, l + bw, t + H)), dark=0.62, amount=0.30)
        m = Image.new("L", (W, H), 0)
        ImageDraw.Draw(m).polygon(poly, fill=255)
        full = Image.new("RGB", (W, H))
        full.paste(tile, (int(min(xs)) - 2, 0))
        img = Image.composite(full, img, m)
    # neon seams between strips
    seams = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    sd = ImageDraw.Draw(seams)
    for i in range(1, n):
        x0 = i * sw - slant / 2
        sd.line([(x0 + slant / 2, 0), (x0 - slant / 2, H)], fill=(CYAN if i % 2 else PINK) + (255,), width=4)
    glow_layer(img, seams, (10, 3))
    # dark band through the middle for the title
    a = np.asarray(img).astype(np.float32)
    yy = np.arange(H)[:, None]
    band = np.exp(-((yy - 520) / 230.0) ** 2) * 0.78
    a = a * (1 - band[..., None]) + np.array((8, 2, 20)) * band[..., None]
    img = Image.fromarray(a.clip(0, 255).astype(np.uint8))
    # thin rules above and below the title
    rl = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    rd = ImageDraw.Draw(rl)
    for y, c in ((372, CYAN), (672, PINK)):
        rd.line([(360, y), (W - 360, y)], fill=c + (255,), width=3)
        rd.polygon([(W / 2 - 10, y), (W / 2, y - 10), (W / 2 + 10, y), (W / 2, y + 10)], fill=c + (255,))
    glow_layer(img, rl, (6,))
    neon_text(img, (W / 2, 505), "BE RIGHT BACK", 200, PINK, spacing=14)
    neon_text(img, (W / 2, 625), "GRAB A SNACK  //  DON'T GO ANYWHERE", 52, CYAN, spacing=6, core=False)
    brackets(img, CYAN)
    img = vignette(img, 0.45)
    img = scanlines(img)
    return img


# ----------------------------------------------------------------- offline --
def offline(gs):
    horizon = 640
    # sky
    t = (np.arange(H)[:, None] / horizon).clip(0, 1) ** 1.5
    top, bot = np.array((8, 3, 26)), np.array((92, 14, 84))
    sky = top + (bot - top) * t
    a = np.repeat(sky[:, None, :], W, axis=1).reshape(H, W, 3)
    a[horizon:] = np.array((14, 3, 28))
    img = Image.fromarray(a.astype(np.uint8))
    # faint giant hero art in the sky (newest game), blended in
    hero = grade(cover(gs[-1][1], (W, H)), dark=0.9, amount=0.45).filter(ImageFilter.GaussianBlur(3))
    m = np.clip(1 - np.arange(H) / horizon, 0, 1) ** 0.8 * 0.38
    mask = Image.fromarray((np.repeat(m[:, None], W, axis=1) * 255).astype(np.uint8))
    img = Image.composite(hero, img, mask)
    # stars
    rnd = random.Random(5)
    d = ImageDraw.Draw(img)
    for _ in range(260):
        x, y = rnd.uniform(0, W), rnd.uniform(0, horizon - 120) * rnd.uniform(0.3, 1)
        r = rnd.choice((1, 1, 1.5, 2))
        b = int(160 + 95 * rnd.random())
        d.ellipse([x - r, y - r, x + r, y + r], fill=(b, b, min(255, b + 20)))
    sun(img, W / 2, horizon, 250)
    floor = Image.new("RGBA", (W, H - horizon), (14, 3, 28, 255))
    img.paste(floor, (0, horizon), floor)
    perspective_grid(img, horizon, PINK)
    hl = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(hl).line([(0, horizon), (W, horizon)], fill=(255, 150, 200, 255), width=4)
    glow_layer(img, hl, (14, 4))
    # game cards fanned in an arc over the horizon, newest in the middle
    order = [p for _, p in gs]
    n = len(order)
    cw, ch = 440, 248
    pos = []
    for i, pic in enumerate(order):
        t = (i - (n - 1) / 2) / ((n - 1) / 2)
        x = W / 2 + t * 700
        y = 790 - (1 - t * t) * 40
        pos.append((abs(t), i, pic, t, x, y))
    for _, i, pic, t, x, y in sorted(pos, key=lambda p: -p[0]):
        c = card(pic, (cw, ch), -t * 8, CYAN if (i % 2 == 0) else VIOLET)
        img.paste(c, (int(x - c.width / 2), int(y - c.height / 2)), c)
    # title
    neon_text(img, (W / 2, 150), "CURRENTLY OFFLINE", 150, CYAN, spacing=10)
    names = "   •   ".join(g["short"].upper() for g, _ in gs)
    neon_text(img, (W / 2, 262), "PROBABLY GAMING OR BUILDING SOMETHING  //  CATCH THE NEXT STREAM", 44, PINK, spacing=4, core=False)
    neon_text(img, (W / 2, 1010), names, 36, CYAN, spacing=3, core=False)
    brackets(img, VIOLET)
    img = vignette(img, 0.35)
    img = scanlines(img, every=3, alpha=0.92)
    return img


def main():
    gs = games()
    os.makedirs(OUT, exist_ok=True)
    brb(gs).save(os.path.join(OUT, "brb.png"))
    off = offline(gs)
    off.save(os.path.join(OUT, "offline.png"))
    off.save(os.path.join(OUT, "offline.jpg"), quality=92)      # Twitch's banner limit is 10 MB
    print("ok:", OUT)


if __name__ == "__main__":
    main()
