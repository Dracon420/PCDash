"""Tolerant 3MF reader (BigTreeTech's file has an undeclared XML prefix)."""
import re, zipfile
import numpy as np, trimesh

def _mat(s):
    if not s: return np.eye(4)
    a = [float(x) for x in s.split()]
    m = np.eye(4); m[:3, :3] = np.array(a[:9]).reshape(3, 3).T; m[:3, 3] = a[9:12]
    return m.T.T if False else m

def load(path):
    txt = zipfile.ZipFile(path).read("3D/3dmodel.model").decode("utf-8", "replace")
    meshes, comps = {}, {}
    for attrs, body in re.findall(r'<object\b([^>]*)>(.*?)</object>', txt, re.S):
        oid = re.search(r'\bid="([^"]*)"', attrs).group(1)
        vs = re.findall(r'<vertex\s+x="([^"]+)"\s+y="([^"]+)"\s+z="([^"]+)"', body)
        if vs:
            ts = re.findall(r'<triangle\s+v1="(\d+)"\s+v2="(\d+)"\s+v3="(\d+)"', body)
            t = trimesh.Trimesh(np.array(vs, float), np.array(ts, int)); t.merge_vertices()
            meshes[oid] = t
        else:
            comps[oid] = [(c.group(1), _mat(c.group(2))) for c in re.finditer(r'<component\b[^>]*objectid="(\d+)"(?:[^>]*transform="([^"]*)")?', body)]
    items = [(m.group(1), _mat(m.group(2))) for m in re.finditer(r'<item\b[^>]*objectid="(\d+)"(?:[^>]*transform="([^"]*)")?', txt)]
    return meshes, comps, items

def assembly(meshes, comps, cid):
    parts = []
    for oid, m in comps[cid]:
        t = meshes[oid].copy(); t.apply_transform(m); parts.append((oid, t))
    return parts
