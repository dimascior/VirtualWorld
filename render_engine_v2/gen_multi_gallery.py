"""
Headless multi-environment gallery renderer.

Renders curated camera poses across all six environments and writes one SVG
per pose plus a gallery.md snippet.  No running renderer or msvcrt required
-- environments.py and frame_pipeline.py are pure Python with no platform
imports, so they load cleanly in any headless context.

Architecture mirrors gen_gallery.py but targets the FramePipeline /
TerminalCompositor system used by test_multi_environment.py instead of the
bedroom-specific render_frame_enhanced() API.

Buffer layout (FramePipeline):
    pipeline = FramePipeline(160, 40)   base_height * 2 = 80 subpixel rows
    buffer[y][x]  -- y in 0..79, x in 0..159
    Character row i: top pixel = buffer[2*i][x], bot pixel = buffer[2*i+1][x]

SVG cell format (passed to build_svg from gen_screenshot_svg):
    cells[(x, i)] = {"top": (r,g,b), "bot": (r,g,b)}   x in 0..159, i in 0..39
    cell_w=5, pixel_h=5  ->  800 x 200 px render area + 18 px title = 800 x 218 px

Usage:
    python render_engine_v2/gen_multi_gallery.py
    python render_engine_v2/gen_multi_gallery.py --out-dir render_engine_v2/screenshots
"""

import sys, os, math, argparse

HERE   = os.path.dirname(os.path.abspath(__file__))
PARENT = os.path.dirname(HERE)
sys.path.insert(0, PARENT)

from render_engine_v2.environments import get_environment, list_environments
from render_engine_v2.frame_pipeline import FramePipeline
from render_engine_v2.gen_screenshot_svg import build_svg

# ---------------------------------------------------------------------------
# Camera (verbatim from test_multi_environment.py so behaviour is identical)
# ---------------------------------------------------------------------------
class Camera:
    __slots__ = ['x', 'y', 'z', 'yaw', 'pitch', 'fov',
                 '_cos_yaw', '_sin_yaw', '_cos_pitch', '_sin_pitch', '_fov_tan']

    def __init__(self, x=0, y=1.6, z=0):
        self.x, self.y, self.z = x, y, z
        self.yaw   = 0.0
        self.pitch = 0.0
        self.fov   = 90
        self.update_trig()

    def update_trig(self):
        self._cos_yaw   = math.cos(self.yaw)
        self._sin_yaw   = math.sin(self.yaw)
        self._cos_pitch = math.cos(self.pitch)
        self._sin_pitch = math.sin(self.pitch)
        self._fov_tan   = math.tan(self.fov * math.pi / 360)

    def get_ray(self, px, py, width, height):
        nx = (px - width  * 0.5) / width  * 2
        ny = (py - height * 0.5) / height * 2
        aspect = width / height * 2.2
        dx = nx * self._fov_tan * aspect
        dy = -ny * self._fov_tan
        dz = 1
        rx  =  dx * self._cos_yaw + dz * self._sin_yaw
        rz  = -dx * self._sin_yaw + dz * self._cos_yaw
        ry  = dy * self._cos_pitch - rz  * self._sin_pitch
        rz2 = dy * self._sin_pitch + rz  * self._cos_pitch
        length = math.sqrt(rx*rx + ry*ry + rz2*rz2)
        return rx/length, ry/length, rz2/length


# ---------------------------------------------------------------------------
# Render dimensions (matching test_multi_environment.py constants)
# ---------------------------------------------------------------------------
RENDER_WIDTH  = 160   # 2 * 80 terminal columns
RENDER_HEIGHT = 80    # 2 * 40 terminal rows  (pipeline stores as base_height=40, *2 internally)


