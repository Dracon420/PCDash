import glob, os, trimesh, numpy as np
for p in sorted(glob.glob("original/*.STL")):
    m = trimesh.load(p, force="mesh")
    b = m.bounds; e = b[1] - b[0]
    print(f"{os.path.basename(p):15s} tris {len(m.faces):6d}  size {e[0]:6.2f} x {e[1]:6.2f} x {e[2]:6.2f}  min {np.round(b[0],2)}  watertight {m.is_watertight}")
