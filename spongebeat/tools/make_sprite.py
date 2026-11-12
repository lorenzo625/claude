#!/usr/bin/env python3
"""Cuts the pixel-art character out of reference.png.

The photo is a 256 px wide pixel-art frame that was upscaled to 735 px, so it
is first sampled back onto its native grid. The mint/blue backdrop and the
loose music notes are then dropped and the colours snapped to a small palette.

Writes extension/images/sprite.png and the toolbar icons.
Requires numpy and Pillow.
"""
from collections import deque
from pathlib import Path

import numpy as np
from PIL import Image

HERE = Path(__file__).resolve().parent
OUT = HERE.parent / "extension" / "images"
NATIVE_WIDTH = 256
COLOURS = 24


def native_grid(rgb):
    """Median colour of the middle of every upscaled pixel block."""
    h, w, _ = rgb.shape
    step = w / NATIVE_WIDTH
    rows = int(round(h / step))
    out = np.zeros((rows, NATIVE_WIDTH, 3))
    for j in range(rows):
        y0, y1 = int(round(j * step + 0.6)), max(int(round((j + 1) * step - 0.6)), int(round(j * step + 0.6)) + 1)
        for i in range(NATIVE_WIDTH):
            x0, x1 = int(round(i * step + 0.6)), max(int(round((i + 1) * step - 0.6)), int(round(i * step + 0.6)) + 1)
            out[j, i] = np.median(rgb[y0:min(y1, h), x0:min(x1, w)].reshape(-1, 3), axis=0)
    return out


def largest_component(mask):
    seen = np.zeros_like(mask, bool)
    best = []
    h, w = mask.shape
    for sy, sx in zip(*np.nonzero(mask)):
        if seen[sy, sx]:
            continue
        seen[sy, sx] = True
        queue, cells = deque([(sy, sx)]), []
        while queue:
            y, x = queue.popleft()
            cells.append((y, x))
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    ny, nx = y + dy, x + dx
                    if 0 <= ny < h and 0 <= nx < w and mask[ny, nx] and not seen[ny, nx]:
                        seen[ny, nx] = True
                        queue.append((ny, nx))
        if len(cells) > len(best):
            best = cells
    out = np.zeros_like(mask, bool)
    ys, xs = zip(*best)
    out[list(ys), list(xs)] = True
    return out


def neighbours(y, x, h, w):
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            if (dy or dx) and 0 <= y + dy < h and 0 <= x + dx < w:
                yield y + dy, x + dx


def defringe(grid, body):
    """Edge cells that are a blend of character and backdrop become one or the other."""
    h, w = body.shape
    edge = np.zeros_like(body)
    for y, x in zip(*np.nonzero(body)):
        edge[y, x] = any(not body[n] for n in neighbours(y, x, h, w))
    grid, out = grid.copy(), body.copy()
    for y, x in zip(*np.nonzero(edge)):
        outside = [grid[n] for n in neighbours(y, x, h, w) if not body[n]]
        inside = [grid[n] for n in neighbours(y, x, h, w) if body[n] and not edge[n]]
        if not inside:
            continue
        bg, c = np.mean(outside, axis=0), grid[y, x]
        best = None
        for n in inside:
            span = n - bg
            a = float(np.clip((c - bg) @ span / max(span @ span, 1e-9), 0, 1))
            off = np.linalg.norm(c - (bg + a * span))
            if best is None or off < best[0]:
                best = (off, a, n)
        off, a, n = best
        if off > 28 or np.linalg.norm(c - n) < 28:
            continue  # a colour of its own, such as a thin outline
        if a < 0.55:
            out[y, x] = False
        else:
            grid[y, x] = n
    return grid, out


def add_outline(rgba, colour=(250, 250, 244, 255)):
    """One-pixel light sticker border so the black outline survives dark pages."""
    padded = np.zeros((rgba.shape[0] + 2, rgba.shape[1] + 2, 4), np.uint8)
    padded[1:-1, 1:-1] = rgba
    solid = padded[..., 3] > 0
    grown = solid.copy()
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            grown |= np.roll(np.roll(solid, dy, 0), dx, 1)
    padded[grown & ~solid] = colour
    return padded


def main():
    rgb = np.asarray(Image.open(HERE / "reference.png").convert("RGB")).astype(float)
    grid = native_grid(rgb)
    r, g, b = grid[..., 0], grid[..., 1], grid[..., 2]
    # Backdrop: light, cool mint and sky blue. The shirt is neutral white, so
    # it is told apart by how far green sits above red.
    backdrop = (g > 228) & (b > 195) & (g - r > 22)
    body = largest_component(~backdrop)
    grid, body = defringe(grid, body)
    body = largest_component(body)

    ys, xs = np.nonzero(body)
    y0, y1, x0, x1 = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1
    grid, body = grid[y0:y1, x0:x1], body[y0:y1, x0:x1]

    # Palette from the character's own pixels only.
    opaque = np.clip(grid[body], 0, 255).astype(np.uint8)
    strip = Image.fromarray(opaque[None, :, :], "RGB")
    quant = strip.quantize(colors=COLOURS, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE, kmeans=COLOURS)
    palette = np.array(quant.getpalette()[:COLOURS * 3]).reshape(-1, 3)
    out = np.zeros((*body.shape, 4), np.uint8)
    out[body, :3] = palette[np.asarray(quant)[0]]
    out[body, 3] = 255

    OUT.mkdir(parents=True, exist_ok=True)
    sprite = Image.fromarray(add_outline(out), "RGBA")
    sprite.save(OUT / "sprite.png", optimize=True)
    print("sprite", sprite.size, "colours", len(palette))

    # Toolbar icons: head and headphones, the square top of the sprite.
    head = sprite.crop((0, 0, sprite.width, sprite.width))
    for size in (16, 32, 48, 128):
        head.resize((size, size), Image.LANCZOS).save(OUT / f"icon{size}.png", optimize=True)


if __name__ == "__main__":
    main()
