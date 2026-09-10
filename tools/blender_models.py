"""Build the World's models in Blender and export them for three.js.

Run inside Blender (Scripting tab, or `blender --background --python`) to
regenerate `thecmanager/static/models/*.json`. The models are committed, so
this is only needed when the shapes change; it exists so they are reproducible
rather than three binaries nobody can edit.

**Why JSON and not glTF.** These are flat-shaded boxes: no skinning, no
textures, no animation clips. GLTFLoader would have meant vendoring it plus
BufferGeometryUtils and SkeletonUtils, roughly 190 KB, and an import map, to
read six cubes. The exporter below writes triangles and a colour, and three
derives flat face normals from non-indexed geometry, which is the look we want.

**Why the origins matter.** Every part's origin is placed at the joint it turns
about: a shoulder, a hip, the base of a wall. Vertices are written relative to
that origin, so animating in three is a rotation and nothing else, and the
per-tool movement stays in JavaScript where it can read what the agent is
doing.

Blender is Z-up and three.js is Y-up, so (x, y, z) is written as (x, z, -y).
"""
import json
import math
import os

import bpy
from mathutils import Vector

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "thecmanager", "static", "models")


# ------------------------------------------------------------------ helpers

def fresh_collection(name):
    if name in bpy.data.collections:
        c = bpy.data.collections[name]
        for ob in list(c.objects):
            bpy.data.objects.remove(ob, do_unlink=True)
        bpy.data.collections.remove(c)
    c = bpy.data.collections.new(name)
    bpy.context.scene.collection.children.link(c)
    return c


def material(name, rgb, rough=0.7):
    m = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    m.use_nodes = True
    b = m.node_tree.nodes.get("Principled BSDF")
    b.inputs["Base Color"].default_value = (*rgb, 1.0)
    b.inputs["Roughness"].default_value = rough
    return m


def _finish(ob, coll, name, mat, pivot, bevel):
    ob.name = name
    if bevel:
        b = ob.modifiers.new("bev", "BEVEL")
        b.width, b.segments, b.limit_method = bevel, 1, 'ANGLE'
        bpy.ops.object.modifier_apply(modifier=b.name)
    bpy.context.scene.cursor.location = Vector(pivot)
    bpy.ops.object.origin_set(type='ORIGIN_CURSOR')      # origin = the joint
    ob.data.materials.clear()
    ob.data.materials.append(mat)
    for c in list(ob.users_collection):
        c.objects.unlink(ob)
    coll.objects.link(ob)
    return ob


def box(coll, name, size, loc, mat, pivot=(0, 0, 0), bevel=0.03):
    bpy.ops.mesh.primitive_cube_add(size=1, location=loc)
    ob = bpy.context.active_object
    ob.scale = Vector(size)
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    return _finish(ob, coll, name, mat, pivot, bevel)


def cone(coll, name, radius, depth, loc, mat, verts=4, pivot=(0, 0, 0)):
    bpy.ops.mesh.primitive_cone_add(vertices=verts, radius1=radius, radius2=0,
                                    depth=depth, location=loc)
    ob = bpy.context.active_object
    ob.rotation_euler[2] = math.radians(45)
    bpy.ops.object.transform_apply(rotation=True)
    return _finish(ob, coll, name, mat, pivot, 0)


def cylinder(coll, name, radius, depth, loc, mat, verts=8, pivot=(0, 0, 0)):
    bpy.ops.mesh.primitive_cylinder_add(vertices=verts, radius=radius,
                                        depth=depth, location=loc)
    return _finish(bpy.context.active_object, coll, name, mat, pivot, 0)


def export(coll_name, filename, root):
    """Triangles, 3 decimals, no normals. See the module docstring."""
    coll = bpy.data.collections[coll_name]
    parts = []
    depsgraph = bpy.context.evaluated_depsgraph_get()
    for ob in sorted(coll.objects, key=lambda o: o.name):
        if ob.type != 'MESH':
            continue
        me = bpy.data.meshes.new_from_object(ob.evaluated_get(depsgraph))
        me.calc_loop_triangles()
        pos = []
        for tri in me.loop_triangles:
            for vi in tri.vertices:
                v = me.vertices[vi].co
                pos += [round(v.x, 3), round(v.z, 3), round(-v.y, 3)]
        colour = (0.6, 0.6, 0.6)
        if me.materials and me.materials[0] and me.materials[0].use_nodes:
            b = me.materials[0].node_tree.nodes.get("Principled BSDF")
            if b:
                colour = tuple(round(c, 3) for c in b.inputs["Base Color"].default_value[:3])
        loc = ob.location
        parts.append({
            "name": ob.name,
            "pivot": [round(loc.x, 3), round(loc.z, 3), round(-loc.y, 3)],
            "color": list(colour),
            "positions": pos,
        })
        bpy.data.meshes.remove(me)

    os.makedirs(OUT, exist_ok=True)
    path = os.path.join(OUT, filename)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"root": root, "parts": parts}, fh, separators=(",", ":"))
    return path, os.path.getsize(path)


