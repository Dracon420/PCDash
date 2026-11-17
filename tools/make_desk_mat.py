"""Print-ready mouse pad / desk mat in the K-Touch HUD style (2026-11-12).

    python tools\\make_desk_mat.py            -> F:\\DeskMat\\*.png (300 DPI)
    python tools\\make_desk_mat.py --preview  -> small previews only

Same palette, Agency FB neon lettering, sliced synthwave sun and pink
perspective grid as the K-Touch Games folder, with wireframe mountains, stars,
a faint hex grid and HUD corner brackets. No game art, so it prints anywhere.

Each size is drawn with 3 mm bleed on every side (print shops trim it off);
text and brackets stay 12 mm inside the trim so a stitched edge can't eat them.
"""
from __future__ import annotations

import math
import os
import random
import sys

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

FONT = r"C:\Windows\Fonts\AGENCYB.TTF"
OUT = r"F:\DeskMat"
BLEED = 3.0          # mm
SAFE = 12.0          # mm inside the trim

CYAN = (0, 240, 255)
VIOLET = (177, 76, 255)
PINK = (255, 42, 109)
YELLOW = (252, 238, 10)

# name: (width mm, height mm)
SIZES = {
    "desk-mat-900x400": (900, 400),
    "mouse-pad-250x210": (250, 210),
}
WORDMARK = "DRACON"
TAGLINE = "PC1  //  DRACONCLUSTER"


