#!/usr/bin/env python3
"""Procedurally builds the PS2-style angel teddy bear.

Writes textures/*.png and angel_bear.glb. Everything is authored in "design
units" (du, roughly 100 px of the reference photo) and scaled to metres on
export. Conventions are glTF's: +Y up, the bear faces +Z, its left side is +X.

Requires numpy, Pillow and mathutils (bundled with Blender's `bpy` module).
"""
import io
import json
import math
import struct
from pathlib import Path

import numpy as np
from PIL import Image
import bpy  # noqa: F401  (the bpy module is what makes mathutils importable)
from mathutils import Vector
from mathutils.bvhtree import BVHTree

ROOT = Path(__file__).resolve().parent.parent
OUT_GLB = ROOT / "angel_bear.glb"
TEX_DIR = ROOT / "textures"
SCALE = 0.12  # du -> metres; the bear ends up ~0.47 m tall

X = np.array([1.0, 0.0, 0.0])
Y = np.array([0.0, 1.0, 0.0])
Z = np.array([0.0, 0.0, 1.0])


# ---------------------------------------------------------------- utilities

def norm(v):
    v = np.asarray(v, float)
    return v / np.maximum(np.linalg.norm(v, axis=-1, keepdims=True), 1e-12)


def smoothstep(a, b, x):
    t = np.clip((np.asarray(x, float) - a) / (b - a), 0.0, 1.0)
    return t * t * (3 - 2 * t)


def lerp(a, b, t):
    return a + (b - a) * t


def bezier(ctrl, ts):
    """De Casteljau evaluation of a Bezier curve of any degree."""
    pts = [np.asarray(c, float)[None, :] * np.ones((len(ts), 1)) for c in ctrl]
    t = np.asarray(ts, float)[:, None]
    while len(pts) > 1:
        pts = [lerp(p, q, t) for p, q in zip(pts[:-1], pts[1:])]
    return pts[0]


def quat_axis_angle(axis, deg):
    half = math.radians(deg) / 2
    x, y, z = norm(axis) * math.sin(half)
    return (x, y, z, math.cos(half))


