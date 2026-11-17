"""Builds the K-Touch tile art that isn't a game's own picture (2026-11-12).

    python make_tile_art.py

1. App tiles (246x160) -> game-art\\<app id>.png
   Each app's own icon from Windows (tools\\app_icon.ps1, via its Start-menu
   AppID), on a dark HUD panel lit in the app's colour from games.json. An
   icon placed in game-art\\icons\\<id>.png is used instead (Bambu Studio's
   exe only carries a tiny icon, so its 154 px .ico is there).
2. Folder tiles (384x340) -> game-art\\_folder_games.png / _folder_apps.png
   Games: the game pictures fanned out over a neon perspective grid.
   Apps: the app icons floating over a hex grid.
   Both carry their title; the K-Touch adds only the count.

Everything is drawn here from pictures already on this PC - nothing is
downloaded. Re-run after adding a game or app; PCDash re-sends changed tiles.
Any file can be replaced by hand: game_art prefers game-art\\<id>.* over all else.
"""
from __future__ import annotations

import json
import math
import os
import subprocess

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
ART = os.path.join(HERE, "game-art")
ICONS = os.path.join(ART, "icons")
TILE = (246, 160)
FOLDER = (384, 340)
FONT = r"C:\Windows\Fonts\AGENCYB.TTF"
NO_WINDOW = 0x08000000

# The HUD palette (same as the K-Touch firmware and the Onn's theme.css).
BG = (4, 5, 9)
CYAN = (0, 240, 255)
VIOLET = (177, 76, 255)
PINK = (255, 42, 109)
YELLOW = (252, 238, 10)


def _hex(c: str) -> tuple:
    c = c.lstrip("#")
    return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))


def _mix(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def _font(size):
    return ImageFont.truetype(FONT, size)


def icon_for(app: dict) -> Image.Image | None:
    os.makedirs(ICONS, exist_ok=True)
    path = os.path.join(ICONS, app["id"] + ".png")
    if not os.path.isfile(path) and app.get("appid"):
        subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                        os.path.join(HERE, "tools", "app_icon.ps1"), "-AppId", app["appid"], "-Out", path],
                       capture_output=True, timeout=60, creationflags=NO_WINDOW)
    if not os.path.isfile(path):
        return None
    im = Image.open(path).convert("RGBA")
    box = im.getchannel("A").point(lambda a: 255 if a > 8 else 0).getbbox()
    return im.crop(box) if box else im


def _fit(im: Image.Image, size: int) -> Image.Image:
    s = size / max(im.size)
    return im.resize((max(1, round(im.width * s)), max(1, round(im.height * s))), Image.LANCZOS)


def _glow(im: Image.Image, color, radius: int, strength: float = 1.0) -> Image.Image:
    """A soft coloured halo the shape of `im`'s alpha, same size + padding."""
    pad = radius * 3
    a = Image.new("L", (im.width + pad * 2, im.height + pad * 2), 0)
    a.paste(im.getchannel("A"), (pad, pad))
    a = a.filter(ImageFilter.GaussianBlur(radius)).point(lambda v: min(255, int(v * strength)))
    g = Image.new("RGBA", a.size, color + (0,))
    g.putalpha(a)
    return g


def _scanlines(img: Image.Image, every=3, alpha=26):
    d = ImageDraw.Draw(img, "RGBA")
    for y in range(0, img.height, every):
        d.line([(0, y), (img.width, y)], fill=(0, 0, 0, alpha))


def _brackets(img: Image.Image, color, length=18, inset=6, width=2):
    d = ImageDraw.Draw(img)
    w, h = img.size
    for x, y, dx, dy in ((inset, inset, 1, 1), (w - 1 - inset, inset, -1, 1),
                         (inset, h - 1 - inset, 1, -1), (w - 1 - inset, h - 1 - inset, -1, -1)):
        d.line([(x, y), (x + dx * length, y)], fill=color, width=width)
        d.line([(x, y), (x, y + dy * length)], fill=color, width=width)