def _mix(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def _glow_paste(img: Image.Image, layer: Image.Image, radii):
    for r in radii:
        g = layer.filter(ImageFilter.GaussianBlur(r))
        img.paste(g, (0, 0), g)
    img.paste(layer, (0, 0), layer)


def render(w_mm: float, h_mm: float, dpi: int, seed: int = 7) -> Image.Image:
    S = dpi / 25.4                                   # px per mm
    W, H = round((w_mm + 2 * BLEED) * S), round((h_mm + 2 * BLEED) * S)
    mm = lambda v: v * S                             # noqa: E731
    trim = mm(BLEED)                                 # trim line offset
    rnd = random.Random(seed)
    wide = w_mm / h_mm > 1.6
    horizon = round(H * (0.56 if wide else 0.52))

    # Sky: deep violet to hot pink at the horizon. Floor: near-black violet.
    t = np.linspace(0, 1, horizon)[:, None] ** 1.6
    top, bot = np.array((8, 3, 24)), np.array((96, 14, 82))
    sky = (top + (bot - top) * t).astype(np.uint8)
    arr = np.zeros((H, W, 3), np.uint8)
    arr[:horizon] = sky[:, None, :]
    ft = np.linspace(0, 1, H - horizon)[:, None]
    ftop, fbot = np.array((30, 6, 46)), np.array((6, 2, 14))
    arr[horizon:] = (ftop + (fbot - ftop) * ft ** 0.5).astype(np.uint8)[:, None, :]
    img = Image.fromarray(arr, "RGB")

    # Stars, denser and brighter higher up.
    d = ImageDraw.Draw(img)
    for _ in range(int(w_mm * h_mm / 170)):
        x, y = rnd.uniform(0, W), rnd.uniform(0, horizon) * rnd.uniform(0.2, 1)
        r = mm(rnd.choice((0.25, 0.3, 0.4, 0.6)))
        b = int(255 * (1 - y / horizon) ** 0.7)
        c = _mix((b, b, b), CYAN if rnd.random() < 0.15 else (255, 255, 255), 0.3)
        d.ellipse([x - r, y - r, x + r, y + r], fill=c)

    # Faint hex grid in the upper sky, fading toward the sun.
    hexl = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    hd = ImageDraw.Draw(hexl)
    size = mm(9)
    dx, dy = size * 1.732, size * 1.5
    y, row = -size, 0
    while y < horizon:
        x = -dx + (dx / 2 if row % 2 else 0)
        while x < W + dx:
            edge = min(1.0, abs(x - W / 2) / (W * 0.45))
            a = int(70 * edge * (1 - max(0, y) / horizon))
            if a > 3:
                pts = [(x + size * math.cos(math.radians(k)), y + size * math.sin(math.radians(k)))
                       for k in range(30, 390, 60)]
                hd.line(pts, fill=CYAN + (a,), width=max(1, round(mm(0.25))))
            x += dx
        y += dy
        row += 1
    img.paste(hexl, (0, 0), hexl)

    # Sliced sun sitting on the horizon.
    sun_r = H * (0.36 if wide else 0.30)
    cx = W / 2
    sun = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    sd = ImageDraw.Draw(sun)
    y0 = int(horizon - sun_r)
    for y in range(y0, horizon):
        tt = (y - y0) / sun_r
        half = math.sqrt(max(0.0, sun_r ** 2 - (horizon - y) ** 2))
        # Gaps widen toward the bottom, like the K-Touch tile.
        if tt > 0.42:
            band = (tt - 0.42) / 0.58
            period = sun_r * 0.085
            gap = period * (0.12 + 0.55 * band)
            if (y - y0) % period < gap:
                continue
        sd.line([(cx - half, y), (cx + half, y)], fill=_mix(YELLOW, PINK, tt ** 0.9) + (255,))
    halo = sun.filter(ImageFilter.GaussianBlur(mm(14)))
    img.paste(halo, (0, 0), halo)
    halo = sun.filter(ImageFilter.GaussianBlur(mm(4)))
    img.paste(halo, (0, 0), halo)
    img.paste(sun, (0, 0), sun)

    # Wireframe mountains either side of the sun.
    def ridge(x_from, x_to, peak_h, n=7):
        pts = [(x_from, horizon)]
        for i in range(1, n):
            x = x_from + (x_to - x_from) * i / n + rnd.uniform(-1, 1) * (x_to - x_from) / n * 0.3
            hgt = peak_h * rnd.uniform(0.35, 1.0) * math.sin(math.pi * i / n) ** 0.6
            pts.append((x, horizon - hgt))
        pts.append((x_to, horizon))
        return pts

    mts = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    md = ImageDraw.Draw(mts)
    lw = max(2, round(mm(0.5)))
    gap = sun_r * 1.15
    ranges = [(-W * 0.02, cx - gap, H * 0.22), (cx + gap, W * 1.02, H * 0.22)]
    if wide:
        ranges += [(W * 0.10, cx - gap * 0.55, H * 0.13), (cx + gap * 0.55, W * 0.90, H * 0.13)]
    for x_from, x_to, ph in ranges:
        pts = ridge(x_from, x_to, ph, 8 if wide else 5)
        md.polygon(pts, fill=(16, 4, 34, 255))
        for (px, py) in pts[1:-1]:                    # wireframe struts down to the base
            for fx in (-0.5, 0.0, 0.5):
                md.line([(px, py), (px + fx * (x_to - x_from) / 8, horizon)],
                        fill=VIOLET + (110,), width=max(1, lw // 2))
        md.line(pts, fill=CYAN + (255,), width=lw, joint="curve")
    _glow_paste(img, mts, (mm(2.5),))

    # Pink perspective grid on the floor.
    grid = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    gd = ImageDraw.Draw(grid)
    step = mm(36)
    n = int(W / step) + 30
    for i in range(-n, n + 1):
        gd.line([(cx + i * step, H + mm(40)), (cx + i * step * 0.06, horizon)],
                fill=PINK + (170,), width=lw)
    rows = 15 if wide else 11
    for k in range(1, rows + 1):
        yy = horizon + (H - horizon) * (k / rows) ** 2.1
        gd.line([(0, yy), (W, yy)], fill=PINK + (int(70 + 150 * k / rows),), width=lw)
    # Fade the grid out just below the horizon so the far lines don't moire.
    fy = np.clip((np.arange(H) - horizon) / (H * 0.10), 0, 1) ** 1.5
    ga = np.asarray(grid.getchannel("A")).astype(np.float32) * fy[:, None]
    grid.putalpha(Image.fromarray(ga.astype(np.uint8), "L"))
    _glow_paste(img, grid, (mm(3), mm(1)))
    hl = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(hl).line([(0, horizon), (W, horizon)], fill=(255, 140, 190, 255), width=lw * 2)
    _glow_paste(img, hl, (mm(4), mm(1.2)))

    # Gentle vignette into the floor edges so the mouse area stays calm.
    vy = np.linspace(0, 1, H)[:, None]
    vx = np.abs(np.linspace(-1, 1, W))[None, :]
    shade = np.clip((vy - 0.80) / 0.20, 0, 1) * 0.55 + (vx ** 6) * 0.25
    a = np.asarray(img).astype(np.float32) * (1 - np.clip(shade, 0, 0.7))[..., None]
    # Scanlines: one dark line per 1.2 mm, very light so it reads as texture.
    period = max(3, round(mm(1.2)))
    a[::period] *= 0.90
    img = Image.fromarray(a.clip(0, 255).astype(np.uint8), "RGB")

    # HUD corner brackets, inside the safe area.
    br = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    bd = ImageDraw.Draw(br)
    ins = trim + mm(SAFE * 0.6)
    ln = mm(26 if wide else 16)
    bw = max(2, round(mm(0.9)))
    for x, y, sx, sy in ((ins, ins, 1, 1), (W - ins, ins, -1, 1), (ins, H - ins, 1, -1), (W - ins, H - ins, -1, -1)):
        bd.line([(x, y), (x + sx * ln, y)], fill=CYAN + (255,), width=bw)
        bd.line([(x, y), (x, y + sy * ln)], fill=CYAN + (255,), width=bw)
    _glow_paste(img, br, (mm(2),))

    # Neon wordmark, top-left, inside the safe area.
    fs = round(mm(30 if wide else 22))
    f = ImageFont.truetype(FONT, fs)
    tx, ty = trim + mm(SAFE + 6), trim + mm(SAFE + 4)
    tl = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(tl).text((tx, ty), WORDMARK, font=f, fill=PINK + (255,), anchor="lt")
    small = ImageFont.truetype(FONT, round(fs * 0.28))
    bbox = ImageDraw.Draw(tl).textbbox((tx, ty), WORDMARK, font=f, anchor="lt")
    ImageDraw.Draw(tl).text((tx + mm(1), bbox[3] + mm(3)), TAGLINE, font=small, fill=CYAN + (255,), anchor="lt")
    _glow_paste(img, tl, (mm(5), mm(1.5)))
    core = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(core).text((tx, ty), WORDMARK, font=f, fill=(255, 235, 245, 255), anchor="lt")
    core = core.filter(ImageFilter.MinFilter(max(3, round(mm(0.9)) | 1)))
    img.paste(core, (0, 0), core)
    return img


def main() -> None:
    preview = "--preview" in sys.argv
    os.makedirs(OUT, exist_ok=True)
    for name, (w, h) in SIZES.items():
        if preview:
            render(w, h, 40).save(os.path.join(OUT, name + "-preview.png"))
        else:
            img = render(w, h, 300)
            img.save(os.path.join(OUT, name + "-300dpi.png"), dpi=(300, 300), optimize=False)
            img.convert("RGB").save(os.path.join(OUT, name + "-300dpi.jpg"), quality=95, dpi=(300, 300))
            img.resize((img.width // 8, img.height // 8), Image.LANCZOS).save(os.path.join(OUT, name + "-preview.png"))
        print(name, "ok")


if __name__ == "__main__":
    main()
