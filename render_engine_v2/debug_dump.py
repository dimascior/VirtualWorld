"""Headless debugger: render one frame and dump hit-ID grid + color grid + ASCII preview.

Usage:
    python render_engine_v2/debug_dump.py [yaw_deg] [pitch_deg] [px] [py] [pz]

Writes:
    render_engine_v2/_debug_hits.txt   - per-cell first-hit material IDs
    render_engine_v2/_debug_ascii.txt  - brightness ASCII preview
    render_engine_v2/_debug_color.txt  - per-cell RGB triplets
"""
import sys, os, math
sys.path.insert(0, os.path.dirname(__file__))

# Import the renderer module so we reuse its scene + camera
import importlib.util
spec = importlib.util.spec_from_file_location(
    "_bed", os.path.join(os.path.dirname(__file__), "test_bedroom_enhanced.py"))
# Stop the renderer's main loop from running by hijacking msvcrt before import
# Easier: re-implement minimal driver using the same primitives by reading file directly
# Fallback: extract just the classes we need by exec-ing only the top portion.

# Simplest: read the source up to "# === MAIN ===" marker and exec it
src_path = os.path.join(os.path.dirname(__file__), "test_bedroom_enhanced.py")
with open(src_path, "r", encoding="utf-8") as f:
    src = f.read()
cut = src.index("# === MAIN ===")
header = src[:cut]
ns = {"__name__": "_bed_headless"}
exec(compile(header, src_path, "exec"), ns)

Vec3 = ns["Vec3"]
Camera = ns["Camera"]
trace_ray = ns["trace_ray"]
create_bedroom = ns["create_bedroom"]
WIDTH = ns["WIDTH"]
HEIGHT = ns["HEIGHT"]

# Parse args
def farg(i, d):
    return float(sys.argv[i]) if len(sys.argv) > i else d

yaw_deg = farg(1, 0.0)
pitch_deg = farg(2, 0.0)
px = farg(3, 0.0)
py = farg(4, 1.6)
pz = farg(5, 0.5)

cam = Camera()
cam.pos = Vec3(px, py, pz)
cam.yaw = math.radians(yaw_deg)
cam.pitch = math.radians(pitch_deg)

scene = create_bedroom()
light = Vec3(0, 2.7, 2)

print(f"Scene objects: {len(scene)}")
print(f"Camera pos=({px:.2f},{py:.2f},{pz:.2f}) yaw={yaw_deg:+.1f} pitch={pitch_deg:+.1f}")
print(f"Render grid: {WIDTH}x{HEIGHT*2} (half-blocks)")

render_h = HEIGHT * 2
hits = [[None] * WIDTH for _ in range(render_h)]
colors = [[(0, 0, 0)] * WIDTH for _ in range(render_h)]
depths = [[float('inf')] * WIDTH for _ in range(render_h)]

import time
t0 = time.time()
for y in range(render_h):
    for x in range(WIDTH):
        ray = cam.get_ray_dir(x, y, WIDTH, render_h)
        c, mid, t = trace_ray(cam.pos, ray, scene, light)
        hits[y][x] = mid
        colors[y][x] = c
        depths[y][x] = t
elapsed = time.time() - t0
print(f"Rendered {WIDTH*render_h} pixels in {elapsed:.2f}s ({WIDTH*render_h/elapsed:.0f} px/s)")

# Stats
from collections import Counter
flat_hits = [h for row in hits for h in row]
counts = Counter(flat_hits)
print("\nHit distribution (top 10):")
for mid, n in counts.most_common(10):
    pct = 100.0 * n / len(flat_hits)
    print(f"  id={mid:>3}  count={n:>5}  ({pct:5.1f}%)")

miss = counts.get(-1, 0)
print(f"\nMisses (sky color): {miss} / {len(flat_hits)} = {100.0*miss/len(flat_hits):.1f}%")

# Dump hit-ID grid (one char per cell, hex)
out_dir = os.path.dirname(__file__)
def hit_char(mid):
    if mid is None or mid == -1:
        return '.'
    if mid < 16:
        return f"{mid:x}"
    if mid < 36:
        return chr(ord('g') + (mid - 16))
    return '#'

with open(os.path.join(out_dir, "_debug_hits.txt"), "w", encoding="utf-8") as f:
    f.write(f"# Hit-ID grid {WIDTH}x{render_h}, '.'=miss, 0-9a-z=mat_id\n")
    f.write(f"# pos=({px},{py},{pz}) yaw={yaw_deg} pitch={pitch_deg}\n")
    for row in hits:
        f.write("".join(hit_char(h) for h in row) + "\n")

# Dump ASCII brightness preview (so I can SEE the scene)
DENSITY = " .:-=+*#%@"
with open(os.path.join(out_dir, "_debug_ascii.txt"), "w", encoding="utf-8") as f:
    f.write(f"# ASCII brightness preview {WIDTH}x{HEIGHT} (collapsed half-blocks)\n")
    for cy in range(HEIGHT):
        line = []
        for x in range(WIDTH):
            top = colors[cy * 2][x]
            bot = colors[cy * 2 + 1][x] if cy * 2 + 1 < render_h else top
            avg = ((top[0] + bot[0]) * 0.299 +
                   (top[1] + bot[1]) * 0.587 +
                   (top[2] + bot[2]) * 0.114) * 0.5 / 255
            idx = min(len(DENSITY) - 1, int(avg * len(DENSITY)))
            line.append(DENSITY[idx])
        f.write("".join(line) + "\n")

# Dump color grid (compact RGB)
with open(os.path.join(out_dir, "_debug_color.txt"), "w", encoding="utf-8") as f:
    f.write(f"# Color grid {WIDTH}x{render_h}, RRGGBB hex per pixel\n")
    for row in colors:
        f.write(" ".join(f"{r:02x}{g:02x}{b:02x}" for (r, g, b) in row) + "\n")

# Center-pixel detail
cx, cy = WIDTH // 2, render_h // 2
print(f"\nCenter pixel ({cx},{cy}):")
print(f"  hit_id = {hits[cy][cx]}")
print(f"  color  = {colors[cy][cx]}")
print(f"  depth  = {depths[cy][cx]}")

ray = cam.get_ray_dir(cx, cy, WIDTH, render_h)
print(f"  ray    = ({ray.x:+.3f}, {ray.y:+.3f}, {ray.z:+.3f})")

print(f"\nWrote: _debug_hits.txt  _debug_ascii.txt  _debug_color.txt")
