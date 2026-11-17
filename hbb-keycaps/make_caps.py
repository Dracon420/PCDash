"""Make HBB keycaps for the stream controller, from BigTreeTech's own design.

BigTreeTech's All-KEY.3mf stores each key as three solid parts (multi-colour):
    shell  18 x 20.47 x 7 mm  hex walls - identical on every key
    core   14.7 x 17 x 5.7    stem + body; its bottom 1 mm forms the SYMBOL
    plate  14.8 x 17 x 1      the face, with the symbol cut out
The face is at the bottom (keys print face-down). For each new key:
    plate = full face  - new symbol
    core  = old core with its old symbol plug removed  + new symbol plug
    shell = unchanged
so the symbol prints in the core's colour and the face in the plate's - exactly
like the originals. A single-colour version (symbol recessed 0.6 mm, for one
filament, or to paint) is written too.

Seen from the front, a face-down model is mirrored, so the artwork is mirrored
here and reads correctly on the finished key.

Output (out/):
    key<N>-<name>.3mf            3 parts - assign colours in the slicer
    key<N>-<name>-single.stl     one body, symbol recessed
    all-keys-multicolour.3mf     all 7, laid out on one plate
    preview.png                  the artwork
"""
from __future__ import annotations

import argparse
import os
import zipfile

import manifold3d as mf
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from shapely import affinity
from shapely.geometry import MultiPolygon, Polygon, box
from shapely.ops import unary_union

import read3mf

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "out")
SRC = os.path.join(HERE, "original", "All-KEY.3mf")
PX_PER_MM = 40
SINGLE_RECESS = 0.6
FONT_SYM = r"C:\Windows\Fonts\seguisym.ttf"

# Per-nozzle settings. The first 0.4 mm print (2026-11-11) came out with blobby
# letters: ~2 mm Segoe UI caps, fattened 0.13 mm a side, closed up R/E/B/M. The
# 0.2 mm profile uses Verdana Bold (made for tiny sizes: wide, open counters),
# bigger letters with tracking, a larger art box, hardly any fattening, and
# hand-drawn mic/camera icons instead of emoji glyphs.
PROFILES = {
    "0.4": dict(out="out", art_mm=11.0, grow=0.13, font=r"C:\Windows\Fonts\segoeuib.ttf",
                text_px=0.25, text_y=0.85, text_w=0.92, track=0.0, sym_box=(0.14, 0.02, 0.86, 0.64), drawn=False),
    "0.2": dict(out="out-0.2mm", art_mm=12.6, grow=0.03, font=r"C:\Windows\Fonts\verdanab.ttf",
                text_px=0.25, text_y=0.745, text_w=0.80, track=0.04, sym_box=(0.22, 0.04, 0.78, 0.53), drawn=True),
}
P = PROFILES["0.4"]

# Your key numbers (as bound in OBS, 2026-11-10/11), symbol, word underneath.
KEYS = [
    (1, "live", "live",        "LIVE"),
    (2, "rec",  "rec",         "REC"),
    (3, "clip", "\u2702",      "CLIP"),   # scissors
    (4, "mic",  "\U0001F3A4",  "MIC"),    # microphone
    (5, "cam",  "\U0001F4F7",  "CAM"),    # camera
    (6, "game", "\U0001F3AE",  "GAME"),   # game controller
    (7, "brb",  "\u2615",      "BRB"),    # hot drink
]


# ------------------------------------------------------------------ geometry --
def to_manifold(t) -> mf.Manifold:
    return mf.Manifold(mf.Mesh(vert_properties=np.asarray(t.vertices, np.float32),
                               tri_verts=np.asarray(t.faces, np.uint32)))


def mesh_arrays(m: mf.Manifold):
    mm = m.to_mesh()
    return np.asarray(mm.vert_properties)[:, :3], np.asarray(mm.tri_verts)


def polys(g) -> list[Polygon]:
    if g.is_empty:
        return []
    if isinstance(g, Polygon):
        return [g]
    return [p for p in getattr(g, "geoms", []) if isinstance(p, Polygon)]