# ---------------------------------------------------------------------------
# Curated poses
# Format:
#   env       - key passed to get_environment()
#   label     - filename stem
#   pos       - (x, y, z)
#   yaw_deg   - 0 = looking +Z, 90 = looking +X
#   pitch_deg - positive = looking down
#   caption   - gallery text
# ---------------------------------------------------------------------------
POSES = [
    # ---- BEDROOM (env v1 via multi-env system) ----------------------------
    {
        "env": "bedroom", "label": "env_00_bedroom_entrance",
        "pos": (0.0, 1.6, 0.5), "yaw_deg": 0.0, "pitch_deg": 0.0,
        "caption": "Bedroom -- standing at the entrance",
    },
    {
        "env": "bedroom", "label": "env_01_bedroom_bed",
        "pos": (1.0, 1.6, 2.5), "yaw_deg": -25.0, "pitch_deg": 10.0,
        "caption": "Bedroom -- looking across the bed",
    },

    # ---- OFFICE -----------------------------------------------------------
    {
        "env": "office", "label": "env_02_office_floor",
        "pos": (0.0, 1.6, 0.0), "yaw_deg": 0.0, "pitch_deg": 0.0,
        "caption": "Office -- open floor view",
    },
    {
        "env": "office", "label": "env_03_office_corner",
        "pos": (-1.5, 1.6, 2.0), "yaw_deg": 35.0, "pitch_deg": 5.0,
        "caption": "Office -- cubicle corner angle",
    },

    # ---- SCI-FI CORRIDOR --------------------------------------------------
    {
        "env": "corridor", "label": "env_04_corridor_centre",
        "pos": (0.0, 1.6, 0.0), "yaw_deg": 0.0, "pitch_deg": 0.0,
        "caption": "Corridor -- centre of the sci-fi hallway",
    },
    {
        "env": "corridor", "label": "env_05_corridor_glow",
        "pos": (0.8, 1.2, 2.0), "yaw_deg": -20.0, "pitch_deg": -5.0,
        "caption": "Corridor -- glowing panel close-up",
    },

    # ---- OUTDOOR PARK -----------------------------------------------------
    {
        "env": "park", "label": "env_06_park_overview",
        "pos": (0.0, 1.6, 0.0), "yaw_deg": 0.0, "pitch_deg": 0.0,
        "caption": "Park -- open-air overview",
    },
    {
        "env": "park", "label": "env_07_park_pond",
        "pos": (2.0, 1.6, 3.0), "yaw_deg": -45.0, "pitch_deg": 12.0,
        "caption": "Park -- water and pond edge",
    },

    # ---- DUNGEON ----------------------------------------------------------
    {
        "env": "dungeon", "label": "env_08_dungeon_entry",
        "pos": (0.0, 1.6, 0.5), "yaw_deg": 0.0, "pitch_deg": 0.0,
        "caption": "Dungeon -- torchlit entry corridor",
    },
    {
        "env": "dungeon", "label": "env_09_dungeon_torch",
        "pos": (-0.8, 1.6, 1.8), "yaw_deg": 15.0, "pitch_deg": -8.0,
        "caption": "Dungeon -- torch flame from below",
    },

    # ---- ABSTRACT VOID ----------------------------------------------------
    {
        "env": "abstract", "label": "env_10_abstract_centre",
        "pos": (0.0, 1.6, 0.0), "yaw_deg": 0.0, "pitch_deg": 0.0,
        "caption": "Abstract -- floating shapes, centre void",
    },
    {
        "env": "abstract", "label": "env_11_abstract_low",
        "pos": (0.0, 0.5, 0.0), "yaw_deg": 45.0, "pitch_deg": -20.0,
        "caption": "Abstract -- low angle, looking up through shapes",
    },
]


# ---------------------------------------------------------------------------
# Render helpers
# ---------------------------------------------------------------------------
def _make_camera(pos, yaw_deg, pitch_deg):
    cam = Camera(*pos)
    cam.yaw   = math.radians(yaw_deg)
    cam.pitch = math.radians(pitch_deg)
    cam.update_trig()
    return cam


