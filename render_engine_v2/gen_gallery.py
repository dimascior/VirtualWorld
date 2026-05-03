"""Render multiple camera poses headlessly and produce one SVG per pose.

No running renderer required.  Loads the renderer source the same way
driver.py does (exec up to '# === MAIN ==='), then calls
render_frame_enhanced() directly for each pose, and writes an SVG using
gen_screenshot_svg.build_svg().

Usage:
    python render_engine_v2/gen_gallery.py
    python render_engine_v2/gen_gallery.py --out-dir render_engine_v2/screenshots
    python render_engine_v2/gen_gallery.py --cell-w 9 --pixel-h 9

Output files (in out-dir):
    00_default.svg
    01_look_at_bed.svg
    02_look_at_desk_window.svg
    03_looking_back_from_door.svg
    04_ceiling_view.svg
    05_corner_closeup.svg
    gallery.md     -- Markdown snippet you can paste into the README
"""

import sys, os, math, argparse

HERE    = os.path.dirname(os.path.abspath(__file__))
PARENT  = os.path.dirname(HERE)

# ---------------------------------------------------------------------------
# Load renderer source headlessly (same pattern as driver.py)
# ---------------------------------------------------------------------------
os.environ.setdefault("DBG_DISABLE", "1")

_src_path = os.path.join(HERE, "test_bedroom_enhanced.py")
with open(_src_path, "r", encoding="utf-8") as _f:
    _src = _f.read()
_ns = {"__name__": "_headless", "__file__": _src_path}
exec(compile(_src[: _src.index("# === MAIN ===")], _src_path, "exec"), _ns)

Vec3                 = _ns["Vec3"]
Camera               = _ns["Camera"]
create_bedroom       = _ns["create_bedroom"]
render_frame_enhanced= _ns["render_frame_enhanced"]
MOVE_SPEED           = _ns["MOVE_SPEED"]
TURN_SPEED           = _ns["TURN_SPEED"]
PITCH_SPEED          = _ns["PITCH_SPEED"]
WIDTH                = _ns["WIDTH"]
HEIGHT               = _ns["HEIGHT"]

# ---------------------------------------------------------------------------
# Import SVG builder from sibling module
# ---------------------------------------------------------------------------
sys.path.insert(0, HERE)
from gen_screenshot_svg import build_svg  # noqa: E402

# ---------------------------------------------------------------------------
# Interesting camera poses
# Each pose is:
#   label     - filename stem (spaces -> underscores)
#   pos       - (x, y, z)   standing position
#   yaw_deg   - horizontal rotation; 0 = +Z, 90 = +X
#   pitch_deg - vertical tilt; positive = look down
#   caption   - human-readable description for the gallery
# ---------------------------------------------------------------------------
POSES = [
    {
        "label":     "00_default",
        "pos":       (0.0,  1.6,  0.5),
        "yaw_deg":   0.0,
        "pitch_deg": 0.0,
        "caption":   "Default starting view",
    },
    {
        "label":     "01_bed_closeup",
        "pos":       (1.2,  1.6,  1.5),
        "yaw_deg":   -35.0,
        "pitch_deg": 12.0,
        "caption":   "Close-up of the bed",
    },
    {
        "label":     "02_desk_window",
        "pos":       (-1.5, 1.6,  1.2),
        "yaw_deg":   45.0,
        "pitch_deg": 0.0,
        "caption":   "Desk and window area",
    },
    {
        "label":     "03_doorway_looking_in",
        "pos":       (0.0,  1.6,  3.2),
        "yaw_deg":   180.0,
        "pitch_deg": 5.0,
        "caption":   "From the doorway looking into the room",
    },
    {
        "label":     "04_ceiling_glance",
        "pos":       (0.0,  1.6,  1.5),
        "yaw_deg":   0.0,
        "pitch_deg": -55.0,
        "caption":   "Looking up at the ceiling",
    },
    {
        "label":     "05_floor_corner",
        "pos":       (-1.8, 1.4,  0.3),
        "yaw_deg":   30.0,
        "pitch_deg": 35.0,
        "caption":   "Low-angle corner shot",
    },
    {
        "label":     "06_wide_sweep",
        "pos":       (0.0,  1.8,  1.5),
        "yaw_deg":   -90.0,
        "pitch_deg": 0.0,
        "caption":   "Wide sweep - side wall",
    },
]

# ---------------------------------------------------------------------------
# Renderer setup
# ---------------------------------------------------------------------------
bedroom = create_bedroom()
light   = Vec3(0, 2.7, 2)


def _make_camera(pos, yaw_deg, pitch_deg):
    cam       = Camera()
    cam.pos   = Vec3(*pos)
    cam.yaw   = math.radians(yaw_deg)
    cam.pitch = math.radians(pitch_deg)
    return cam


def _tile_grid_to_cells(tile_grid):
    """Convert HEIGHT x WIDTH list-of-lists into {(x,y): {top,bot}} dict."""
    cells = {}
    for y, row in enumerate(tile_grid):
        for x, cell in enumerate(row):
            if cell is None:
                continue
            top_mat, bot_mat, top_color, bot_color = cell
            cells[(x, y)] = {"top": top_color, "bot": bot_color}
    return cells


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="Render gallery of SVG screenshots")
    ap.add_argument("--out-dir",  default=os.path.join(HERE, "screenshots"))
    ap.add_argument("--cell-w",  type=int, default=9)
    ap.add_argument("--pixel-h", type=int, default=9)
    args = ap.parse_args()

    os.makedirs(args.out_dir, exist_ok=True)

    gallery_rows = []

    for i, pose in enumerate(POSES):
        label     = pose["label"]
        caption   = pose["caption"]
        yaw_deg   = pose["yaw_deg"]
        pitch_deg = pose["pitch_deg"]

        cam = _make_camera(pose["pos"], yaw_deg, pitch_deg)

        print(f"[{i+1}/{len(POSES)}] rendering {label} ...")
        _, _, ascii_preview, tile_grid = render_frame_enhanced(cam, bedroom, light, i)

        cells = _tile_grid_to_cells(tile_grid)
        meta  = {
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

        # Also print ASCII preview to stdout for a quick sanity check
        for row in ascii_preview:
            print("   " + "".join(row))
        print()

        # Collect gallery row (relative path from readme location)
        rel = os.path.relpath(out_path, HERE).replace("\\", "/")
        gallery_rows.append((rel, caption, label, yaw_deg, pitch_deg, pose["pos"]))

    # Write gallery.md snippet
    md_path = os.path.join(args.out_dir, "gallery.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("## Gallery\n\n")
        f.write("| View | Description |\n")
        f.write("|------|-------------|\n")
        for rel, caption, label, yaw, pitch, pos in gallery_rows:
            px, py, pz = pos
            f.write(
                f'| ![{caption}]({rel}) '
                f'| **{caption}** '
                f'`yaw {yaw:+.0f} pitch {pitch:+.0f} '
                f'pos ({px:.1f}, {py:.1f}, {pz:.1f})` |\n'
            )
    print(f"Gallery snippet: {md_path}")
    print("Done.")


if __name__ == "__main__":
    main()