# ------------------------------------------------------------------- models

def build_worker():
    """A chunky worker: big head, short limbs, joints where they should be."""
    coll = fresh_collection("gso1_worker")
    skin = material("gso1_skin", (0.93, 0.88, 0.80))
    body = material("gso1_body", (0.31, 0.17, 0.90))     # tinted per tool in JS
    limb = material("gso1_limb", (0.20, 0.18, 0.36))
    boot = material("gso1_boot", (0.12, 0.11, 0.20))

    root = bpy.data.objects.new("worker", None)
    coll.objects.link(root)
    parts = [
        box(coll, "torso", (0.46, 0.30, 0.60), (0, 0, 0.88), body, (0, 0, 0.58), 0.045),
        box(coll, "head",  (0.52, 0.46, 0.46), (0, 0, 1.40), skin, (0, 0, 1.17), 0.045),
        box(coll, "armL",  (0.15, 0.15, 0.46), (-0.30, 0, 0.86), limb, (-0.30, 0, 1.08), 0.045),
        box(coll, "armR",  (0.15, 0.15, 0.46), (0.30, 0, 0.86),  limb, (0.30, 0, 1.08), 0.045),
        box(coll, "legL",  (0.17, 0.17, 0.50), (-0.13, 0, 0.30), boot, (-0.13, 0, 0.56), 0.045),
        box(coll, "legR",  (0.17, 0.17, 0.50), (0.13, 0, 0.30),  boot, (0.13, 0, 0.56), 0.045),
    ]
    for ob in parts:
        ob.parent = root
        ob.matrix_parent_inverse = root.matrix_world.inverted()
    return export("gso1_worker", "worker.json", "worker")


def build_props():
    """The village: a workshop, a store hut, a banner and crates."""
    coll = fresh_collection("gso1_props")
    stone = material("gso1_stone", (0.26, 0.25, 0.36), 0.75)
    wall  = material("gso1_wall",  (0.42, 0.38, 0.55), 0.75)
    roof  = material("gso1_roof",  (0.31, 0.17, 0.90), 0.75)
    dark  = material("gso1_dark",  (0.09, 0.08, 0.14), 0.75)
    wood  = material("gso1_wood",  (0.35, 0.26, 0.22), 0.75)
    teal  = material("gso1_teal",  (0.00, 0.88, 0.72), 0.75)

    box (coll, "hut_base",   (1.30, 1.30, 0.18), (0, 0, 0.09), stone)
    box (coll, "hut_wall",   (1.02, 1.02, 0.62), (0, 0, 0.49), wall)
    cone(coll, "hut_roof",   0.95, 0.55, (0, 0, 1.07), roof)
    box (coll, "hut_door",   (0.28, 0.06, 0.38), (0, -0.52, 0.37), dark, bevel=0.015)
    box (coll, "store_base", (0.62, 0.62, 0.12), (0, 0, 0.06), stone)
    box (coll, "store_wall", (0.48, 0.48, 0.34), (0, 0, 0.29), wood)
    cone(coll, "store_roof", 0.46, 0.28, (0, 0, 0.60), roof)
    cylinder(coll, "flag_pole", 0.035, 1.15, (0, 0, 0.575), wood, verts=6)
    box (coll, "flag_cloth", (0.42, 0.03, 0.26), (0.23, 0, 1.00), teal, bevel=0.01)
    box (coll, "crate",      (0.26, 0.26, 0.26), (0, 0, 0.13), wood)
    return export("gso1_props", "props.json", "props")


if __name__ == "__main__":
    for path, size in (build_worker(), build_props()):
        print(f"wrote {path} ({size:,} bytes)")
    bpy.context.scene.cursor.location = (0, 0, 0)
