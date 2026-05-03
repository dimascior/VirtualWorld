"""Convert renderer debug output into an SVG screenshot for the README.

Reads colors.csv (x, y, top_r, top_g, top_b, bot_r, bot_g, bot_b) and
meta.json from the _debug_tiles/latest/ directory and produces a static
SVG image where each character cell becomes two vertically-stacked colored
rectangles — exactly matching what the ANSI half-block renderer displays.

Adjacent cells of the same color within a row are merged (RLE) to keep the
SVG compact even at full resolution.

Usage:
    python render_engine_v2/gen_screenshot_svg.py
    python render_engine_v2/gen_screenshot_svg.py --csv PATH --meta PATH --out PATH
    python render_engine_v2/gen_screenshot_svg.py --cell-w 10 --pixel-h 8

Output: render_engine_v2/render_screenshot.svg  (default)
"""

import argparse
import csv
import itertools
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

# Default pixel dimensions.
# cell_w = width in SVG pixels of one character column.
# pixel_h = height in SVG pixels of one half-pixel row.
# A 60-column x 20-row grid becomes  60*cell_w  x  20*pixel_h*2  SVG pixels.
# Defaults give 540 x 360, which is a clean 3:2 with square pixels.
DEFAULT_CELL_W  = 9
DEFAULT_PIXEL_H = 9


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _rle(row):
    """Yield (start_index, run_length, value) for a list."""
    for value, group in itertools.groupby(enumerate(row), key=lambda t: t[1]):
        items = list(group)
        yield items[0][0], len(items), value


def _xml(s):
    return (s.replace("&", "&amp;")
             .replace("<", "&lt;")
             .replace(">", "&gt;")
             .replace('"', "&quot;"))


# ---------------------------------------------------------------------------
# SVG builder
# ---------------------------------------------------------------------------