# ------------------------------------------------------------- app tiles --
def app_tile(app: dict) -> Image.Image | None:
    icon = icon_for(app)
    if icon is None:
        return None
    w, h = TILE
    col = _hex(app.get("color", "#00f0ff"))
    lit = _mix(col, (255, 255, 255), 0.25) if sum(col) < 150 else col   # Steam's navy needs lifting
    img = Image.new("RGB", TILE, BG)
    # Radial light in the app's colour behind where the icon sits.
    cx, cy = int(w * 0.70), int(h * 0.44)
    light = Image.new("L", TILE, 0)
    ImageDraw.Draw(light).ellipse([cx - 120, cy - 110, cx + 120, cy + 110], fill=150)
    light = light.filter(ImageFilter.GaussianBlur(34))
    img = Image.composite(Image.new("RGB", TILE, _mix(BG, lit, 0.85)), img, light)
    # Faint diagonal hatching, HUD style.
    d = ImageDraw.Draw(img, "RGBA")
    for x in range(-h, w, 14):
        d.line([(x, h), (x + h, 0)], fill=lit + (18,), width=1)
    _scanlines(img)
    # Icon with its glow.
    ic = _fit(icon, 92)
    halo = _glow(ic, lit, 10, 1.4)
    img.paste(halo, (cx - halo.width // 2, cy - halo.height // 2), halo)
    img.paste(ic, (cx - ic.width // 2, cy - ic.height // 2), ic)
    _brackets(img, lit)
    # A thin accent rule along the top, like the dashboard cards.
    d = ImageDraw.Draw(img)
    d.line([(0, 0), (w, 0)], fill=lit, width=2)
    return img


# ----------------------------------------------------------- folder tiles --
def _neon_text(img: Image.Image, text: str, xy, size: int, color, anchor="ls"):
    f = _font(size)
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(layer).text(xy, text, font=f, fill=color + (255,), anchor=anchor)
    halo = layer.filter(ImageFilter.GaussianBlur(9))
    halo2 = layer.filter(ImageFilter.GaussianBlur(3))
    img.paste(halo, (0, 0), halo)
    img.paste(halo2, (0, 0), halo2)
    core = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(core).text(xy, text, font=f, fill=(255, 255, 255, 255), anchor=anchor)
    # White core, tinted edge: reads as a lit neon tube.
    img.paste(layer, (0, 0), layer)
    shrink = core.filter(ImageFilter.MinFilter(3))
    img.paste(shrink, (0, 0), shrink)


def _perspective_grid(img: Image.Image, horizon: int, color, vanish_x=None):
    w, h = img.size
    vx = vanish_x if vanish_x is not None else w // 2
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    for i in range(-14, 15):                         # lines running to the vanishing point
        d.line([(vx + i * 60, h), (vx, horizon)], fill=color + (150,), width=1)
    for k in range(1, 14):                           # horizontals, closer together toward the horizon
        y = horizon + (h - horizon) * (k / 13) ** 2.2
        d.line([(0, y), (w, y)], fill=color + (int(60 + 120 * k / 13),), width=1)
    glow = layer.filter(ImageFilter.GaussianBlur(2))
    img.paste(glow, (0, 0), glow)
    img.paste(layer, (0, 0), layer)


def _sky(size, top, bottom):
    w, h = size
    img = Image.new("RGB", size)
    d = ImageDraw.Draw(img)
    for y in range(h):
        d.line([(0, y), (w, y)], fill=_mix(top, bottom, y / max(1, h - 1)))
    return img


def _card(pic: Image.Image, size, angle: float, border) -> Image.Image:
    """A game picture as a tilted, neon-edged card with a drop glow."""
    cw, ch = size
    s = max(cw / pic.width, ch / pic.height)
    p = pic.resize((round(pic.width * s), round(pic.height * s)), Image.LANCZOS)
    l, t = (p.width - cw) // 2, (p.height - ch) // 2
    p = p.crop((l, t, l + cw, t + ch)).convert("RGBA")
    d = ImageDraw.Draw(p)
    d.rectangle([0, 0, cw - 1, ch - 1], outline=border + (255,), width=2)
    pad = 24
    canvas = Image.new("RGBA", (cw + pad * 2, ch + pad * 2), (0, 0, 0, 0))
    shadow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rectangle([pad, pad, pad + cw, pad + ch], fill=border + (150,))
    shadow = shadow.filter(ImageFilter.GaussianBlur(10))
    canvas.paste(shadow, (0, 0), shadow)
    canvas.paste(p, (pad, pad), p)
    return canvas.rotate(angle, resample=Image.BICUBIC, expand=True)


def folder_games(games: list) -> Image.Image:
    import game_art
    w, h = FOLDER
    img = _sky(FOLDER, (12, 4, 30), (70, 10, 70))
    horizon = 150
    # Synthwave sun, sliced.
    sun = Image.new("RGBA", FOLDER, (0, 0, 0, 0))
    sd = ImageDraw.Draw(sun)
    r = 78
    for y in range(horizon - r, horizon):
        t = (y - (horizon - r)) / r
        half = math.sqrt(max(0, r * r - (horizon - y) ** 2))
        if int(t * 12) % 2 == 1 and t > 0.45:
            continue                                  # the slices
        sd.line([(w // 2 - half, y), (w // 2 + half, y)], fill=_mix(YELLOW, PINK, t) + (255,))
    halo = sun.filter(ImageFilter.GaussianBlur(14))
    img.paste(halo, (0, 0), halo)
    img.paste(sun, (0, 0), sun)
    floor = Image.new("RGBA", (w, h - horizon), (8, 2, 20, 255))
    img.paste(floor, (0, horizon), floor)
    _perspective_grid(img, horizon, PINK)
    ImageDraw.Draw(img).line([(0, horizon), (w, horizon)], fill=PINK, width=2)
    # Game pictures fanned across the middle.
    pics = []
    for g in games:
        try:
            src = game_art.source_path(g)
        except Exception:  # noqa: BLE001
            src = None
        if src and os.path.isfile(src):
            pics.append(Image.open(src).convert("RGB"))
    # The newest game (last in games.json) is the hero card in the middle.
    hero = pics[-1] if pics else None
    others = pics[:-1][:4]
    order = others[: len(others) // 2] + ([hero] if hero else []) + others[len(others) // 2:]
    n = len(order)
    if n:
        spread = 112
        for i, pic in enumerate(order):
            t = (i - (n - 1) / 2) / max(1, (n - 1) / 2)    # -1 .. 1
            card = _card(pic, (128, 84), -t * 12, CYAN if i % 2 else VIOLET)
            x = int(w / 2 + t * spread - card.width / 2)
            y = int(196 - card.height / 2 + abs(t) * 14)
            img.paste(card, (x, y), card)
        # The middle one again on top, so the centre card is the hero.
        card = _card(hero or order[n // 2], (168, 110), 0, CYAN)
        img.paste(card, (w // 2 - card.width // 2, 190 - card.height // 2), card)
    _scanlines(img)
    shade = Image.new("L", FOLDER, 0)
    sd = ImageDraw.Draw(shade)
    for y in range(262, h):
        sd.line([(0, y), (w, y)], fill=int(200 * (y - 262) / (h - 262)))
    img = Image.composite(Image.new("RGB", FOLDER, (0, 0, 0)), img, shade)
    _neon_text(img, "GAMES", (20, h - 46), 76, PINK)
    _brackets(img, VIOLET, 26, 8, 3)
    return img


def _hex_grid(img: Image.Image, color, size=26):
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    w, h = img.size
    dx, dy = size * 1.732, size * 1.5
    row = 0
    y = -size
    while y < h + size:
        x0 = (dx / 2) if row % 2 else 0
        x = -dx + x0
        while x < w + dx:
            pts = [(x + size * math.cos(math.radians(a)), y + size * math.sin(math.radians(a)))
                   for a in range(30, 390, 60)]
            fade = int(110 * max(0.0, 1 - abs(y - h * 0.42) / (h * 0.7)))
            d.line(pts, fill=color + (fade,), width=1)
            x += dx
        y += dy
        row += 1
    img.paste(layer, (0, 0), layer)


def folder_apps(apps: list) -> Image.Image:
    w, h = FOLDER
    img = _sky(FOLDER, (2, 14, 26), (4, 5, 9))
    glow = Image.new("L", FOLDER, 0)
    ImageDraw.Draw(glow).ellipse([w // 2 - 190, 10, w // 2 + 190, 290], fill=120)
    glow = glow.filter(ImageFilter.GaussianBlur(50))
    img = Image.composite(Image.new("RGB", FOLDER, (0, 60, 80)), img, glow)
    _hex_grid(img, CYAN)
    icons = [i for i in (icon_for(a) for a in apps) if i is not None]
    # Orbit: icons on an ellipse around a bright core, each lit in cyan.
    cx, cy = w // 2, 140
    core = Image.new("RGBA", FOLDER, (0, 0, 0, 0))
    ImageDraw.Draw(core).ellipse([cx - 120, cy - 66, cx + 120, cy + 66], outline=CYAN + (200,), width=2)
    ImageDraw.Draw(core).ellipse([cx - 150, cy - 86, cx + 150, cy + 86], outline=VIOLET + (120,), width=1)
    halo = core.filter(ImageFilter.GaussianBlur(4))
    img.paste(halo, (0, 0), halo)
    img.paste(core, (0, 0), core)
    n = len(icons)
    for i, ic in enumerate(icons):
        a = math.radians(-90 + 360 * i / max(1, n))
        x = cx + 120 * math.cos(a)
        y = cy + 66 * math.sin(a)
        size = 58 + int(14 * (math.sin(a) + 1) / 2)    # nearer (lower) ones bigger
        small = _fit(ic, size)
        g = _glow(small, CYAN, 7, 1.2)
        img.paste(g, (int(x - g.width / 2), int(y - g.height / 2)), g)
        img.paste(small, (int(x - small.width / 2), int(y - small.height / 2)), small)
    _scanlines(img)
    shade = Image.new("L", FOLDER, 0)
    sd = ImageDraw.Draw(shade)
    for y in range(240, h):
        sd.line([(0, y), (w, y)], fill=int(200 * (y - 240) / (h - 240)))
    img = Image.composite(Image.new("RGB", FOLDER, (0, 0, 0)), img, shade)
    _neon_text(img, "APPS", (20, h - 46), 76, CYAN)
    _brackets(img, CYAN, 26, 8, 3)
    return img


def main() -> None:
    with open(os.path.join(HERE, "games.json"), encoding="utf-8-sig") as fh:
        j = json.load(fh)
    os.makedirs(ART, exist_ok=True)
    for app in j.get("apps", []):
        img = app_tile(app)
        if img is None:
            print(app["id"], "- no icon found, stays a colour tile")
            continue
        img.save(os.path.join(ART, app["id"] + ".png"))
        print(app["id"], "ok")
    folder_games(j.get("games", [])).save(os.path.join(ART, "_folder_games.png"))
    folder_apps(j.get("apps", [])).save(os.path.join(ART, "_folder_apps.png"))
    print("folders ok")


if __name__ == "__main__":
    main()
