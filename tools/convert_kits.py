#!/usr/bin/env python3
"""Convert CC0 .glb kits into the compact JSON the World loads.

Run over an extracted pack:

    python3 tools/convert_kits.py /tmp/pack/rts   rts
    python3 tools/convert_kits.py /tmp/pack/nature nature

**Why not glTFLoader.** These kits are stylised low-poly with, at most, one
diffuse texture per material. Vendoring GLTFLoader plus BufferGeometryUtils and
SkeletonUtils is ~190 KB of machinery for skinning, morph targets, Draco and
KTX2 that none of these files use. The reader below is the subset that matters:
positions, UVs, indices and a base colour, with the scene graph's transforms
baked in.

**Why not Blender for this part.** Blender is the right tool for authoring and
for fixing a model's origin, but 170 models through an import/export round trip
is slow and hard to re-run. This is a pure function from .glb to .json.

**Textures.** A pack ships the same 1024px atlas inside every model that uses
it, which is why the nature kit is 81 MB for 68 objects. Unique images are
written once, normal maps are dropped (the look is flat-shaded; they would only
add weight), and the rest are downscaled with `sips`.

glTF is Y-up and so is three.js, so unlike the Blender path there is no axis
conversion here.
"""
from __future__ import annotations

import base64
import hashlib
import json
import struct
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "thecmanager" / "static" / "models"
TEX = OUT / "tex"

# Component type -> (struct char, byte size)
CT = {5120: ("b", 1), 5121: ("B", 1), 5122: ("h", 2),
      5123: ("H", 2), 5125: ("I", 4), 5126: ("f", 4)}
NCOMP = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}

TEX_SIZE = 256          # plenty for a stylised atlas seen from 10 metres


def read_glb(path: Path):
    d = path.read_bytes()
    if d[:4] != b"glTF":
        raise ValueError(f"{path.name}: not a GLB")
    jlen = struct.unpack("<I", d[12:16])[0]
    j = json.loads(d[20:20 + jlen].decode("utf-8", "replace"))
    off = 20 + jlen
    blob = b""
    if len(d) > off:
        blen = struct.unpack("<I", d[off:off + 4])[0]
        blob = d[off + 8:off + 8 + blen]
    return j, blob


def buffer_bytes(j, blob, index):
    buf = j["buffers"][index]
    uri = buf.get("uri")
    if uri is None:
        return blob
    if uri.startswith("data:"):
        return base64.b64decode(uri.split(",", 1)[1])
    raise ValueError("external .bin files are not supported")


def accessor(j, blob, idx):
    """Read one accessor into a flat list. Handles byteStride."""
    acc = j["accessors"][idx]
    n = NCOMP[acc["type"]]
    fmt, size = CT[acc["componentType"]]
    count = acc["count"]
    if "bufferView" not in acc:
        return [0] * (count * n)
    bv = j["bufferViews"][acc["bufferView"]]
    data = buffer_bytes(j, blob, bv.get("buffer", 0))
    start = bv.get("byteOffset", 0) + acc.get("byteOffset", 0)
    stride = bv.get("byteStride") or (size * n)
    out = []
    for i in range(count):
        o = start + i * stride
        out.extend(struct.unpack_from("<" + fmt * n, data, o))
    return out