def build_svg(cells, meta, cell_w, pixel_h):
    """
    cells : dict (x, y) -> {"top": (r,g,b), "bot": (r,g,b)}
    meta  : dict from meta.json  (may be empty)
    Returns SVG text string, or None if cells is empty.
    """
    if not cells:
        return None

    cols = max(x for x, _ in cells) + 1
    rows = max(y for _, y in cells) + 1
    cell_h   = pixel_h * 2
    title_h  = cell_h                    # one cell-height for the info bar
    svg_w    = cols * cell_w
    svg_h    = rows * cell_h + title_h
    offset_y = title_h                   # render area starts below the title bar

    yaw   = meta.get("yaw_deg",   0.0)
    pitch = meta.get("pitch_deg", 0.0)
    pos   = meta.get("pos",       [0.0, 0.0, 0.0])
    frame = meta.get("frame",     0)
    title = (
        f"BEDROOM 3D  "
        f"frame:{frame}  "
        f"yaw:{yaw:.1f}  "
        f"pitch:{pitch:.1f}  "
        f"pos:({pos[0]:.2f}, {pos[1]:.2f}, {pos[2]:.2f})"
    )

    # Pre-build row arrays so we can do RLE in one pass.
    top_rows = [[None] * cols for _ in range(rows)]
    bot_rows = [[None] * cols for _ in range(rows)]
    black = (0, 0, 0)
    for (x, y), cell in cells.items():
        top_rows[y][x] = cell["top"]
        bot_rows[y][x] = cell["bot"]
    # Fill any gaps with black
    for y in range(rows):
        for x in range(cols):
            if top_rows[y][x] is None:
                top_rows[y][x] = black
            if bot_rows[y][x] is None:
                bot_rows[y][x] = black

    out = []
    out.append(
        f'<svg xmlns="http://www.w3.org/2000/svg"'
        f' width="{svg_w}" height="{svg_h}"'
        f' style="display:block;background:#111">'
    )

    # Title bar background
    out.append(
        f'<rect x="0" y="0" width="{svg_w}" height="{title_h}" fill="#1a1a1a"/>'
    )
    # Title text (vertically centred in the title bar)
    text_y = title_h // 2
    out.append(
        f'<text x="6" y="{text_y}"'
        f' dominant-baseline="middle"'
        f' font-family="monospace,Consolas,Courier New"'
        f' font-size="11" fill="#9a9a9a">'
        f'{_xml(title)}</text>'
    )

    # Render rows — two half-pixel rows per character row, RLE per half-row
    for y in range(rows):
        py_top = offset_y + y * cell_h
        py_bot = py_top + pixel_h

        for start_x, run_len, color in _rle(top_rows[y]):
            r, g, b = color
            x_px = start_x * cell_w
            w_px = run_len  * cell_w
            out.append(
                f'<rect x="{x_px}" y="{py_top}"'
                f' width="{w_px}" height="{pixel_h}"'
                f' fill="#{r:02x}{g:02x}{b:02x}"/>'
            )

        for start_x, run_len, color in _rle(bot_rows[y]):
            r, g, b = color
            x_px = start_x * cell_w
            w_px = run_len  * cell_w
            out.append(
                f'<rect x="{x_px}" y="{py_bot}"'
                f' width="{w_px}" height="{pixel_h}"'
                f' fill="#{r:02x}{g:02x}{b:02x}"/>'
            )

    out.append("</svg>")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(
        description="Generate SVG screenshot from renderer colors.csv"
    )
    ap.add_argument(
        "--csv",
        default=os.path.join(HERE, "_debug_tiles", "latest", "colors.csv"),
        help="Path to colors.csv  (default: _debug_tiles/latest/colors.csv)",
    )
    ap.add_argument(
        "--meta",
        default=os.path.join(HERE, "_debug_tiles", "latest", "meta.json"),
        help="Path to meta.json   (default: _debug_tiles/latest/meta.json)",
    )
    ap.add_argument(
        "--out",
        default=os.path.join(HERE, "render_screenshot.svg"),
        help="Output SVG path     (default: render_screenshot.svg)",
    )
    ap.add_argument("--cell-w",  type=int, default=DEFAULT_CELL_W,
                    help=f"SVG pixels per character column (default {DEFAULT_CELL_W})")
    ap.add_argument("--pixel-h", type=int, default=DEFAULT_PIXEL_H,
                    help=f"SVG pixels per half-pixel row  (default {DEFAULT_PIXEL_H})")
    args = ap.parse_args()

    if not os.path.isfile(args.csv):
        print(f"ERROR: {args.csv} not found", file=sys.stderr)
        return 1

    meta = {}
    if os.path.isfile(args.meta):
        with open(args.meta, encoding="utf-8") as f:
            meta = json.load(f)

    cells = {}
    with open(args.csv, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            x, y = int(row["x"]), int(row["y"])
            cells[(x, y)] = {
                "top": (int(row["top_r"]), int(row["top_g"]), int(row["top_b"])),
                "bot": (int(row["bot_r"]), int(row["bot_g"]), int(row["bot_b"])),
            }

    svg = build_svg(cells, meta, args.cell_w, args.pixel_h)
    if not svg:
        print("ERROR: no cells to render — is colors.csv empty?", file=sys.stderr)
        return 1

    out_dir = os.path.dirname(args.out)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

    with open(args.out, "w", encoding="utf-8") as f:
        f.write(svg)

    size_kb = os.path.getsize(args.out) / 1024
    cols = max(x for x, _ in cells) + 1
    rows = max(y for _, y in cells) + 1
    svg_w = cols * args.cell_w
    svg_h = rows * args.pixel_h * 2 + args.pixel_h * 2
    print(
        f"Written: {args.out}\n"
        f"  Size:  {size_kb:.1f} KB\n"
        f"  Grid:  {cols}x{rows} cells -> {svg_w}x{svg_h} SVG pixels\n"
        f"  Cells: {len(cells)}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
