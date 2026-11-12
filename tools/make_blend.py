#!/usr/bin/env python3
"""Imports angel_bear.glb into Blender, sets up a small scene and saves
angel_bear.blend. Pass a directory as the first argument to also render
preview images into it. Runs with Blender's `bpy` Python module.
"""
import sys
from pathlib import Path

import bpy
from mathutils import Vector

ROOT = Path(__file__).resolve().parent.parent
TARGET = Vector((0.0, 0.0, 0.23))


def look_from(obj, location):
    obj.location = location
    obj.rotation_euler = (TARGET - Vector(location)).to_track_quat("-Z", "Y").to_euler()


def main():
    bpy.ops.wm.read_factory_settings()
    for datablocks in (bpy.data.objects, bpy.data.meshes, bpy.data.materials, bpy.data.lights, bpy.data.cameras):
        for block in list(datablocks):
            datablocks.remove(block)

    bpy.ops.import_scene.gltf(filepath=str(ROOT / "angel_bear.glb"))
    for mat in bpy.data.materials:
        # Plush and painted highlights only: no specular, as on PS2.
        bsdf = mat.node_tree.nodes.get("Principled BSDF")
        if bsdf and "Specular IOR Level" in bsdf.inputs:
            bsdf.inputs["Specular IOR Level"].default_value = 0.0
    for obj in bpy.context.selected_objects:
        obj.select_set(False)

    scene = bpy.context.scene
    cam = bpy.data.objects.new("Camera", bpy.data.cameras.new("Camera"))
    cam.data.lens = 50
    scene.collection.objects.link(cam)
    scene.camera = cam
    look_from(cam, (0.22, -1.55, 0.50))

    sun = bpy.data.objects.new("Sun", bpy.data.lights.new("Sun", "SUN"))
    sun.data.energy = 3.0
    sun.data.angle = 0.2
    scene.collection.objects.link(sun)
    look_from(sun, (-1.0, -2.0, 2.4))

    background = scene.world.node_tree.nodes["Background"]
    background.inputs["Color"].default_value = (0.20, 0.20, 0.26, 1.0)
    background.inputs["Strength"].default_value = 1.0

    scene.render.engine = "CYCLES"
    scene.cycles.device = "CPU"
    scene.cycles.samples = 48
    scene.render.resolution_x, scene.render.resolution_y = 960, 720
    scene.view_settings.view_transform = "Standard"

    # Open straight into a textured viewport.
    for screen in bpy.data.screens:
        for area in screen.areas:
            for space in area.spaces:
                if space.type == "VIEW_3D":
                    space.shading.type = "MATERIAL"

    bpy.ops.wm.save_as_mainfile(filepath=str(ROOT / "angel_bear.blend"), compress=True)
    print(f"saved angel_bear.blend with Blender {bpy.app.version_string}: "
          f"{len(bpy.data.objects)} objects, {len(bpy.data.materials)} materials, "
          f"{sum(1 for i in bpy.data.images if i.packed_file)} packed images")

    if len(sys.argv) > 1:
        out = Path(sys.argv[1])
        views = {"front": (0.22, -1.55, 0.50), "side": (1.35, -0.85, 0.45), "back": (-0.7, 1.4, 0.6), "top": (0.0, -0.9, 1.4)}
        for name, location in views.items():
            look_from(cam, location)
            scene.render.filepath = str(out / f"{name}.png")
            bpy.ops.render.render(write_still=True)


if __name__ == "__main__":
    main()