def extrude(g, z0: float, z1: float) -> mf.Manifold:
    out = mf.Manifold()
    for p in polys(g):
        if p.area < 1e-4:
            continue
        rings = [np.asarray(p.exterior.coords)[:-1]] + [np.asarray(h.coords)[:-1] for h in p.interiors]
        cs = mf.CrossSection([r.tolist() for r in rings], mf.FillRule.EvenOdd)
        out = out + mf.Manifold.extrude(cs, z1 - z0).translate([0, 0, z0])
    return out


def slice_outline(m: mf.Manifold, z: float) -> Polygon:
    """Outer outline (holes filled) of a solid's cross-section at height z."""
    cs = m.slice(z)
    shapes = [Polygon(np.asarray(c)) for c in cs.to_polygons() if len(c) >= 3]
    shapes = [s.buffer(0) for s in shapes]
    return unary_union([Polygon(s.exterior) for s in shapes if not s.is_empty]).buffer(0)


# ------------------------------------------------------------------- artwork --
def draw_art(sym: str, word: str) -> Image.Image:
    n = int(P["art_mm"] * PX_PER_MM)
    img = Image.new("L", (n, n), 0)
    d = ImageDraw.Draw(img)
    sb = tuple(int(n * f) for f in P["sym_box"])                        # symbol box, upper part
    cx, cy = (sb[0] + sb[2]) // 2, (sb[1] + sb[3]) // 2
    r = (sb[3] - sb[1]) // 2
    if sym == "live":            # broadcast: dot with two arcs each side
        dot = int(r * 0.30)
        d.ellipse((cx - dot, cy - dot, cx + dot, cy + dot), fill=255)
        w = max(10, int(r * 0.16))
        for rr in (r * 0.62, r * 0.98):
            rr = int(rr)
            d.arc((cx - rr, cy - rr, cx + rr, cy + rr), 140, 220, fill=255, width=w)
            d.arc((cx - rr, cy - rr, cx + rr, cy + rr), -40, 40, fill=255, width=w)
    elif sym == "rec":           # ring with a filled dot
        w = max(10, int(r * 0.17))
        d.ellipse((cx - r * 0.92, cy - r * 0.92, cx + r * 0.92, cy + r * 0.92), outline=255, width=w)
        dot = int(r * 0.48)
        d.ellipse((cx - dot, cy - dot, cx + dot, cy + dot), fill=255)
    elif P["drawn"] and sym == "🎤":     # microphone: capsule, holder, stem, base
        w = max(10, int(r * 0.17))
        cw, ch = r * 0.42, r * 0.62
        d.rounded_rectangle((cx - cw, cy - r * 0.98, cx + cw, cy - r * 0.98 + 2 * ch), radius=int(cw), fill=255)
        hr = r * 0.70
        d.arc((cx - hr, cy - r * 0.98 + ch - hr * 0.55, cx + hr, cy - r * 0.98 + ch + hr * 1.25), 0, 180, fill=255, width=w)
        top = cy - r * 0.98 + ch + hr * 1.25
        d.rectangle((cx - w / 2, top - w / 2, cx + w / 2, cy + r * 0.86), fill=255)
        d.rounded_rectangle((cx - r * 0.50, cy + r * 0.80, cx + r * 0.50, cy + r * 0.80 + w), radius=w // 2, fill=255)
    elif P["drawn"] and sym == "📷":     # camera: body with a hollow lens
        bw, bh = r * 1.0, r * 0.66
        top = cy - bh + r * 0.12
        d.rounded_rectangle((cx - r * 0.42, top - r * 0.26, cx + r * 0.18, top + 6), radius=int(r * 0.08), fill=255)
        d.rounded_rectangle((cx - bw, top, cx + bw, top + 2 * bh), radius=int(r * 0.2), fill=255)
        lc = top + bh
        for rr, col in ((r * 0.52, 0), (r * 0.34, 255)):
            d.ellipse((cx - rr, lc - rr, cx + rr, lc + rr), fill=col)
        d.ellipse((cx + bw * 0.62, top + r * 0.12, cx + bw * 0.82, top + r * 0.32), fill=0)
    else:                        # a glyph from Segoe UI Symbol, scaled to the box
        size = int((sb[3] - sb[1]) * 1.05)
        font = ImageFont.truetype(FONT_SYM, size)
        bb = d.textbbox((0, 0), sym, font=font)
        scale = min((sb[2] - sb[0]) / (bb[2] - bb[0]), (sb[3] - sb[1]) / (bb[3] - bb[1]))
        font = ImageFont.truetype(FONT_SYM, int(size * scale * 0.98))
        bb = d.textbbox((0, 0), sym, font=font)
        d.text((cx - (bb[0] + bb[2]) / 2, cy - (bb[1] + bb[3]) / 2), sym, font=font, fill=255)
    draw_word(d, n, word)
    return img


def draw_word(d, n, word):
    """The label, letter by letter with extra spacing so gaps stay open when printed."""
    track = int(n * P["track"])
    def layout(size):
        f = ImageFont.truetype(P["font"], size)
        widths = [d.textlength(ch, font=f) for ch in word]
        return f, widths, sum(widths) + track * (len(word) - 1)
    size = int(n * P["text_px"])
    f, widths, total = layout(size)
    if total > n * P["text_w"]:
        f, widths, total = layout(int(size * n * P["text_w"] / total))
    bb = d.textbbox((0, 0), word, font=f)
    x, y = n / 2 - total / 2, n * P["text_y"] - (bb[1] + bb[3]) / 2
    for ch, w in zip(word, widths):
        d.text((x, y), ch, font=f, fill=255)
        x += w + track


def art_polygon(img: Image.Image):
    """Bitmap -> smooth polygons (merge pixel runs, close tiny gaps, simplify)."""
    a = np.asarray(img) > 127
    h, _ = a.shape
    px = 1.0 / PX_PER_MM
    rects = []
    for y in range(h):
        xs = np.flatnonzero(np.diff(np.concatenate([[0], a[y].astype(np.int8), [0]])))
        for x0, x1 in zip(xs[0::2], xs[1::2]):
            rects.append(box(x0 * px, (h - y - 1) * px, x1 * px, (h - y) * px))
    g = unary_union(rects).buffer(px * 0.7).buffer(-px * 0.7)
    # Fatten every stroke by MIN_GROW mm per side: the thinnest lines (mic cable,
    # steam) were ~0.2 mm, too thin for a 0.4 mm nozzle to lay down.
    return g.buffer(P["grow"], join_style=1).simplify(px * 0.35)


def place(art, face: Polygon):
    c = face.centroid
    b = art.bounds
    g = affinity.translate(art, c.x - (b[0] + b[2]) / 2, c.y - (b[1] + b[3]) / 2)
    g = affinity.scale(g, xfact=-1, yfact=1, origin=(c.x, c.y))   # mirrored: model is face-down
    # Keep a margin from the face edge, and drop specks too small to print.
    g = g.intersection(face.buffer(-1.0))
    return unary_union([p for p in polys(g) if p.area > 0.04])


# ------------------------------------------------------------------- 3MF out --
PART_FILAMENT = {"shell": 1, "face": 1, "symbol+core": 2}   # default colours: symbol stands out


def write_3mf(path: str, objects: list[tuple[str, list[tuple[str, mf.Manifold]]]], spacing=(24, 26), cols=4):
    """objects: [(object name, [(part name, manifold), ...]), ...]. One build item per object.

    Besides the standard 3MF, writes Metadata/model_settings.config - the part list
    Bambu Studio and Orca-based slicers (Elegoo Slicer) read. Without it they show
    each key as one lump whose pieces can't be given different filaments.
    """
    res, items, oid = [], [], 1
    cfg = []
    for k, (oname, parts) in enumerate(objects):
        comp_ids = []
        part_cfg = []
        for pname, m in parts:
            kind = pname.split(" ", 1)[-1]
            part_cfg.append(
                f'<part id="{oid}" subtype="normal_part"><metadata key="name" value="{kind}"/>'
                f'<metadata key="matrix" value="1 0 0 0 0 1 0 0 0 0 1 0 0 0 0 1"/>'
                f'<metadata key="extruder" value="{PART_FILAMENT.get(kind, 1)}"/></part>')
            v, f = mesh_arrays(m)
            vx = "".join(f'<vertex x="{x:.4f}" y="{y:.4f}" z="{z:.4f}"/>' for x, y, z in v)
            tx = "".join(f'<triangle v1="{a}" v2="{b}" v3="{c}"/>' for a, b, c in f)
            res.append(f'<object id="{oid}" type="model" name="{pname}"><mesh><vertices>{vx}</vertices><triangles>{tx}</triangles></mesh></object>')
            comp_ids.append(oid)
            oid += 1
        comps = "".join(f'<component objectid="{c}"/>' for c in comp_ids)
        res.append(f'<object id="{oid}" type="model" name="{oname}"><components>{comps}</components></object>')
        cfg.append(f'<object id="{oid}"><metadata key="name" value="{oname}"/><metadata key="extruder" value="1"/>'
                   f'{"".join(part_cfg)}</object>')
        x, y = (k % cols) * spacing[0], -(k // cols) * spacing[1]
        items.append(f'<item objectid="{oid}" transform="1 0 0 0 1 0 0 0 1 {x} {y} 0"/>')
        oid += 1
    model = ('<?xml version="1.0" encoding="UTF-8"?>'
             '<model unit="millimeter" xml:lang="en-US" xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">'
             f'<resources>{"".join(res)}</resources><build>{"".join(items)}</build></model>')
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml",
                   '<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                   '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                   '<Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/></Types>')
        z.writestr("_rels/.rels",
                   '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Target="/3D/3dmodel.model" Id="rel0" Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/></Relationships>')
        z.writestr("3D/3dmodel.model", model)
        z.writestr("Metadata/model_settings.config",
                   f'<?xml version="1.0" encoding="UTF-8"?>\n<config>{"".join(cfg)}</config>')


def write_stl(path: str, m: mf.Manifold):
    v, f = mesh_arrays(m)
    tri = v[f]
    n = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    n /= np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-12)
    data = np.zeros(len(f), dtype=[("n", "<f4", 3), ("v", "<f4", (3, 3)), ("a", "<u2")])
    data["n"], data["v"] = n, tri
    with open(path, "wb") as fh:
        fh.write(b"PCDash HBB keycap".ljust(80, b" "))
        fh.write(np.uint32(len(f)).tobytes())
        fh.write(data.tobytes())