def node_matrix(node):
    """A node's local transform as a 4x4 row-major list."""
    if "matrix" in node:
        m = node["matrix"]              # glTF matrices are column-major
        return [[m[0], m[4], m[8], m[12]],
                [m[1], m[5], m[9], m[13]],
                [m[2], m[6], m[10], m[14]],
                [m[3], m[7], m[11], m[15]]]
    t = node.get("translation", [0, 0, 0])
    r = node.get("rotation", [0, 0, 0, 1])
    s = node.get("scale", [1, 1, 1])
    x, y, z, w = r
    rm = [
        [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
        [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
        [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
    ]
    return [[rm[i][k] * s[k] for k in range(3)] + [t[i]] for i in range(3)] + \
           [[0, 0, 0, 1]]


def mat_mul(a, b):
    return [[sum(a[i][k] * b[k][j] for k in range(4)) for j in range(4)]
            for i in range(4)]


def apply(m, p):
    return (m[0][0] * p[0] + m[0][1] * p[1] + m[0][2] * p[2] + m[0][3],
            m[1][0] * p[0] + m[1][1] * p[1] + m[1][2] * p[2] + m[1][3],
            m[2][0] * p[0] + m[2][1] * p[1] + m[2][2] * p[2] + m[2][3])


IDENT = [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]]


def save_textures(j, blob, wanted):
    """Write unique diffuse images, downscaled. Returns image index -> filename."""
    TEX.mkdir(parents=True, exist_ok=True)
    out = {}
    for i, im in enumerate(j.get("images", [])):
        if i not in wanted:
            continue
        name = (im.get("name") or "").lower()
        # Drop normal maps, which flat shading has no use for. Matching on the
        # word alone threw away Bark_NormalTree, the bark of a tree species
        # actually called "Normal Tree", and those trees then rendered white.
        # Only the _Normal suffix marks a normal map.
        stem = name.rsplit(".", 1)[0]
        if stem.endswith("_normal") or stem.endswith("normalmap"):
            continue
        if "bufferView" not in im:
            continue
        bv = j["bufferViews"][im["bufferView"]]
        data = buffer_bytes(j, blob, bv.get("buffer", 0))
        raw = data[bv.get("byteOffset", 0):bv.get("byteOffset", 0) + bv["byteLength"]]
        h = hashlib.sha1(raw).hexdigest()[:10]
        dest = TEX / f"{h}.png"
        if not dest.exists():
            dest.write_bytes(raw)
            subprocess.run(["sips", "-Z", str(TEX_SIZE), str(dest)],
                           capture_output=True)
        out[i] = dest.name
    return out


def convert(path: Path):
    """One .glb -> a list of parts, normalised to stand on the origin."""
    j, blob = read_glb(path)
    mats = j.get("materials", [])

    # Which image does each material sample?
    def tex_image(mi):
        if mi is None or mi >= len(mats):
            return None
        pbr = mats[mi].get("pbrMetallicRoughness", {})
        t = pbr.get("baseColorTexture")
        if not t:
            return None
        tex = j.get("textures", [])[t["index"]]
        return tex.get("source")

    wanted = {img for mi in range(len(mats)) if (img := tex_image(mi)) is not None}
    images = save_textures(j, blob, wanted)

    # Walk the scene graph so node transforms are baked into the vertices.
    groups = {}
    def walk(ni, parent):
        node = j["nodes"][ni]
        world = mat_mul(parent, node_matrix(node))
        if "mesh" in node:
            for prim in j["meshes"][node["mesh"]].get("primitives", []):
                attrs = prim.get("attributes", {})
                if "POSITION" not in attrs:
                    continue
                pos = accessor(j, blob, attrs["POSITION"])
                uv = accessor(j, blob, attrs["TEXCOORD_0"]) if "TEXCOORD_0" in attrs else None
                idx = (accessor(j, blob, prim["indices"]) if "indices" in prim
                       else list(range(len(pos) // 3)))
                mi = prim.get("material")
                g = groups.setdefault(mi, {"pos": [], "uv": [], "idx": [], "map": {}})
                g["map"] = {}          # indices are per-primitive
                # Indexed, not expanded: a cube is 8 vertices and 36 indices,
                # not 36 vertices. Across a 68-model kit that is the difference
                # between a few megabytes and tens of them.
                base = g["map"]
                for k in idx:
                    key = k
                    j2 = base.get(key)
                    if j2 is None:
                        pt = apply(world, (pos[k * 3], pos[k * 3 + 1], pos[k * 3 + 2]))
                        j2 = len(g["pos"]) // 3
                        g["pos"] += [pt[0], pt[1], pt[2]]
                        if uv:
                            g["uv"] += [uv[k * 2], uv[k * 2 + 1]]
                        base[key] = j2
                    g["idx"].append(j2)
        for c in node.get("children", []):
            walk(c, world)

    scene = j.get("scenes", [{}])[j.get("scene", 0)]
    for ni in scene.get("nodes", []):
        walk(ni, IDENT)
    if not groups:
        return []

    # Normalise: stand on y=0, centred, largest dimension 1.0. The World scales
    # each prop to the size it wants, so a kit's arbitrary units stop mattering.
    allp = [v for g in groups.values() for v in g["pos"]]
    xs, ys, zs = allp[0::3], allp[1::3], allp[2::3]
    cx = (min(xs) + max(xs)) / 2
    cz = (min(zs) + max(zs)) / 2
    fy = min(ys)
    span = max(max(xs) - min(xs), max(ys) - min(ys), max(zs) - min(zs)) or 1.0
    k = 1.0 / span

    parts = []
    for mi, g in sorted(groups.items(), key=lambda kv: (kv[0] is None, kv[0])):
        colour = [0.72, 0.72, 0.72]
        if mi is not None and mi < len(mats):
            pbr = mats[mi].get("pbrMetallicRoughness", {})
            colour = [round(c, 3) for c in pbr.get("baseColorFactor", [0.72, 0.72, 0.72, 1])[:3]]
        img = tex_image(mi)
        part = {
            "name": f"{path.stem}__{mi if mi is not None else 0}",
            "pivot": [0, 0, 0],
            "color": colour,
            "positions": [round((v - (cx if i % 3 == 0 else (fy if i % 3 == 1 else cz))) * k, 3)
                          for i, v in enumerate(g["pos"])],
        }
        part["index"] = g["idx"]
        if img is not None and img in images and g["uv"]:
            part["tex"] = images[img]
            part["uvs"] = [round(v, 3) for v in g["uv"]]
        parts.append(part)
    return parts


def main(src: str, name: str, limit: int = 0):
    files = sorted(Path(src).glob("*.glb"))
    if limit:
        files = files[:limit]
    models = {}
    skipped = []
    for f in files:
        try:
            parts = convert(f)
        except Exception as e:                        # noqa: BLE001
            skipped.append((f.name, str(e)[:60]))
            continue
        if parts:
            models[f.stem] = parts
    OUT.mkdir(parents=True, exist_ok=True)
    dest = OUT / f"{name}.json"
    flat = [p for parts in models.values() for p in parts]
    with open(dest, "w", encoding="utf-8") as fh:
        json.dump({"root": name, "parts": flat}, fh, separators=(",", ":"))
    tris = sum(len(p["positions"]) // 9 for p in flat)
    print(f"  {dest.name}: {len(models)} models, {len(flat)} parts, "
          f"{tris:,} tris, {dest.stat().st_size/1048576:.1f} MB")
    for n, e in skipped[:5]:
        print(f"    skipped {n}: {e}")
    return models


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], int(sys.argv[3]) if len(sys.argv) > 3 else 0)