def quat_matrix(q):
    x, y, z, w = q
    return np.array([
        [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
        [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
        [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
    ])


def grid_tris(nrows, ncols):
    i, j = np.meshgrid(np.arange(nrows - 1), np.arange(ncols - 1), indexing="ij")
    a = (i * ncols + j).ravel()
    b, c, d = a + 1, a + ncols, a + ncols + 1
    return np.concatenate([np.stack([a, c, b], 1), np.stack([b, c, d], 1)])


def clean(pos, tris):
    """Drops degenerate triangles and orients a closed mesh outward."""
    p = pos[tris]
    area = np.linalg.norm(np.cross(p[:, 1] - p[:, 0], p[:, 2] - p[:, 0]), axis=1)
    tris = tris[area > 1e-9]
    p = pos[tris] - pos.mean(0)
    volume = np.einsum("ij,ij->i", p[:, 0], np.cross(p[:, 1], p[:, 2])).sum()
    return tris if volume > 0 else tris[:, ::-1]


def sphere_ab(nseg, nring):
    a = np.linspace(0, 1, nseg + 1)
    b = np.linspace(0, 1, nring + 1)
    B, A = np.meshgrid(b, a, indexing="ij")
    return A.ravel(), B.ravel(), grid_tris(nring + 1, nseg + 1)


def sphere_dirs(A, B):
    """Unit sphere with the b=0 pole at +Z and the a=0 meridian towards +Y."""
    th, ph = np.pi * B, 2 * np.pi * A
    return np.stack([-np.sin(th) * np.sin(ph), np.sin(th) * np.cos(ph), np.cos(th)], 1)


def basis(forward, up=Y):
    ez = norm(forward)
    ex = norm(np.cross(up, ez))
    return np.stack([ex, np.cross(ez, ex), ez], 1)  # columns are the local axes


def tube(path, radii, nseg, seam, cap0="flat", cap1="round", vscale=0.4):
    """Swept tube with capped ends; u wraps once around, v follows the length."""
    path = np.asarray(path, float)
    radii = np.asarray(radii, float)
    tang = norm(np.gradient(path, axis=0))
    rows = []  # (centre, radius, normal, binormal)
    n = np.asarray(seam, float)
    frames = []
    for k in range(len(path)):
        n = norm(n - (n @ tang[k]) * tang[k])
        frames.append((n, np.cross(tang[k], n)))

    def cap(k, sign, style):
        angles = {"flat": [90], "round": [90, 58, 28]}[style]
        out = []
        for ang in angles:
            s, c = math.sin(math.radians(ang)), math.cos(math.radians(ang))
            depth = radii[k] * s if style == "round" else 0.0
            out.append((path[k] + sign * tang[k] * depth, radii[k] * c, *frames[k]))
        return out

    rows += cap(0, -1, cap0)
    rows += [(path[k], radii[k], *frames[k]) for k in range(len(path))]
    rows += cap(len(path) - 1, 1, cap1)[::-1]

    a = np.linspace(0, 1, nseg + 1)
    cs, sn = np.cos(2 * np.pi * a)[:, None], np.sin(2 * np.pi * a)[:, None]
    pos, uv, v, prev = [], [], 0.0, None
    for c, r, nn, bb in rows:
        ring = c + r * (cs * nn + sn * bb)
        if prev is not None:
            v += np.linalg.norm(ring[0] - prev) * vscale
        prev = ring[0]
        pos.append(ring)
        uv.append(np.stack([a, np.full_like(a, v)], 1))
    pos, uv = np.concatenate(pos), np.concatenate(uv)
    return pos, uv, clean(pos, grid_tris(len(rows), nseg + 1))


class Part:
    """One glTF node + mesh. Geometry is stored relative to the pivot."""

    def __init__(self, name, parent=None, pivot=(0, 0, 0), quat=(0, 0, 0, 1)):
        self.name, self.parent = name, parent
        self.pivot = np.asarray(pivot, float)
        self.quat = quat
        self.prims = []

    def add(self, mat, pos, uv, tris, tint=None, local=False):
        used = np.unique(tris)
        remap = np.full(len(pos), -1)
        remap[used] = np.arange(len(used))
        tint = np.ones((len(pos), 3)) if tint is None else np.asarray(tint, float)
        self.prims.append(dict(
            mat=mat,
            pos=pos[used] - (0 if local else self.pivot),
            uv=np.asarray(uv, float)[used],
            tint=tint[used],
            tris=remap[tris],
        ))

    def mirrored(self, name):
        x, y, z, w = self.quat
        out = Part(name, self.parent, self.pivot * [-1, 1, 1], (x, -y, -z, w))
        for p in self.prims:
            out.prims.append(dict(p, pos=p["pos"] * [-1, 1, 1], tris=p["tris"][:, ::-1]))
        return out

    def world(self):
        """Returns (rotation, translation) mapping local du coordinates to world."""
        R, t = quat_matrix(self.quat), self.pivot
        if self.parent is not None:
            PR, pt = self.parent.world()
            R, t = PR @ R, PR @ t + pt
        return R, t


# ----------------------------------------------------------------- textures

rng = np.random.default_rng(7)


def tile_noise(h, w, cell):
    """Smooth value noise that tiles in both directions."""
    g = rng.random((h // cell, w // cell))
    ys, xs = np.arange(h) / cell, np.arange(w) / cell
    y0, x0 = ys.astype(int), xs.astype(int)
    fy, fx = smoothstep(0, 1, ys - y0)[:, None], smoothstep(0, 1, xs - x0)[None, :]
    y1, x1 = (y0 + 1) % g.shape[0], (x0 + 1) % g.shape[1]
    top = lerp(g[y0][:, x0], g[y0][:, x1], fx)
    bot = lerp(g[y1][:, x0], g[y1][:, x1], fx)
    return lerp(top, bot, fy)


def fur_field(size):
    """Zero-mean, unit-variance plush pattern: soft blotches plus short strands."""
    blot = sum(amp * tile_noise(size, size, c) for amp, c in ((0.5, 32), (0.35, 16), (0.3, 8), (0.3, 4)))
    white = rng.random((size, size))
    strands = sum(np.roll(white, k, axis=0) for k in range(-3, 4))
    strands = strands + 0.5 * np.roll(strands, 1, axis=1)
    f = (blot - blot.mean()) / blot.std() * 0.6 + (strands - strands.mean()) / strands.std() * 0.8
    return (f - f.mean()) / f.std()


FUR_DARK = np.array([52, 31, 19.0])
FUR_MID = np.array([94, 60, 39.0])
FUR_LIGHT = np.array([150, 106, 72.0])


def fur_rgb(field):
    t = np.clip(field * 0.3, -1, 1)[..., None]
    return np.where(t < 0, lerp(FUR_MID, FUR_DARK, -t), lerp(FUR_MID, FUR_LIGHT, t))


def ellipse(size, cx, cy, rx, ry):
    """Signed distance-ish field: <0 inside, in pixels along the minor axis."""
    yy, xx = np.mgrid[0:size, 0:size] + 0.5
    return (np.hypot((xx - cx) / rx, (yy - cy) / ry) - 1) * min(rx, ry)


def tex_fur():
    size = 128
    img = fur_rgb(fur_field(size))
    # Stitched seam on the u=0 edge, so it wraps cleanly across the tile border.
    stitch = (np.arange(size) % 8 < 5)[:, None]
    img[:, 0] = np.where(stitch, lerp(img[:, 0], FUR_LIGHT * 1.08, 0.85), img[:, 0] * 0.62)
    for col, k in ((1, 0.68), (size - 1, 0.68), (2, 0.88), (size - 2, 0.88)):
        img[:, col] *= k
    return img


def tex_sole():
    size = 64
    img = fur_rgb(fur_field(size) * 0.8)
    pads = np.minimum.reduce([
        ellipse(size, 14, 21, 7, 8), ellipse(size, 32, 13, 7, 8), ellipse(size, 50, 21, 7, 8),
        ellipse(size, 23, 42, 12, 12), ellipse(size, 41, 42, 12, 12), ellipse(size, 32, 46, 18, 11),
    ])
    stipple = 1 + 0.2 * (rng.random((size, size)) - 0.5) + 0.12 * (tile_noise(size, size, 4) - 0.5)
    pad_col = np.array([172, 128, 92.0]) * stipple[..., None]
    pad_col *= lerp(0.8, 1.0, smoothstep(0, 4, -pads))[..., None]  # soft inner edge
    img *= lerp(0.6, 1.0, smoothstep(0, 3.5, pads))[..., None]  # contact shadow
    return lerp(img, pad_col, smoothstep(0.6, -0.6, pads)[..., None])


def tex_heart():
    size = 64
    base = np.array([208, 20, 36.0]) * (0.86 + 0.28 * tile_noise(size, size, 8))[..., None]
    grain = rng.random((size, size))
    img = base * (0.82 + 0.36 * rng.random((size, size)))[..., None]
    img[grain > 0.95] = [255, 150, 160]
    img[grain < 0.07] = [132, 6, 20]
    return img


def tex_feather():
    w, h = 32, 64
    yy, xx = np.mgrid[0:h, 0:w] + 0.5
    u, v = xx / w, yy / h
    side = np.abs(2 * u - 1)
    shade = 1 - 0.2 * side ** 2
    shade *= 1 + 0.035 * np.sin((yy - side * w * 0.9) * 2 * np.pi / 5)  # barbs
    shade *= 1 - 0.12 * np.exp(-(side * w / 2 / 0.8) ** 2) * (1 - 0.6 * v)  # shaft
    shade *= 1 + 0.03 * (rng.random((h, w)) - 0.5)
    root = smoothstep(0.0, 0.4, v)[..., None]  # darker, cooler where a row tucks under
    img = np.array([250, 249, 244.0]) * shade[..., None] * lerp(np.array([0.8, 0.82, 0.9]), 1.0, root)
    img[rng.random((h, w)) > 0.975] = 255  # glitter
    return img


def tex_eye():
    size = 32
    yy, xx = np.mgrid[0:size, 0:size] + 0.5
    img = np.ones((size, size, 3)) * [15, 12, 14]
    rim = np.exp(-((np.hypot(xx - 16, yy - 16) - 12.5) / 3.0) ** 2) * smoothstep(-4, 12, (xx - 16) + (yy - 16))
    img += rim[..., None] * np.array([70, 58, 66.0])
    a = math.radians(-28)
    hx = (xx - 10.5) * math.cos(a) - (yy - 9.5) * math.sin(a)
    hy = (xx - 10.5) * math.sin(a) + (yy - 9.5) * math.cos(a)
    glint = smoothstep(1.25, 0.55, np.hypot(hx / 5.2, hy / 2.6))
    glint = np.maximum(glint, 0.8 * smoothstep(1.3, 0.4, np.hypot(xx - 19.5, yy - 7.5) / 1.6))
    return lerp(img, np.array([255, 255, 255.0]), glint[..., None])


def encode_png(rgb, colors):
    """8-bit CLUT image, like PS2 textures."""
    im = Image.fromarray(np.clip(rgb, 0, 255).astype(np.uint8), "RGB")
    im = im.quantize(colors=colors, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)
    buf = io.BytesIO()
    im.save(buf, "PNG", optimize=True)
    return buf.getvalue()


# materials: name -> (texture file, generator, palette size)
MATERIALS = {
    "Fur": ("fur.png", tex_fur, 256),
    "PawPad": ("paw_pad.png", tex_sole, 128),
    "Heart": ("heart.png", tex_heart, 64),
    "Feather": ("feather.png", tex_feather, 64),
    "Eye": ("eye.png", tex_eye, 32),
}


# ----------------------------------------------------------------- geometry

HEAD_C = np.array([0.0, 2.55, 0.10])
HEAD_R = np.array([1.45, 1.05, 1.15])
MUZZLE = norm([0.0, -0.5, 0.9])


def head_surface(d):
    d = norm(d)
    p = d * HEAD_R
    p[..., 0] *= 1 + 0.07 * np.clip(-d[..., 1], 0, 1)  # cheeks
    w = np.exp(-(np.arccos(np.clip(d @ MUZZLE, -1, 1)) / 0.42) ** 2)
    return p + w[..., None] * np.array([0.0, -0.05, 0.2])


def planar_uv(d, scale=0.48):
    return np.stack([0.5 + scale * d[:, 0], 0.5 - scale * d[:, 1]], 1)


def build_head(root):
    head = Part("Head", root, HEAD_C, quat_axis_angle(Z, -12))
    # Poles on the muzzle and the back of the head: the texture pinch hides
    # under the nose and the seam runs straight down the face.
    A, B, tris = sphere_ab(16, 10)
    tilt = math.atan2(-MUZZLE[1], MUZZLE[2])
    rot = np.array([[1, 0, 0], [0, math.cos(tilt), -math.sin(tilt)], [0, math.sin(tilt), math.cos(tilt)]])
    pos = head_surface(sphere_dirs(A, B) @ rot.T)
    head.add("Fur", pos, np.stack([2 * A, B], 1), clean(pos, tris), local=True)

    parts = [head]
    for side, sx in (("L", 1), ("R", -1)):
        # ear
        c = np.array([1.27 * sx, 0.8, -0.1])
        A, B, tris = sphere_ab(10, 6)
        d = sphere_dirs(A, B)
        loc = d * [0.6, 0.6, 0.24]
        r = np.hypot(d[:, 0], d[:, 1])
        front = d[:, 2] > 0
        loc[:, 2] -= np.where(front, 0.26 * np.clip(1 - (r / 0.8) ** 2, 0, 1), 0)
        inner = np.where(front, 1 - smoothstep(0.55, 0.9, r), 0)[:, None]
        tint = lerp(np.ones(3), np.array([0.2, 0.15, 0.14]), inner)
        pos = loc @ basis([0.25 * sx, 0.1, 1.0]).T + c
        uv = np.stack([0.5 + 0.3 * loc[:, 0], 0.5 - 0.3 * loc[:, 1]], 1)
        ear = Part(f"Ear.{side}", head, c)
        ear.add("Fur", pos, uv, clean(pos, tris), tint)
        parts.append(ear)

        # eye
        p = head_surface(np.array([0.42 * sx, -0.17, 0.89]))
        n = norm(p / HEAD_R ** 2)
        c = p - 0.03 * n
        A, B, tris = sphere_ab(8, 5)
        d = sphere_dirs(A, B)
        pos = (d * [0.25, 0.18, 0.11]) @ basis(n).T + c
        eye = Part(f"Eye.{side}", head, c)
        eye.add("Eye", pos, planar_uv(d), clean(pos, tris))
        parts.append(eye)

    c = head_surface(MUZZLE) + np.array([0.0, 0.03, -0.02])
    A, B, tris = sphere_ab(8, 5)
    d = sphere_dirs(A, B)
    loc = d * [0.3, 0.17, 0.14]
    loc[:, 0] *= 0.72 + 0.38 * d[:, 1]  # rounded triangle, point down
    pos = loc @ basis([0, -0.3, 1.0]).T + c
    nose = Part("Nose", head, c)
    nose.add("Eye", pos, planar_uv(d), clean(pos, tris))
    parts.append(nose)
    return parts


def build_body(root):
    body = Part("Body", root)
    A, B, tris = sphere_ab(14, 9)
    d = sphere_dirs(A, B)
    d = np.stack([d[:, 0], -d[:, 2], d[:, 1]], 1)  # poles vertical, a=0 at the belly
    pear = 1 - 0.18 * d[:, 1]
    pos = np.stack([d[:, 0] * 0.95 * pear, 0.95 + d[:, 1], d[:, 2] * 0.82 * pear], 1)
    pos[:, 2] += 0.07 * np.clip(d[:, 2], 0, 1) * (1 - d[:, 1] ** 2)  # belly
    pos[:, 1] = np.maximum(pos[:, 1], 0.0)
    body.add("Fur", pos, np.stack([2 * A, B], 1), clean(pos, tris))

    c = np.array([0.0, 0.4, -0.9])
    A, B, tris = sphere_ab(8, 5)
    d = sphere_dirs(A, B)
    pos = d * 0.3 + c
    tail = Part("Tail", body, c)
    tail.add("Fur", pos, planar_uv(d, 0.09), clean(pos, tris))
    return [body, tail]


def build_arm(root):
    ts = np.linspace(0, 1, 6)
    path = bezier([[0.62, 1.55, 0.0], [1.55, 1.25, 0.7], [0.86, 1.15, 1.12]], ts)
    arm = Part("Arm.L", root, path[0])
    arm.add("Fur", *tube(path, lerp(0.30, 0.42, ts ** 1.3), 8, seam=[-1, -0.6, 0]))
    return arm


def build_leg(root):
    ts = np.linspace(0, 1, 4)
    path = bezier([[0.5, 0.5, 0.35], [1.2, 0.48, 1.1], [1.8, 0.55, 1.8]], ts)
    leg = Part("Leg.L", root, path[0])
    leg.add("Fur", *tube(path, lerp(0.5, 0.46, ts), 8, seam=Y, cap1="flat"))

    # Foot: a squashed dome whose flattened front carries the paw-pad texture.
    centre = np.array([1.98, 0.76, 1.98])
    M = basis([math.sin(math.radians(22)), 0.15, math.cos(math.radians(22))])
    nseg = 12
    rows = [(0, 0.2), (0.5, 0.17), (0.88, 0.08), (1.0, -0.08), (0.9, -0.3), (0.55, -0.46), (0, -0.52)]
    fur_v = [0, 0, 0.0, 0.1, 0.22, 0.36, 0.5]
    a = np.linspace(0, 1, nseg + 1)
    ang = 2 * np.pi * a + np.pi / 2  # seam on top, continuing the leg's
    pos, uv_fur, uv_sole, row_of = [], [], [], []
    for k, (rho, z) in enumerate(rows):
        loc = np.stack([0.8 * rho * np.cos(ang), 0.74 * rho * np.sin(ang), np.full_like(a, z)], 1)
        pos.append(loc @ M.T + centre)
        uv_fur.append(np.stack([a, np.full_like(a, fur_v[k])], 1))
        s = 0.47 * rho / 0.88
        uv_sole.append(np.stack([0.5 + s * np.cos(ang), 0.5 - s * np.sin(ang)], 1))
        row_of.append(np.full(nseg + 1, k))
    pos, row_of = np.concatenate(pos), np.concatenate(row_of)
    tris = clean(pos, grid_tris(len(rows), nseg + 1))
    sole = (row_of[tris] <= 2).all(1)
    leg.add("PawPad", pos, np.concatenate(uv_sole), tris[sole])
    leg.add("Fur", pos, np.concatenate(uv_fur), tris[~sole])
    return leg


def build_heart(root):
    centre = np.array([0.0, 1.32, 1.06])
    nseg, half_w, thick = 16, 0.72, 0.27
    t = np.linspace(0, 2 * np.pi, nseg + 1)
    ox = np.sin(t) ** 3
    oy = (13 * np.cos(t) - 5 * np.cos(2 * t) - 2 * np.cos(3 * t) - np.cos(4 * t)) / 16
    rows = [(0.0, 1), (0.55, 1), (0.9, 1), (1.0, 0), (0.9, -1), (0.55, -1), (0.0, -1)]
    pos = np.concatenate([
        np.stack([s * ox * half_w, (s * oy + 0.16) * half_w, sign * thick * np.sqrt(1 - s * s) * np.ones_like(t)], 1)
        for s, sign in rows
    ])
    lean = math.radians(-8)
    pos = pos @ np.array([[1, 0, 0], [0, math.cos(lean), -math.sin(lean)], [0, math.sin(lean), math.cos(lean)]]).T
    uv = np.stack([0.5 + pos[:, 0] * 0.8, 0.5 - pos[:, 1] * 0.8], 1)
    heart = Part("Heart", root, centre)
    heart.add("Heart", pos, uv, clean(pos, grid_tris(len(rows), nseg + 1)), local=True)
    return heart


def feather(base, direction, length, width, z, curl):
    """Flattened lens: diamond cross-sections between a root and a tip vertex."""
    d = np.array([direction[0], direction[1], 0.0])
    c = np.array([-direction[1], direction[0], 0.0])
    b = np.array([base[0], base[1], z])
    rings = ((0.18, 0.62), (0.5, 1.0), (0.8, 0.9), (0.95, 0.52))
    pos, uv = [b], [[0.5, 0.0]]
    for v, w in rings:
        hw = w * width / 2
        mid = b + d * (v * length) + Z * (curl * v * v)
        pos += [mid + c * hw, mid + Z * (0.26 * hw + 0.02), mid - c * hw, mid - Z * (0.26 * hw + 0.02)]
        uv += [[1, v], [0.5, v], [0, v], [0.5, v]]
    pos.append(b + d * length + Z * curl)
    uv.append([0.5, 1.0])
    last, tip = 1 + 4 * (len(rings) - 1), 1 + 4 * len(rings)
    tris = [[0, 1 + k, 1 + (k + 1) % 4] for k in range(4)]
    for r in range(len(rings) - 1):
        for k in range(4):
            p, q = 1 + 4 * r + k, 1 + 4 * r + (k + 1) % 4
            tris += [[p, p + 4, q], [q, p + 4, q + 4]]
    tris += [[tip, last + (k + 1) % 4, last + k] for k in range(4)]
    pos = np.array(pos)
    return pos, np.array(uv, float), clean(pos, np.array(tris))


def build_wing(root):
    """Left wing in its own plane (x outward, y up, z towards the front)."""
    wing = Part("Wing.L", root, [0.45, 1.95, -0.9], quat_axis_angle(Y, -6))
    lead = [[0.0, 0.0], [0.7, 1.8], [2.05, 1.9], [2.55, 0.6]]  # leading edge
    jitter = np.random.default_rng(3)

    # (count, s range, length fraction, width, z layer, curl): back row first
    for count, s0, s1, frac, width, z, curl in (
        (8, 0.10, 1.00, 1.00, 0.70, 0.00, 0.12),
        (7, 0.08, 0.94, 0.64, 0.66, 0.09, 0.09),
        (6, 0.10, 0.90, 0.36, 0.62, 0.17, 0.06),
    ):
        ss = np.linspace(s0, s1, count)
        for s, base in zip(ss, bezier(lead, ss)):
            ang = math.radians(lerp(-100, -62, s) + jitter.uniform(-3, 3))
            full = (base[1] - lerp(-0.85, -0.3, s)) / abs(math.sin(ang))
            length = full * frac * jitter.uniform(0.95, 1.05)
            wing.add("Feather", *feather(base, (math.cos(ang), math.sin(ang)), length, width, z, curl), local=True)

    ts = np.linspace(0, 1, 9)
    path = np.concatenate([bezier(lead, ts), np.full((9, 1), 0.16)], 1)
    pos, uv, tris = tube(path, lerp(0.19, 0.1, ts), 6, seam=-Z)
    # Fold u so the plain part of the feather texture wraps without a seam.
    uv = np.stack([0.15 + 0.2 * np.abs(2 * uv[:, 0] - 1), 0.55 + 0.1 * uv[:, 1]], 1)
    wing.add("Feather", pos, uv, tris, local=True)
    return wing


def build_parts():
    root = Part("AngelBear")
    arm, leg, wing = build_arm(root), build_leg(root), build_wing(root)
    return [
        root, *build_body(root), *build_head(root),
        arm, arm.mirrored("Arm.R"), leg, leg.mirrored("Leg.R"),
        build_heart(root), wing, wing.mirrored("Wing.R"),
    ]


# ------------------------------------------------------- normals and shading

def finish(parts):
    """Adds smooth normals and baked ambient-occlusion vertex colours."""
    verts, polys, welded = [], [], []
    for part in parts:
        if not part.prims:
            continue
        R, t = part.world()
        pos = np.concatenate([p["pos"] for p in part.prims])
        offs = np.cumsum([0] + [len(p["pos"]) for p in part.prims])
        tris = np.concatenate([p["tris"] + o for p, o in zip(part.prims, offs)])
        # Weld UV-seam duplicates so shading stays smooth across them.
        _, first, inv = np.unique(np.round(pos * 1e4).astype(np.int64), axis=0, return_index=True, return_inverse=True)
        inv = inv.ravel()
        tp = pos[tris]
        fn = np.cross(tp[:, 1] - tp[:, 0], tp[:, 2] - tp[:, 0])
        acc = np.zeros((len(first), 3))
        for k in range(3):
            np.add.at(acc, inv[tris[:, k]], fn)
        nrm = norm(acc)
        welded.append((part, offs, inv, pos[first] @ R.T + t, nrm, nrm @ R.T))
        base = len(verts)
        verts += [tuple(v) for v in pos @ R.T + t]
        polys += [tuple(int(i) + base for i in tri) for tri in tris]

    base = len(verts)
    verts += [(-50, -0.002, -50), (-50, -0.002, 50), (50, -0.002, 50), (50, -0.002, -50)]
    polys += [(base, base + 1, base + 2), (base, base + 2, base + 3)]
    bvh = BVHTree.FromPolygons(verts, polys, all_triangles=True)

    srng = np.random.default_rng(11)
    nrays, reach = 48, 1.4
    r, phi = np.sqrt(srng.random(nrays)), 2 * np.pi * srng.random(nrays)
    hemi = np.stack([r * np.cos(phi), r * np.sin(phi), np.sqrt(1 - r * r)], 1)  # cosine weighted
    shadow = np.array([0.2, 0.17, 0.26])

    for part, offs, inv, wpos, nrm, wnrm in welded:
        ao = np.empty(len(wpos))
        for i, (p, n) in enumerate(zip(wpos, wnrm)):
            dirs = hemi @ basis(n, X if abs(n[1]) > 0.9 else Y).T
            origin = Vector(p + n * 0.03)
            blocked = 0.0
            for d in dirs:
                dist = bvh.ray_cast(origin, Vector(d), reach)[3]
                if dist is not None:
                    blocked += 1 - dist / reach
            ao[i] = 1 - blocked / nrays
        light = lerp(shadow, np.ones(3), smoothstep(0.05, 0.85, ao)[:, None])
        for prim, o0, o1 in zip(part.prims, offs[:-1], offs[1:]):
            prim["nrm"] = nrm[inv[o0:o1]]
            prim["col"] = prim["tint"] * light[inv[o0:o1]]


# -------------------------------------------------------------------- export

def write_glb(parts, images, path):
    blob = bytearray()
    views, accessors = [], []

    def view(data, target=None):
        while len(blob) % 4:
            blob.append(0)
        v = {"buffer": 0, "byteOffset": len(blob), "byteLength": len(data)}
        if target:
            v["target"] = target
        blob.extend(data)
        views.append(v)
        return len(views) - 1

    def accessor(arr, ctype, kind, target=34962, bounds=False, normalized=False):
        acc = {"bufferView": view(arr.tobytes(), target), "componentType": ctype, "count": len(arr), "type": kind}
        if bounds:
            acc["min"], acc["max"] = arr.min(0).tolist(), arr.max(0).tolist()
        if normalized:
            acc["normalized"] = True
        accessors.append(acc)
        return len(accessors) - 1

    names = list(MATERIALS)
    gltf_images, materials = [], []
    for name in names:
        gltf_images.append({"name": name, "mimeType": "image/png", "bufferView": view(images[name])})
        materials.append({
            "name": name,
            "pbrMetallicRoughness": {
                "baseColorTexture": {"index": len(materials)},
                "metallicFactor": 0.0,
                "roughnessFactor": 1.0,
            },
        })

    nodes, meshes = [], []
    index = {id(p): i for i, p in enumerate(parts)}
    for part in parts:
        node = {"name": part.name}
        if np.any(part.pivot):
            node["translation"] = (part.pivot * SCALE).tolist()
        if tuple(part.quat) != (0, 0, 0, 1):
            node["rotation"] = [float(q) for q in part.quat]
        kids = [index[id(p)] for p in parts if p.parent is part]
        if kids:
            node["children"] = kids
        if part.prims:
            prims = []
            for p in part.prims:
                col = np.concatenate([np.clip(p["col"], 0, 1), np.ones((len(p["col"]), 1))], 1)
                prims.append({
                    "attributes": {
                        "POSITION": accessor((p["pos"] * SCALE).astype(np.float32), 5126, "VEC3", bounds=True),
                        "NORMAL": accessor(p["nrm"].astype(np.float32), 5126, "VEC3"),
                        "TEXCOORD_0": accessor(p["uv"].astype(np.float32), 5126, "VEC2"),
                        "COLOR_0": accessor(np.round(col * 255).astype(np.uint8), 5121, "VEC4", normalized=True),
                    },
                    "indices": accessor(p["tris"].astype(np.uint16).ravel(), 5123, "SCALAR", target=34963),
                    "material": names.index(p["mat"]),
                })
            node["mesh"] = len(meshes)
            meshes.append({"name": part.name, "primitives": prims})
        nodes.append(node)

    while len(blob) % 4:
        blob.append(0)
    gltf = {
        "asset": {"version": "2.0", "generator": "angel-bear tools/build_model.py"},
        "scene": 0,
        "scenes": [{"name": "Scene", "nodes": [0]}],
        "nodes": nodes,
        "meshes": meshes,
        "materials": materials,
        "textures": [{"sampler": 0, "source": i} for i in range(len(names))],
        "images": gltf_images,
        # bilinear + mipmaps, repeat wrapping
        "samplers": [{"magFilter": 9729, "minFilter": 9987, "wrapS": 10497, "wrapT": 10497}],
        "accessors": accessors,
        "bufferViews": views,
        "buffers": [{"byteLength": len(blob)}],
    }
    js = json.dumps(gltf, separators=(",", ":")).encode()
    js += b" " * (-len(js) % 4)
    with open(path, "wb") as f:
        f.write(struct.pack("<4sII", b"glTF", 2, 12 + 8 + len(js) + 8 + len(blob)))
        f.write(struct.pack("<I4s", len(js), b"JSON") + js)
        f.write(struct.pack("<I4s", len(blob), b"BIN\0") + bytes(blob))


def main():
    TEX_DIR.mkdir(exist_ok=True)
    images = {}
    for name, (filename, make, colors) in MATERIALS.items():
        images[name] = encode_png(make(), colors)
        (TEX_DIR / filename).write_bytes(images[name])

    parts = build_parts()
    finish(parts)
    write_glb(parts, images, OUT_GLB)

    tris = sum(len(p["tris"]) for part in parts for p in part.prims)
    verts = sum(len(p["pos"]) for part in parts for p in part.prims)
    top = max((p["pos"] @ part.world()[0].T + part.world()[1])[:, 1].max() for part in parts for p in part.prims)
    print(f"{OUT_GLB.name}: {tris} triangles, {verts} vertices, {OUT_GLB.stat().st_size / 1024:.0f} KiB, "
          f"{top * SCALE:.2f} m tall")


if __name__ == "__main__":
    main()