# ---------------------------------------------------------------------- main --
def main():
    global OUT, P
    ap = argparse.ArgumentParser()
    ap.add_argument("--nozzle", choices=sorted(PROFILES), default="0.4")
    P = PROFILES[ap.parse_args().nozzle]
    OUT = os.path.join(HERE, P["out"])
    os.makedirs(OUT, exist_ok=True)
    meshes, comps, _ = read3mf.load(SRC)
    parts = [to_manifold(t) for _, t in read3mf.assembly(meshes, comps, "4")]   # the first key
    # Identify the three parts by size.
    def ext(m):
        b = m.bounding_box()
        return (b[3] - b[0], b[4] - b[1], b[5] - b[2])
    shell = next(m for m in parts if ext(m)[2] > 6.5)
    plate = next(m for m in parts if ext(m)[2] < 1.5)
    core = next(m for m in parts if 4 < ext(m)[2] < 6.5)
    z0 = shell.bounding_box()[2]
    face_top = plate.bounding_box()[5]
    print(f"shell vol {shell.volume():.1f}, core vol {core.volume():.1f}, plate vol {plate.volume():.1f}, face z {z0:.2f}..{face_top:.2f}")

    # The full face area = plate + the old symbol, i.e. the plate's outline with holes filled.
    face = slice_outline(plate, (z0 + face_top) / 2)
    face_slab = extrude(face, z0 - 0.01, face_top)
    core_blank = core - face_slab                    # old symbol plug gone

    multi, previews = [], []
    lift = -z0                                       # put the face on z = 0 for printing
    for num, name, sym, word in KEYS:
        img = draw_art(sym, word)
        art = place(art_polygon(img), face)
        plug = extrude(art, z0, face_top)
        new_plate = extrude(face, z0, face_top) - plug
        new_core = core_blank + plug
        sh, co, pl = (m.translate([0, 0, lift]) for m in (shell, new_core, new_plate))
        key_parts = [(f"key{num} shell", sh), (f"key{num} symbol+core", co), (f"key{num} face", pl)]
        write_3mf(os.path.join(OUT, f"key{num}-{name}.3mf"), [(f"key{num}-{name}", key_parts)])
        # Fallback that works in any slicer: the three parts as separate STLs.
        # Drag all three in together and answer "Yes" to loading them as one
        # object with multiple parts; then pick a filament per part.
        part_dir = os.path.join(OUT, "parts", f"key{num}-{name}")
        os.makedirs(part_dir, exist_ok=True)
        for pname, m in key_parts:
            write_stl(os.path.join(part_dir, f"key{num}-{name}-{pname.split(' ', 1)[-1].replace('+', '-')}.stl"), m)

        single = (sh + co + pl) - extrude(art, -0.01, SINGLE_RECESS)
        write_stl(os.path.join(OUT, f"key{num}-{name}-single.stl"), single)
        print(f"key{num}-{name}: core {co.volume():6.1f}  face {pl.volume():6.1f}  single {single.volume():6.1f} mm3"
              f"  ok={single.status() == mf.Error.NoError}")
        multi.append((f"key{num}-{name}", key_parts))
        previews.append((num, word, img))
    write_3mf(os.path.join(OUT, "all-keys-multicolour.3mf"), multi)
    faces_preview(multi, face, lift)

    sheet = Image.new("RGB", (len(previews) * 200 + 10, 236), (20, 22, 30))
    d = ImageDraw.Draw(sheet)
    for i, (num, word, img) in enumerate(previews):
        sheet.paste(Image.merge("RGB", [img.resize((190, 190))] * 3), (10 + i * 200, 10))
        d.text((10 + i * 200, 210), f"key {num}: {word}", fill=(220, 220, 220))
    sheet.save(os.path.join(OUT, "preview.png"))
    print("done ->", OUT)