def _render_pose(env, cam):
    """Render one pose.  Returns buffer[y][x] = (r, g, b)."""
    pipeline = FramePipeline(RENDER_WIDTH, RENDER_HEIGHT // 2)
    pipeline.min_scale = 1.0
    pipeline.scale     = 1.0

    cx, cy, cz = cam.x, cam.y, cam.z

    def trace_pixel(px, py, width, height):
        dx, dy, dz = cam.get_ray(px, py, width, height)
        return env.trace(cx, cy, cz, dx, dy, dz)

    pipeline.begin_frame()
    pipeline.render_full(trace_pixel)
    pipeline.end_frame()
    return pipeline.get_buffer()   # 80 rows x 160 cols


def _buffer_to_cells(buffer):
    """Map FramePipeline subpixel buffer to build_svg cell dict.

    buffer has RENDER_HEIGHT rows (80) x RENDER_WIDTH cols (160).
    Character row i (0..39): top = buffer[2*i], bot = buffer[2*i+1].
    """
    cells = {}
    char_rows = len(buffer) // 2
    cols      = len(buffer[0]) if buffer else 0
    for i in range(char_rows):
        top_row = buffer[2 * i]
        bot_row = buffer[2 * i + 1]
        for x in range(cols):
            cells[(x, i)] = {"top": top_row[x], "bot": bot_row[x]}
    return cells


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="Render multi-environment SVG gallery")
    ap.add_argument("--out-dir",  default=os.path.join(HERE, "screenshots"))
    ap.add_argument("--cell-w",  type=int, default=5)
    ap.add_argument("--pixel-h", type=int, default=5)
    args = ap.parse_args()

    os.makedirs(args.out_dir, exist_ok=True)

    gallery_rows = []
    env_cache = {}   # avoid rebuilding geometry for consecutive same-env poses

    for i, pose in enumerate(POSES):
        env_name  = pose["env"]
        label     = pose["label"]
        caption   = pose["caption"]
        yaw_deg   = pose["yaw_deg"]
        pitch_deg = pose["pitch_deg"]

        if env_name not in env_cache:
            env_cache[env_name] = get_environment(env_name)
        env = env_cache[env_name]

        cam = _make_camera(pose["pos"], yaw_deg, pitch_deg)

        print(f"[{i+1:2}/{len(POSES)}] {label}")
        buffer = _render_pose(env, cam)
        cells  = _buffer_to_cells(buffer)
        meta   = {
            "frame":     i,
            "yaw_deg":   yaw_deg,
            "pitch_deg": pitch_deg,
            "pos":       list(pose["pos"]),
        }

        svg      = build_svg(cells, meta, args.cell_w, args.pixel_h)
        out_path = os.path.join(args.out_dir, f"{label}.svg")
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(svg)

        size_kb = os.path.getsize(out_path) / 1024
        print(f"   -> {out_path}  ({size_kb:.1f} KB)")

        rel = os.path.relpath(out_path, HERE).replace("\\", "/")
        px, py, pz = pose["pos"]
        gallery_rows.append((rel, caption, label, yaw_deg, pitch_deg, pose["pos"], env_name))

    # Write gallery snippet
    md_path = os.path.join(args.out_dir, "multi_gallery.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("## Environments Gallery\n\n")
        f.write("> Six distinct worlds rendered through the same ray-trace pipeline.\n\n")

        current_env = None
        for rel, caption, label, yaw, pitch, pos, env_name in gallery_rows:
            if env_name != current_env:
                current_env = env_name
                f.write(f"\n### {env_name.capitalize()}\n\n")
                f.write("| View | Description |\n")
                f.write("|------|-------------|\n")
            px, py, pz = pos
            f.write(
                f'| ![{caption}]({rel}) '
                f'| **{caption}** '
                f'`yaw {yaw:+.0f} pitch {pitch:+.0f} '
                f'pos ({px:.1f}, {py:.1f}, {pz:.1f})` |\n'
            )

    print(f"\nGallery snippet: {md_path}")
    print(f"Done. {len(POSES)} poses rendered.")


if __name__ == "__main__":
    main()
