# Quick software renders (no OpenGL): top-down height map + side silhouette for each cap.
import glob, os, sys
import numpy as np, trimesh
from PIL import Image, ImageDraw

def heightmap(m, res=12, top=True):
    b = m.bounds
    W, H = int((b[1][0]-b[0][0])*res)+1, int((b[1][1]-b[0][1])*res)+1
    img = np.full((H, W), np.nan)
    pts, _ = trimesh.sample.sample_surface(m, 200000, seed=1)
    xs = ((pts[:,0]-b[0][0])*res).astype(int); ys = ((pts[:,1]-b[0][1])*res).astype(int)
    for x, y, z in zip(xs, ys, pts[:,2]):
        cur = img[H-1-y, x]
        if np.isnan(cur) or (z > cur if top else z < cur): img[H-1-y, x] = z
    return img, b

files = sorted(glob.glob("original/*.STL"))
tiles = []
for p in files:
    m = trimesh.load(p, force="mesh")
    hm, b = heightmap(m)
    zmin, zmax = b[0][2], b[1][2]
    norm = np.nan_to_num((hm - zmin) / (zmax - zmin), nan=0)
    im = Image.fromarray((norm*255).astype(np.uint8)).resize((216, 246), Image.NEAREST).convert("RGB")
    bot, _ = heightmap(m, top=False)
    normb = np.nan_to_num((bot - zmin) / (zmax - zmin), nan=1)
    imb = Image.fromarray(((1-normb)*255).astype(np.uint8)).resize((216, 246), Image.NEAREST).convert("RGB")
    t = Image.new("RGB", (440, 270), (20, 20, 28)); t.paste(im, (2, 20)); t.paste(imb, (222, 20))
    ImageDraw.Draw(t).text((4, 4), f"{os.path.basename(p)}  top (bright=high) | bottom", fill=(220,220,220))
    tiles.append(t)
sheet = Image.new("RGB", (3*444, ((len(tiles)+2)//3)*274), (0,0,0))
for i, t in enumerate(tiles): sheet.paste(t, ((i%3)*444, (i//3)*274))
sheet.save("caps_sheet.png"); print("ok")
m = trimesh.load("original/HOME.STL", force="mesh")
z = np.round(m.vertices[:,2], 2); u, c = np.unique(z, return_counts=True)
print("HOME vertex z-levels (count):", dict(zip(u[c>20], c[c>20])))