def faces_preview(multi, face, lift):
    """How each finished key face looks from the front (un-mirrored): grey face, white symbol."""
    S = 24
    b = face.bounds
    W, H = int((b[2] - b[0]) * S) + 20, int((b[3] - b[1]) * S) + 20
    sheet = Image.new("RGB", (len(multi) * (W + 10) + 10, H + 20), (20, 22, 30))
    for i, (name, parts) in enumerate(multi):
        im = Image.new("RGB", (W, H), (0, 0, 0))
        d = ImageDraw.Draw(im)
        def draw(poly_list, colour):
            for p in poly_list:
                pts = [((b[2] - x) * S + 10, (b[3] - y) * S + 10) for x, y in p.exterior.coords]   # x flipped back
                d.polygon(pts, fill=colour)
                for h in p.interiors:
                    d.polygon([((b[2] - x) * S + 10, (b[3] - y) * S + 10) for x, y in h.coords], fill=(0, 0, 0))
        # to_polygons() returns outer rings AND hole rings separately: combine them
        # even-odd (symmetric difference) so the symbol cut-outs stay holes.
        rings = [Polygon(np.asarray(c)).buffer(0) for c in parts[2][1].slice(0.5).to_polygons() if len(c) >= 3]
        plate_poly = Polygon()
        for r in rings:
            plate_poly = plate_poly.symmetric_difference(r)
        draw(polys(face), (240, 240, 240))
        draw(polys(plate_poly), (70, 74, 86))
        sheet.paste(im, (10 + i * (W + 10), 10))
    sheet.save(os.path.join(OUT, "faces.png"))


if __name__ == "__main__":
    main()
