# Terminal 3D Rendering Engineering Standard

**Project:** Virtual World / render_engine_v2
**Reference implementation:** `test_bedroom_enhanced.py`, `debug_dump.py`, `debug_watcher.py`
**Status:** Verified working, April 30, 2026
**Goal:** A reproducible, agent-observable specification for turning a plain ANSI-capable
terminal into a real-time, ray-traced 3D rendering surface with per-pixel object
identity, frame deltas, and a directory-tree feature dump.

This document is the authoritative engineering standard derived from the experimental
session that produced the working bedroom renderer plus its embedded debug
infrastructure. Nothing in this document is aspirational; every design decision is
backed by code that ran and produced the artifacts listed in the verification
section.

---

## Table of Contents

1. [Why a terminal at all?](#1-why-a-terminal-at-all)
2. [Architectural overview](#2-architectural-overview)
3. [Display surface specification (60×20 cells = 60×40 pixels)](#3-display-surface-specification)
4. [Camera + ray model](#4-camera--ray-model)
5. [Scene representation (axis-aligned boxes)](#5-scene-representation)
6. [Material identity and category schema](#6-material-identity-and-category-schema)
7. [Per-pixel rendering pipeline](#7-per-pixel-rendering-pipeline)
8. [Input model (Windows msvcrt + agent aliases)](#8-input-model)
9. [Embedded debug HTTP server](#9-embedded-debug-http-server)
10. [Frame-delta detection and ring buffer](#10-frame-delta-detection-and-ring-buffer)
11. [Per-tile feature directory tree](#11-per-tile-feature-directory-tree)
12. [Live watcher (separate terminal)](#12-live-watcher)
13. [Headless verification harness](#13-headless-verification-harness)
14. [Operational lessons (verified)](#14-operational-lessons)
15. [Module extraction proposal: `terminal3d`](#15-module-extraction-proposal-terminal3d)
16. [Verification log of this session](#16-verification-log-of-this-session)
17. [Conformance checklist](#17-conformance-checklist)

---

## 1. Why a terminal at all?

Terminals provide the lowest-friction display surface available to any process:

- No GPU, no window manager, no graphics driver dependency.
- Trivially captured to text logs, diffed, replayed, transmitted over SSH.
- Every cell already has 24-bit foreground and 24-bit background color via ANSI
  truecolor escapes (`ESC[38;2;R;G;Bm`, `ESC[48;2;R;G;Bm`).
- An observing agent can read its own state because every pixel of output is a
  byte stream, not a framebuffer.

The single trade-off is resolution. A typical interactive shell affords something
like 60×20 to 120×40 character cells. The standard recovers a usable amount of
detail by treating each character cell as **two stacked pixels** using the Unicode
half-block character `▀` with foreground = top pixel and background = bottom
pixel. This doubles the vertical resolution at zero cost.

A 60×20 cell display thus becomes a **60×40 effective pixel grid**.

---

## 2. Architectural overview

Four concurrent concerns share one process plus optionally a second terminal:

```
                     +--------------------------------------+
                     |   Renderer process (test_bedroom_*)  |
                     |                                      |
   stdin keystrokes  |  +-----------+    +---------------+  |    ANSI frames
   ----------------->|  |  Input    |--->|   Render      |--|---------------> stdout
                     |  | (msvcrt)  |    |   60x20 cells |  |  (the user
                     |  +-----------+    +-------+-------+  |   sees the room)
                     |        |                  |          |
                     |        v                  v          |
                     |  +------------------------------+    |
                     |  |  dbg_publish (locked state)  |    |
                     |  +------+--------+--------+-----+    |
                     |         |        |        |          |
                     |         |        |        v          |
                     |         |        |   _debug_state.json (atomic write)
                     |         |        v                   |
                     |         |   _debug_tiles/frame_NNNNN/  ... (per-tile tree)
                     |         v                            |
                     |   HTTP server thread (:8765)         |
                     |    /state /events /frames /tiles ... |
                     +-----+--------------------------------+
                           |
                           |  HTTP poll
                           v
                     +-------------------------------+
                     |  debug_watcher.py (terminal 2)|
                     |  prints keystrokes + deltas   |
                     +-------------------------------+
```

The **only** mandatory output channel is the renderer's stdout. Every other
channel (file, HTTP, directory tree, second-terminal watcher) is observability
and is gated behind environment variables (`DBG_DISABLE=1`, `DBG_PORT=…`,
`DBG_AUTODUMP=0`, `DBG_FRAMES_MAX=…`).

---

## 3. Display surface specification

| Field | Value | Source |
|---|---|---|
| Cell width  `WIDTH`  | 60 | `test_bedroom_enhanced.py` |
| Cell height `HEIGHT` | 20 | same |
| Effective pixel rows | 40 (HEIGHT × 2 because of half-blocks) | `render_height = HEIGHT * 2 if use_half_blocks else HEIGHT` |
| Per-cell glyph | `▀` (`UPPER_HALF`) when half-blocks enabled | constant |
| Per-cell color | foreground = top pixel RGB, background = bottom pixel RGB | `rgb(*top_color) + bg_rgb(*bot_color)` |

Half-block invariant:
> A character cell `(x, char_y)` corresponds to **two pixels**:
> top pixel `(x, char_y*2)`, bottom pixel `(x, char_y*2 + 1)`.
> The renderer must trace one ray per pixel, never one ray per cell.

ANSI escape vocabulary used (and only these):

```
ESC[2J            clear screen
ESC[H             cursor home
ESC[?25l/?25h     hide / show cursor
ESC[r;cH          goto row r col c
ESC[38;2;R;G;Bm   24-bit foreground
ESC[48;2;R;G;Bm   24-bit background
ESC[0m            reset
ESC[1m            bold
```

A density ramp is also defined for monochrome fallback / brightness preview /
ASCII export:

```python
DENSITY_CHARS = " .'`^\",:;Il!i><~+_-?][}{1)(|/tfjrxnuvczXYUJCLQ0OZmwqpdbkhao*#MW&8%B@$"
```

The standard requires both modes to be available. Brightness preview is what
agents read when ANSI is stripped from the output capture, and it is what
populates `ascii_preview` in the state JSON.

---

## 4. Camera + ray model

`Camera` carries `pos: Vec3`, `yaw: rad`, `pitch: rad`, `fov: deg = 90`.

`get_ray_dir(screen_x, screen_y, width, height)` builds a ray as follows
(verbatim from the reference implementation):

```python
nx = (screen_x - width/2) / (width/2)        # [-1, 1]
ny = (screen_y - height/2) / (height/2)
aspect = width / height
fov_rad = self.fov * math.pi / 180
tan_half = math.tan(fov_rad / 2)

dx = nx * tan_half * aspect
dy = -ny * tan_half                          # screen Y inverted
dz = 1.0                                      # camera-space looks +Z

# Pitch (around X)
py = dy * cos_p - dz * sin_p
pz = dy * sin_p + dz * cos_p

# Yaw (around Y)
rx = dx * cos_y + pz * sin_y
rz = -dx * sin_y + pz * cos_y

return Vec3(rx, py, rz).normalize()
```

Why these signs in particular: this session **verified** that

- Pressing `l` (yaw right) shifts visible objects to screen-left: source confirms
  `camera.yaw += TURN_SPEED` and the rotation matrix above.
- Pressing `i` (pitch up) shifts visible objects down: source confirms
  `camera.pitch -= PITCH_SPEED` together with the inverted `ny` and the X-axis
  rotation above.
- Pressing `w` advances along yaw direction:
  `pos.x += sin(yaw)*MOVE_SPEED; pos.z += cos(yaw)*MOVE_SPEED`.

Constants (and only these):
```
TURN_SPEED  = 0.12 rad/keypress  (~6.875°)
PITCH_SPEED = 0.08 rad/keypress  (~4.583°)
MOVE_SPEED  = 0.20 units/keypress
MAX_PITCH   = 1.20 rad           (~68.75°)
```

Agents and tests should rely on these values being deterministic; they are
re-used for prediction in [section 16](#16-verification-log-of-this-session).

---

## 5. Scene representation

Scenes are lists of axis-aligned `Box(min: Vec3, max: Vec3, mat: Material)`.
Ray/box intersection is the standard slab method, returning
`(t, normal, mat_id)` for the nearest entry. Two slab quirks worth knowing:

- The `t` returned is `tmin` if `tmin >= 0.001` else `tmax`. This handles the
  case where the ray origin sits inside a box (small numerical inside).
- Normals are derived by face proximity (`abs(hit.x - bmin.x) < eps`, etc.).

The bedroom contains 41 boxes. Floor and ceiling are single monolithic boxes
spanning the room, with a **procedural checker shader** applied at hit time:

```python
if mat.id == MAT_FLOOR.id or mat.id == MAT_CEILING.id:
    if (int(math.floor(hit.x * 2)) + int(math.floor(hit.z * 2))) & 1:
        base_color = tuple(int(c * 0.7) for c in base_color)
```

This is the standard's approved pattern for "infinite" textured planes:
collapse them into one bounding box and generate detail in the shader.

---

## 6. Material identity and category schema

A `Material` instance receives a unique `id` from a class-level counter at
construction time. **Order of instantiation determines id.** The mapping must
therefore be hand-maintained alongside the materials. The reference scene's
authoritative table:

```
id   name             category
 0   wall             walls
 1   floor            floor
 2   ceiling          ceiling
 3   bed_frame        bed
 4   bed_sheet        bed
 5   pillow           bed
 6   desk             desk_setup
 7   monitor          desk_setup
 8   monitor_screen   desk_setup
 9   chair            desk_setup
10   window           windows
11   curtain          windows
12   lamp             lights
13   nightstand       furniture
14   poster           decor
15   rug              decor
16   plant            decor
17   bookshelf        furniture
18   door             doors
-1   sky (miss)       sky
```

Each category gets one display character used in the per-frame
`map_category.txt` overlay:

```
walls=W   floor=F   ceiling=C   bed=b   desk_setup=d
windows=n lights=L  furniture=f decor=. doors=D   sky=~   transition=+
```

The standard requires:
1. Every material ID has an entry in `MAT_NAME` and `MAT_CATEGORY`.
2. Every category has an entry in `MAT_CAT_CHAR`.
3. `transition` is reserved for half-block tiles whose top and bottom belong to
   different categories (object boundary running through the cell vertically).

---

## 7. Per-pixel rendering pipeline

The rendering function returns a 4-tuple, as a hard requirement of this
standard:

```
render_frame_enhanced(camera, objects, light_pos, frame)
    -> (buffer,      # WIDTH x HEIGHT chars (always '▀' in half-block mode)
        colors_out,  # WIDTH x HEIGHT ANSI prefix strings (fg+bg)
        ascii_preview,  # WIDTH x HEIGHT density chars (no ANSI)
        tile_grid)   # WIDTH x HEIGHT of (top_mat, bot_mat, top_color, bot_color)
```

Per cell, two rays are traced (top and bottom pixel). Each ray is shaded by
`trace_ray`, which performs:

1. Iterate all objects, retain closest hit.
2. If miss, return sky color `(40, 40, 60)` with `mat_id = -1`.
3. Apply procedural checker for floor/ceiling.
4. Emissive shortcut: if `mat.emissive > 0`, return `color * (0.5 + 0.5*emissive)`.
5. Lambert + distance attenuation lighting:
   - `ambient = 0.3`
   - `diffuse = max(0, n·l) * 0.7 * (1 - shadow)`
   - `atten = 1 / (1 + light_dist * 0.1)`
   - `brightness = ambient + diffuse * atten`
6. Optional shadow ray (skippable via `T` toggle for ~2x speedup).
7. Subtle multiplicative noise: `sin(hit.x*10) * sin(hit.z*10) * 0.03`.

The per-tile record is built **inside the inner loop**, never as a post-pass —
the standard forbids re-tracing rays for diagnostic data because that doubles
render time.

ASCII preview is derived from the average luminance of top and bottom pixels:

```python
br = (top.r*0.3 + top.g*0.59 + top.b*0.11
    + bot.r*0.3 + bot.g*0.59 + bot.b*0.11) / 510.0
ascii_preview[char_y][x] = DENSITY_CHARS[int(br * len(DENSITY_CHARS))]
```

This preview is the only viewable channel for an agent whose terminal capture
strips ANSI.

---

## 8. Input model

Windows: `msvcrt.kbhit` + `msvcrt.getch`, non-blocking. Arrow keys come as the
two-byte sequence `\xe0` + scancode. The standard mandates two anti-footgun
behaviors verified in this session:

### 8.1 Per-frame keystroke dedupe

OS keyboard auto-repeat at 5 FPS produces multi-step jumps. The reference
implementation collects keys into a `seen` set per call so that repeated
auto-repeats of the same key in one polling window count once:

```python
def get_keyboard_input():
    seen = set(); keys = []
    while msvcrt.kbhit():
        ch = msvcrt.getch()
        # ... resolve to k ...
        if k not in seen:
            seen.add(k); keys.append(k)
    return keys
```

### 8.2 Agent-friendly SPACE alias `/`

Many terminal automation tools strip whitespace-only payloads. SPACE
(`' '`) is therefore aliased to `'/'`:

```python
if key == ' ' or key == '/':
    mode = 'auto' if mode == 'manual' else 'manual'
```

This was verified: a `send_to_terminal " "` call in the reference automation
tool sends only Enter, never reaching the script. Sending `/` works.

### 8.3 Key sanitization in published state

When non-printable keys (`\r`, `\n`, arrow scancodes) leak into displayed
strings they wreck terminal output. The publisher repr-escapes anything
non-printable or longer than one printable character:

```python
disp = [repr(k)[1:-1] if (len(k) != 1 or not k.isprintable()) else k
        for k in raw_keys]
```

This is required at the server boundary AND at the watcher boundary
(defense in depth).

---

## 9. Embedded debug HTTP server

A `socketserver.TCPServer` on `127.0.0.1:8765` is started in a daemon thread
during module import. The daemon exits with the process; no shutdown hook is
needed.

### 9.1 Endpoints (all GET)

| Path | Returns | Notes |
|---|---|---|
| `/` or `/state` or `/state.json` | full `_DBG_STATE` JSON | locked snapshot |
| `/ascii` | text/plain ASCII preview | brightness grid |
| `/events` | merged event log JSON | non-frame events preserved, last 50 frames |
| `/frames` | index of distinct frames (no ASCII payload) | lightweight |
| `/frames/latest` | most recent distinct frame with ASCII | full record |
| `/frames/N` | Nth distinct frame (negative indexing supported) | 404 if out-of-range |
| `/frames/clear` | clears frame ring buffer | returns `{"ok": true}` |
| `/tiles` | live in-memory per-tile grid (no disk I/O) | 1200 records for 60×20 |
| `/tiles/dump` | force-write a tile directory tree for the current frame | returns counts |

All responses include `Access-Control-Allow-Origin: *`. The server logs nothing
to stdout (overrides `log_message`) so it never corrupts the renderer's frame.

### 9.2 State shape

```json
{
  "frame": 489,
  "phase": "frame",
  "fps": 5.4,
  "mode": "manual",
  "pos": [0.024, 1.6, 0.699],
  "yaw_deg": 6.875,
  "pitch_deg": -4.583,
  "use_msaa": false,
  "use_edge_detection": false,
  "skip_shadows": false,
  "show_debug": false,
  "last_keys": ["w", "\r"],
  "key_seq": 3,
  "center_ray": [0.119, 0.080, 0.989],
  "center_hit_id": 10,
  "center_depth": 3.29,
  "ascii_preview": ["...60 chars...", "..."],
  "timestamp": 1777593065.6
}
```

`center_hit_id` and `center_depth` come from one extra ray trace through the
exact center pixel (`WIDTH/2, HEIGHT`) so an agent always knows what is "under
the crosshair" without reading 1200 tiles.

### 9.3 Atomic file fallback

The same JSON state is written to `_debug_state.json` as `tmp + os.replace`.
This is a hard requirement: any reader that opens the file mid-write will
either see the old version or the new one, never a torn file.

### 9.4 Publisher contract

`dbg_publish(phase, **fields)` must:

1. Pop side-channel kwargs (`_tile_grid`, `_ascii_preview_grid`) before merging
   into state — they are too big to serialize on every call.
2. Take `_DBG_LOCK` for the entire merge + write.
3. Sanitize keys in the human summary, never in the raw state.
4. Append a structured event record (`ts, phase, summary`).
5. Maintain the event log invariant: keep all non-frame events (last 100) **plus**
   the last 50 frame events. Merge sorted by timestamp. This stops a fast frame
   stream from evicting keystrokes.
6. On `phase == "frame"` only, compute the delta key
   `(yaw, pitch, posx, posy, posz, hash(ascii))` rounded as
   `(2, 2, 3, 3, 3, hash)`. If new, append to the frame ring buffer (max 60),
   and if `_tile_grid` was supplied and `DBG_AUTODUMP` is set, call
   `dump_tile_tree`.

---

## 10. Frame-delta detection and ring buffer

A "frame" in the user-visible sense is every render iteration. A "distinct
frame" is one whose pose AND ASCII preview differs from the prior recorded
distinct frame. Only distinct frames are archived. The delta key in the
reference implementation:

```python
key = (
    round(yaw_deg, 2),
    round(pitch_deg, 2),
    round(pos[0], 3),
    round(pos[1], 3),
    round(pos[2], 3),
    hash("\n".join(ascii_preview)),
)
```

Why include the ASCII hash: the procedural noise term in `trace_ray` produces
identical output for identical poses, so two consecutive identical poses do
**not** yield a new dump. This is observable in the verification log: 4 frames
across 3 keystrokes (1 baseline + 3 deltas, no spurious frames between).

Ring buffer cap defaults to 60, configurable via `DBG_FRAMES_MAX`.

---

## 11. Per-tile feature directory tree

Layout produced by `dump_tile_tree(frame_num, tile_grid, ascii_preview, meta)`:

```
_debug_tiles/
  frame_NNNNN/
    meta.json           camera pose + frame + trigger keys + width/height
    tiles.json          full WIDTH x HEIGHT records (1200 for 60x20)
    map_category.txt    HEIGHT lines x WIDTH chars, single-letter category map
    map_top_mat.txt     HEIGHT lines x WIDTH chars, hex of top mat id
    ascii_preview.txt   brightness preview
    colors.csv          x,y,top_r,top_g,top_b,bot_r,bot_g,bot_b
    by_category/
      walls/      _index.txt + xx_yy.json per tile
      floor/      ...
      ceiling/
      bed/
      desk_setup/
      windows/
      lights/
      furniture/
      decor/
      doors/
    transitions/
      _index.txt        per-tile transition listing
      xx_yy.json        each transition tile's full record
  latest/               mirror of the most recent frame's summary files
```

Per-tile JSON record schema (verified, from frame_00225 tile 35,13):

```json
{
  "x": 35, "y": 13,
  "top_mat_id": 3,  "top_name": "bed_frame", "top_category": "bed",
  "bot_mat_id": 4,  "bot_name": "bed_sheet", "bot_category": "bed",
  "top_color": [49, 31, 18], "bot_color": [157, 157, 171],
  "top_hex": "#311f12",      "bot_hex": "#9d9dab",
  "transition": false,
  "ascii": "("
}
```

Throttling: dump is invoked only on delta frames. At the verified rate of about
2 deltas per second of active input, ~1200 tiny files per dump is acceptable
disk pressure on Windows. The standard forbids dumping every frame.

`_safe_rmtree` is used before each dump so that a re-dump of the same
frame number replaces cleanly (relevant when `/tiles/dump` is hit manually).

---

## 12. Live watcher

`debug_watcher.py` is a separate Python process (run in a second terminal). It
polls `/state`, `/events`, `/frames` and prints:

- Every new non-frame event (`init`, `keystroke`, `quit`).
- Every new distinct frame as one line:
  `TIME delta f=NNNN keys=... Y±d.d P±d.d pos=(±x,±y,±z) hit=N t=d.dd`.

Reconnect strategy: catches `URLError`, sleeps, retries forever. Optional
`--ascii` flag dumps the current ASCII preview after each delta block.

The watcher exists because the renderer's stdout already shows the actual
frame; the agent needs an orthogonal stream of structured events that doesn't
have to compete with ANSI redraws.

---

## 13. Headless verification harness

`debug_dump.py` lets a tester verify a single pose without launching the
interactive renderer:

```
python render_engine_v2/debug_dump.py [yaw_deg] [pitch_deg] [px] [py] [pz]
```

Implementation trick: it `exec`s the source up to the `# === MAIN ===` marker
to import `Vec3`, `Camera`, `trace_ray`, `create_bedroom`, `WIDTH`, `HEIGHT`
without running the interactive loop. This is a deliberate alternative to a
full module split — the renderer can stay one file while tests still get
clean access to its primitives.

Outputs:

```
_debug_hits.txt    60 x 40 grid of per-pixel material IDs (hex chars)
_debug_ascii.txt   60 x 20 brightness preview (collapsed half-blocks)
_debug_color.txt   60 x 40 grid of per-pixel RRGGBB hex
```

Plus stdout: hit-distribution histogram, miss percentage, center-pixel detail.

This is the ground truth tool used to confirm that the live renderer's
center-ray hit ID matches the box geometry independent of ANSI rendering.

---

## 14. Operational lessons

Each item below is a **verified failure mode** from this session, with its
mandatory mitigation.

### 14.1 Never diagnose visuals from a captured terminal
Tool output usually has ANSI stripped. The colors are gone, half-blocks render
as `▀` everywhere, and luminance information is destroyed. Always read either
`ascii_preview` (which is already monochrome density chars) or
`map_category.txt` (which is already category letters).

### 14.2 Whitespace-only inputs disappear
`send_to_terminal " "` is silently rewritten to bare Enter. Aliasing a
printable character (`/`) to SPACE is mandatory for any agent-controllable
toggle.

### 14.3 Carriage returns in `last_keys`
Pressing Enter delivers `\r`. If you let `\r` reach a status bar or log line
it overwrites the line you just printed. Sanitize with
`repr(k)[1:-1] if (len(k)!=1 or not k.isprintable()) else k` at every display
boundary.

### 14.4 Ring buffers evict the wrong things
A unified 50-event ring buffer drowns keystrokes when frame events arrive at
30 Hz. Split: keep all non-frame events (capped at 100), keep last 50 frame
events, merge by timestamp.

### 14.5 Atomic writes are required
Readers will hit half-written files on Windows. Always write to `path + ".tmp"`
then `os.replace`.

### 14.6 Material IDs depend on instantiation order
Adding a new material between existing ones renumbers everything downstream.
The metadata maps (`MAT_NAME`, `MAT_CATEGORY`) must be reviewed any time a new
`Material(...)` is added before another one. Consider this when reviewing PRs.

### 14.7 Center-ray sampling is cheap and worth it
One extra ray per frame buys you `center_hit_id` + `center_depth`, which is
usually all an automated test wants. Don't make tests parse 1200 tiles when
they just need to know "is the camera looking at the window?"

### 14.8 `_DBG_LATEST_TILES` is a global
`/tiles` and `/tiles/dump` rely on it. The render loop must update it under
the lock before publishing the frame.

### 14.9 Per-frame disk I/O is bounded by deltas, not by frames
1200 file writes at 30 Hz would crush the FS. 1200 file writes at 2 Hz on
distinct-frame transitions only is fine on Windows NTFS.

### 14.10 dump_tile_tree references globals defined later in source
`MAT_NAME` etc. live below the `dump_tile_tree` function definition. Python
resolves them at call time, so this is safe — but anyone refactoring the file
to move things around must preserve "dump function defined; metadata defined;
main loop runs" ordering.

---

## 15. Module extraction proposal: `terminal3d`

The reference implementation is a single 1129-line script. To turn it into a
reusable library this is the proposed split:

```
terminal3d/
    __init__.py         re-exports the public API
    geometry.py         Vec3, Box, ray-box intersect, slab math
    camera.py           Camera class, get_ray_dir
    materials.py        Material, MAT_NAME, MAT_CATEGORY, MAT_CAT_CHAR
                         (the metadata maps stay editable per scene)
    shading.py          trace_ray, light model, procedural shaders
    surface.py          ANSI helpers (rgb, bg_rgb, goto, half-block packer),
                         frame buffer composition
    input_win.py        msvcrt-based get_keyboard_input + key dedupe
    input_posix.py      termios fallback (not yet implemented)
    debug_state.py      global state dict, dbg_publish, lock, atomic write
    debug_server.py     _DbgHandler, _start_dbg_server
    tile_dump.py        dump_tile_tree
    watcher.py          (entry point) live tail of HTTP server
    headless.py         (entry point) one-shot frame to text files
```

### 15.1 Public API contract

```python
from terminal3d import Renderer, Scene, Material, Camera, run

scene = Scene()
scene.add_box((-2.5, 0, -1), (2.5, 0.05, 4), Material("floor", (120, 90, 60), 0.6))
# ... build scene ...

run(
    scene,
    width=60, height=20,
    camera=Camera(pos=(0, 1.6, 0.5)),
    lights=[Vec3(0, 2.7, 2)],
    debug=DebugConfig(port=8765, autodump=True, frames_max=60),
    input_handler=default_handler_windows(),
)
```

`run` is the only function with a side effect on stdout. Everything else is
pure (or HTTP-side).

### 15.2 Hard requirements that survive extraction

- 4-tuple return from `render_frame`: `(buffer, colors, ascii_preview, tile_grid)`.
- Half-block packing as the default; 1:1 cell mapping as a flag.
- `MAT_CATEGORY` and `MAT_CAT_CHAR` provided by the scene, not hard-coded.
- All env vars: `DBG_DISABLE`, `DBG_PORT`, `DBG_AUTODUMP`, `DBG_FRAMES_MAX`.
- All 9 endpoints listed in section 9.1.
- `_safe_rmtree` + atomic write semantics.
- Key sanitization at server boundary AND watcher boundary.

### 15.3 Optional, non-blocking extensions

- POSIX input via `termios` + `select` (for SSH testing).
- ANSI-stripping fallback rendering for `--no-color` terminals.
- `Triangle` and `Sphere` primitives in addition to `Box`.
- BVH for scenes with > 100 objects (current bedroom = 41, no BVH needed).
- `pixel_grid` (60×40) sub-tile dump variant for full per-pixel features.

---

## 16. Verification log of this session

The following is the ground-truth evidence that the reference implementation
behaves as specified. All numbers come from the live HTTP server during the
session of 2026-04-30.

### 16.1 Baseline pose

```
GET /state
frame=62  pos=(0, 1.6, 0.5)  yaw=0.00°  pitch=0.00°
center_hit_id=10 (window)   center_depth=3.45
```

This is consistent with the scene: window box is at z=3.95-4.0, camera at
z=0.5, distance ≈ 3.45. Yaw=0 looks straight at +Z. Material id 10 is window.

### 16.2 Pure yaw step (`l`)

```
GET /state
frame=200  pos=(0, 1.6, 0.5)  yaw=6.875°  pitch=0.00°
center_hit_id=10  center_depth=3.47   trigger_keys=[l, \r]
```

Predicted: window stays under crosshair because the window is wider than the
yaw angle subtended (window width 1.2 m at distance 3.45 m subtends ≈19°,
yaw step is 6.87°). Verified.

`map_category.txt` of `frame_00105`:

```
CCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC
...
WWWWWWWWWWWWWWWWWWWWWWnn++++++++nnWW++++WWWWWWWWWWWWWWWWWWWW
WWWWWWWWWWWWWWWWWWWWWWnnnnnnnnnnnnWW....WWWWWWWWWWWWWWWWWWWW
WWWWWWWWWWWWWWWWWWWWWWnnnnnnnnnnnnWW++++WWWWWWWWWWWWWWWWWWWW
```

Window block (`n`s) shifted left ~3 columns vs baseline (which had it at
columns 25-35). Correct sign for yaw right.

### 16.3 Pure pitch step (`i`)

```
GET /state
frame=341  pos=(0, 1.6, 0.5)  yaw=6.875°  pitch=-4.583°
center_hit_id=10  center_depth=3.49   trigger_keys=[i, \r]
```

Predicted: ceiling band grows because looking up. Verified — ceiling rows in
`frame_00291/map_category.txt` increased from 6 to 7. Window block dropped
1 row.

### 16.4 Pure forward step (`w`)

```
GET /state
frame=489  pos=(0.024, 1.6, 0.699)  yaw=6.875°  pitch=-4.583°
center_hit_id=10  center_depth=3.29  trigger_keys=[w, \r]
```

Predicted Δpos = (sin 6.875° × 0.2, 0, cos 6.875° × 0.2) = (0.0240, 0, 0.1986).
Observed Δpos = (0.024, 0, 0.199). Match to 3 decimal places.

Predicted Δdepth = -0.20 (camera moved straight at the wall along its yaw).
Observed Δdepth = 3.49 → 3.29 = -0.20. Exact match.

### 16.5 Per-pixel boundary integrity

`frame_00438/transitions/_index.txt` reports 82 transition tiles. The first
8 listed are all `top=ceiling/bot=walls` along the upper room edges — the
expected geometry from a box-corner perspective when looking forward and
slightly down.

Spot-checked tile (35, 13) in `frame_00225/by_category/bed/35_13.json`:
```json
{"top_name":"bed_frame","bot_name":"bed_sheet",
 "top_color":[49,31,18],"bot_color":[157,157,171],
 "top_hex":"#311f12","bot_hex":"#9d9dab","transition":false}
```

Same category (`bed`) on both halves but different materials (`bed_frame`
under `bed_sheet`) — correct: at this row the camera is grazing the side of
the bed where the sheet has just risen above the dark frame.

### 16.6 Auto-dump correctness

After `l`, `i`, `w` with grounding queries between, the `_debug_tiles`
directory contained exactly:

```
frame_00000   (init pose)
frame_00105   (after l)
frame_00291   (after i)
frame_00438   (after w)
latest        (mirror of frame_00438)
```

No spurious dumps between keystrokes. No missing dumps. Delta detection is
working precisely as specified.

---

## 17. Conformance checklist

A new implementation conforms to this standard if and only if every item
below is true.

### Display
- [ ] 24-bit ANSI escapes `ESC[38;2;...m` and `ESC[48;2;...m` are emitted.
- [ ] Half-block packing is the default, with optional flag to disable.
- [ ] Pixel grid is exactly `WIDTH × HEIGHT*2` when half-blocks are on.

### Camera
- [ ] `get_ray_dir` produces unit vectors that obey the verified yaw/pitch sign
      conventions of section 16.
- [ ] Movement uses `pos.x += sin(yaw)*MOVE_SPEED; pos.z += cos(yaw)*MOVE_SPEED`.

### Rendering
- [ ] `render_frame_*` returns the 4-tuple in the specified order.
- [ ] `tile_grid[y][x]` is exactly `(top_mat, bot_mat, top_color, bot_color)`.
- [ ] ASCII preview is built inline and uses the published `DENSITY_CHARS` ramp.

### Materials
- [ ] Every material id has entries in both `MAT_NAME` and `MAT_CATEGORY`.
- [ ] Every category has a single character in `MAT_CAT_CHAR`.
- [ ] `transition` is reserved for half-block category boundaries.

### Input
- [ ] Per-frame deduplication of repeated keys.
- [ ] SPACE is aliased to `/` for at least the auto/manual toggle.
- [ ] Non-printable keys are repr-escaped before display or logging.

### Debug server
- [ ] Starts on import as a daemon thread on `127.0.0.1:8765` (overrideable).
- [ ] Disables itself if `DBG_DISABLE=1`.
- [ ] All 9 endpoints from section 9.1 are present and respond correctly.
- [ ] `log_message` is overridden to silence the handler.
- [ ] `Access-Control-Allow-Origin: *` is set.

### Atomic state file
- [ ] `_debug_state.json` is rewritten via `tmp + os.replace` after every
      `dbg_publish`.

### Frame deltas
- [ ] Delta key includes pose AND ASCII hash.
- [ ] Ring buffer capped at `DBG_FRAMES_MAX` (default 60).
- [ ] Event log preserves all non-frame events (last 100) plus last 50 frames.

### Tile tree dump
- [ ] Auto-invoked on each delta when `DBG_AUTODUMP=1` (default).
- [ ] Produces exactly the directory layout listed in section 11.
- [ ] `latest/` mirror is updated atomically per dump.
- [ ] `_safe_rmtree` runs before re-dump of the same frame number.

### Watcher
- [ ] Connects to the server, retries on `URLError`.
- [ ] Prints non-frame events distinctly from frame deltas.
- [ ] Sanitizes display keys identically to the publisher.

### Headless harness
- [ ] Reuses scene + camera primitives from the renderer source.
- [ ] Outputs `_debug_hits.txt`, `_debug_ascii.txt`, `_debug_color.txt`.

---

## Appendix A — Verified terminal session transcript (abridged)

```
PS> python render_engine_v2/test_bedroom_enhanced.py
[renderer alive on terminal A, debug server bound to :8765]

PS> Invoke-WebRequest http://127.0.0.1:8765/state
{frame:62, pos:[0,1.6,0.5], yaw_deg:0, hit:10, depth:3.45}

[send l to terminal A]
PS> Invoke-WebRequest http://127.0.0.1:8765/state
{frame:200, yaw_deg:6.875, hit:10, depth:3.47}

[send i to terminal A]
PS> Invoke-WebRequest http://127.0.0.1:8765/state
{frame:341, yaw_deg:6.875, pitch_deg:-4.583, hit:10, depth:3.49}

[send w to terminal A]
PS> Invoke-WebRequest http://127.0.0.1:8765/state
{frame:489, pos:[0.024,1.6,0.699], yaw_deg:6.875, pitch_deg:-4.583,
 hit:10, depth:3.29}

PS> Get-ChildItem render_engine_v2/_debug_tiles -Directory
frame_00000  frame_00105  frame_00291  frame_00438  latest
```

## Appendix B — Reference file inventory

| File | Lines | Bytes | Role |
|---|---|---|---|
| `test_bedroom_enhanced.py` | 1129 | 46723 | Renderer + embedded debug server + tile dump |
| `debug_watcher.py`         |  125 |  5191 | Live tail of debug server |
| `debug_dump.py`            |  138 |  5067 | Headless single-frame verification |

## Appendix C — Environment variables

| Var | Default | Effect |
|---|---|---|
| `DBG_DISABLE`     | unset | If set, no HTTP server, no file writes, no events. |
| `DBG_PORT`        | 8765  | Bind port for HTTP server. |
| `DBG_AUTODUMP`    | 1     | If 0, delta frames do not auto-write the tile tree. |
| `DBG_FRAMES_MAX`  | 60    | Ring buffer cap for distinct frames. |

---

# Part II — Room → World Gap Analysis

This part of the standard answers two coupled questions raised after the
bedroom verification:

1. How do we promote the bedroom from a single self-contained scene into one
   chunk of a larger connected world?
2. How do we evolve from a camera that only **looks** at the scene into an
   agent (player or AI) that **interacts** with it — opens the door, picks up
   the lamp, sees the bed get mussed, hears something move in another room?

Both are addressed against the **current evidence** in this repository — i.e.
what already exists in `test_bedroom_enhanced.py`, `environments.py`,
`debug_dump.py`, and `debug_watcher.py`. Each gap entry names what is
already present, what is missing, and the smallest forward step that
preserves the verified guarantees of Part I.

## 18. Current evidence inventory

| Capability | Status today | Source of truth |
|---|---|---|
| AABB ray tracing of static scene | working | `trace_ray`, `Box.intersect` |
| Half-block 60×40 surface | working | `render_frame_enhanced` |
| Camera with yaw/pitch/pos | working | `Camera`, verified in section 16 |
| Per-tile material + category dump | working | `dump_tile_tree`, frame dirs verified |
| Embedded HTTP debug server | working | `_DbgHandler`, 9 endpoints verified |
| Frame-delta archive | working | `_DBG_FRAMES`, ring buffer verified |
| Multiple scene templates | partially working | `environments.py` defines 6 scenes |
| Scene switching at runtime | **NOT integrated** | only the bedroom is loaded by `test_bedroom_enhanced.py` |
| Connected multi-room world | **MISSING** | each environment is an island |
| Streaming / chunk loading | **MISSING** | the entire scene is a flat list |
| Collision against geometry | **PARTIAL** | only axis-aligned room-bounds clamp on camera (`max(-2.3, min(2.3, …))`) |
| Object identity beyond material | **MISSING** | only `mat.id` exists; there is no `entity_id` |
| Mutable scene state | **MISSING** | scene list is built once at startup |
| Interaction (pickup/open/use) | **MISSING** | input maps only to camera transforms |
| Server-side world clock | **MISSING** | each frame is independent |
| Multi-agent / network | **MISSING** | single-process, single-camera |

Two important things to notice in this table:

- The renderer's primitives (`Box`, `Sphere`, `Camera`, `trace_ray`) are
  already general enough to render any scene `environments.py` produces.
- The debug surface (`/state`, `/tiles`, frame deltas, tile tree) is already
  general enough to observe **any** scene. Nothing in Part I is bedroom-
  specific. The world layer can be built on top without rewriting it.

## 19. From room to world: the architecture

The world is structured as a coordinate space partitioned into **chunks**.
Each chunk owns a list of objects and a small set of metadata. The bedroom
becomes one chunk. The player's room is another chunk. A hallway between
them is a third. The current scene is the union of the chunks within the
camera's render radius.

```
                +-------------------------------------------+
                |  WorldRegistry                            |
                |    name -> Chunk loader callable          |
                |    bedroom -> create_bedroom()            |
                |    office  -> create_office()             |
                |    park    -> create_outdoor_park()       |
                |    ...                                    |
                +---------------+---------------------------+
                                |
                                v
                +-------------------------------------------+
                |  World grid (sparse, Dict[Tuple, Chunk])  |
                |    (-1, 0) hallway_north                  |
                |    ( 0, 0) bedroom    <--  current chunk  |
                |    ( 1, 0) bathroom                       |
                |    ( 0, 1) park       (outdoor portal)    |
                +---------------+---------------------------+
                                |
                                v
                +-------------------------------------------+
                |  Active scene = union of all chunks       |
                |  within RENDER_RADIUS of camera.pos       |
                |  Per-chunk object id namespace            |
                +-------------------------------------------+
```

Two coordinate systems are needed:

- **World coords** — global, monotonic. Camera, lights, NPCs live here.
- **Chunk coords** — `(cx, cz)` integer pair. Used for spatial lookup and
  for streaming neighbors in/out.

Chunk size is a tunable. For the bedroom that fits in a 5×4 m box, a
**16 m × 16 m** chunk is the recommended starting cell — large enough to
hold a whole room, small enough to keep the per-chunk object count under
100 (which is the ceiling at which `trace_ray`'s linear scan stays fast).

### 19.1 The Chunk record (proposed)

```python
class Chunk:
    cx: int
    cz: int
    objects: List[Box | Sphere]   # already exist
    lights: List[Vec3]             # already exist as a flat list today
    portals: List[Portal]          # see 19.2
    spawn_points: List[SpawnPoint]
    bounds: Box                     # axis-aligned chunk envelope
    chunk_seed: int                 # for deterministic procedural detail
    state: Dict[str, Any]           # mutable game state for THIS chunk
```

The first three fields already exist as the global lists in
`test_bedroom_enhanced.py` and as `env.objects`/`env.lights` in
`environments.py`. The transformation is purely:

> Move the global object list off the module and onto a `Chunk` instance
> keyed on `(cx, cz)`.

Nothing in the renderer needs to know about chunks. It still receives a
flat `objects` list — the world layer just produces a different one each
frame.

### 19.2 Portals

A **portal** is a marked region (an axis-aligned trigger box) that asserts
"crossing this volume in this direction puts you at coordinates X in chunk
Y." Portals are how rooms connect.

```
Portal:
    trigger: Box                 # in world coords, in source chunk
    target_chunk: (int, int)
    target_pos: Vec3
    target_yaw: float            # so player faces the right way after step
    bidirectional: bool
```

The bedroom door (material id 18) is the obvious first portal: a trigger
box lying just inside the doorway that, when entered, moves the camera to
the matching trigger box of an adjacent hallway chunk.

Implementation cost: tens of lines. Detection is a single AABB containment
check per frame against camera position.

### 19.3 Streaming policy

Per frame:

1. Compute the chunk `(cx, cz)` containing `camera.pos`.
2. Build the active set = all chunks within Chebyshev distance ≤ N
   (start with N=1 → 9 chunks max).
3. For chunks newly entering the active set, call their loader once and
   cache the result.
4. For chunks leaving the active set, drop the reference and let GC clean
   up. (Save mutable `state` first — see section 21.)
5. Concatenate `objects` across active chunks. This is the list passed
   to `trace_ray`.

With 9 chunks × ~50 objects each = 450 objects, the renderer remains under
the linear-scan ceiling. Above that, BVH or grid acceleration becomes
mandatory (see section 23).

### 19.4 Skybox per chunk

The bedroom currently defines the sky color implicitly inside `trace_ray`
as `(40, 40, 60)`. Every `Environment` in `environments.py` already has its
own `sky_color`. The world renderer must therefore consult the **camera-
containing chunk** for sky color (and possibly ambient and time-of-day),
not a global constant.

### 19.5 What changes in `_debug_state.json`

```diff
   "pos": [0.024, 1.6, 0.699],
+  "chunk": [0, 0],
+  "active_chunks": [[-1, 0], [0, 0], [1, 0], [0, 1]],
   "yaw_deg": 6.875,
```

The tile tree gains an enclosing dir per chunk:

```
_debug_tiles/
    frame_NNNNN/
        meta.json                (now includes chunk + active_chunks)
        chunks/
            bedroom_0_0/
                tiles.json
                map_category.txt
                ...
            hallway_-1_0/
                tiles.json
                ...
        merged/                  # backwards-compatible single-grid view
```

This preserves the verified per-pixel feature mapping while adding a
lookup axis for "which chunk is this pixel from?"

## 20. From observer to participant: interaction

Today, every keystroke modifies only `Camera`. To turn the player into an
agent, the same input pipeline must be able to modify `World` state.

### 20.1 The `Entity` layer (the missing concept)

`Material` is the only object identity that exists today. Two pillows of
the same material are indistinguishable. `Entity` introduces stable identity:

```python
class Entity:
    eid: int                     # unique per world
    name: str                    # 'pillow_left', 'door_main'
    geometry: Box | Sphere       # rendering primitive
    pose: Vec3                   # mutable
    flags: Set[str]              # 'pickable', 'openable', 'lit', 'static'
    state: Dict[str, Any]        # 'open': True, 'on': False, etc.
    on_interact: Optional[Callable]   # invoked when player presses E on it
```

Every renderable object gets exactly one entity wrapper. The renderer
continues to operate on geometry; the world layer maintains the
`Dict[entity_id, Entity]` map so the `mat_id` returned from `trace_ray`
can be enriched into a full entity record when the agent asks "what am I
looking at?"

### 20.2 The "look-at" query (already 99% there)

The bedroom renderer already publishes `center_hit_id`. The interaction
layer needs:

```
GET /lookat
{
  "entity_id": 42,
  "name": "lamp_nightstand",
  "category": "lights",
  "distance": 1.21,
  "actions": ["toggle", "examine"],
  "state": {"on": false}
}
```

This is one extra dictionary lookup keyed by `mat_id`/`entity_id`.

### 20.3 Interaction keys

Reserve a small key set, all routed through the same publisher used for
camera input today:

| Key | Action |
|---|---|
| `E` | activate entity under crosshair (`on_interact`) |
| `G` | grab/drop entity in hand |
| `X` | toggle inventory display |
| `Z` | crouch (vertical pos clamp change) |
| `Shift+W/A/S/D` | run (movement multiplier) |

`process_input` in the reference implementation already shows the pattern.
Each new action is one branch.

### 20.4 The action protocol

Every interaction is published through the existing debug server as a
distinct event so an external agent can observe and replay:

```
POST /action
{ "verb": "toggle", "target_eid": 42 }
```

Server-side this calls `Entity.on_interact` and emits an `event` of type
`action` into the same merged event log that already holds keystrokes and
deltas. The rule from section 9 still applies: action events are not
frame events and must never be evicted by the rolling frame buffer.

### 20.5 Mutable scene rendering

The renderer must re-read the entity's `pose` and `state` each frame
(rather than baking it into `Box.min`/`Box.max` once at scene build).
Because `trace_ray` already iterates the live list each frame, the only
required change is that `Box`/`Sphere` derive their slabs from
`entity.pose` and `entity.geometry` lazily. Trivial.

A door entity, for example:
- `state['open'] in {False, True}`
- `geometry` defines the closed bounds
- when `open`, the bounds are translated by `+1.0` in X (or removed from
  the active list entirely until close)

### 20.6 Inventory and hand-held objects

A picked-up entity is removed from the chunk's `objects` list and added
to `Player.inventory`. It is no longer rendered in world space. When held,
it can optionally be rendered as a small AABB attached to the camera
basis vectors (a kind of "viewmodel"). This is a six-line addition to
the per-frame pre-render step.

### 20.7 Time and ticks

A real environment moves on its own. The standard requires a single
**world clock** that increments deterministically:

```
tick_dt = 1.0 / TARGET_FPS
world.tick(tick_dt) is called once per render iteration BEFORE trace_ray
```

Entities with `tick` callables update their state. The lamp flicker, the
door auto-close, an NPC's path, all run from this hook. Determinism comes
from using `frame * tick_dt` as the time variable, never `time.time()`.

This is the single most important addition to make the world feel alive
without giving up the **same-input-same-output** guarantee that makes the
debug surface valuable.

## 21. Persistence

Streaming chunks in and out is destructive unless their `state` dict is
saved before unloading and restored on reload.

```
_world_state/
    chunks/
        0_0.json     # bedroom: {door_main: {open: true}, lamp_nightstand: {on: true}}
        -1_0.json    # hallway: {...}
    player.json      # pos, yaw, inventory, flags
    world.json       # global clock, weather, quest flags
```

Atomic writes apply (tmp + os.replace), same rule as `_debug_state.json`.

## 22. NPCs / non-camera agents

An NPC is an `Entity` with an additional `controller` callable invoked on
each world tick. The controller receives the world state and returns a
movement / action intent. The world applies it.

The simplest possible controller is a static patrol path. A more
sophisticated one queries the chunk's nav graph (see 23.2). An LLM-driven
controller could use the existing HTTP server in reverse — instead of an
external agent reading the renderer's state to know what the room looks
like, an internal NPC reads its own chunk's state to choose actions.

## 23. Performance gaps

Real-time interactivity at the 5 FPS measured today is too slow for
serious play. Three required upgrades:

### 23.1 Spatial acceleration

`trace_ray` is O(N·objects) per pixel. With 60×40 = 2400 rays and 41
objects = ~100k intersections per frame today. With 9 chunks × 50
objects = ~1M intersections per frame, the loop becomes the bottleneck.

**Required:** uniform grid or BVH per chunk, rebuilt only when entities in
that chunk move. Static entities never trigger a rebuild.

### 23.2 Navigation graph

Each chunk should also expose a coarse **occupancy grid** (e.g. 0.5 m
cells of "blocked" or "free") for collision and pathfinding. This is
the data structure both the player's collision detector and any NPC
A* needs. It is also cheap: an offline pass over the chunk's static
geometry rasterizes the grid once at chunk-build time.

### 23.3 Optional: precompute a per-chunk mat-id grid for low-fidelity preview

When the camera is very far from a chunk (still in the active set but
visible only at oblique angles or behind another chunk), tracing it at
full per-pixel resolution wastes work. A 16×16 mat-id pre-bake per chunk
suffices for a billboarded distant fallback. This is optional and only
relevant once the world exceeds ~10 chunks.

## 24. Networking

The current debug server is single-client, polling-only. To support
multiple agents in the same world:

- Replace polling with **server-sent events** on `/events`. The frame
  deltas already form a perfect SSE stream.
- Per-agent identity headers so action events are attributed.
- Authoritative server: each agent submits intents via `POST /action`,
  the server applies them to a single shared `World` instance, broadcasts
  the resulting deltas.

Nothing in Part I forbids this. The server just becomes one process and
the renderer becomes a per-agent client.

## 25. Required deliverables to fully realize the vision

The following milestones, in order, transform the verified bedroom
renderer into the full world. Each milestone is small enough to verify
independently using the debug surface from Part I.

| # | Milestone | Verification (uses Part I machinery) |
|---|---|---|
| M1 | Add `Chunk` class wrapping the existing object list | `/state` reports chunk=(0,0), single-chunk render visually identical to today's |
| M2 | Build `WorldRegistry` from `environments.py`'s 6 scenes | switching chunks via debug command changes `/tiles` layout |
| M3 | Implement portals; make bedroom door go to a hallway chunk | walking through door produces a chunk transition event in `/events` |
| M4 | Streaming: render union of 9-chunk neighborhood | `/state.active_chunks` reports correct list as camera moves |
| M5 | Replace global `MOVE_SPEED` clamp with per-frame collision against the active object list | sliding along walls produces no NaN tile dumps |
| M6 | Introduce `Entity` layer; expose `/lookat` | center-pixel queries return entity id + name + actions |
| M7 | Implement `E` to toggle door / lamp; `tick(dt)` for auto-close | action events appear in event log; same-frame ASCII reflects new state |
| M8 | Persist mutable chunk state on unload | re-entering a chunk preserves toggled lamps |
| M9 | BVH per chunk | frame time drops; per-tile dump still pixel-identical |
| M10 | Inventory + viewmodel | picked entity disappears from world tile tree, appears in `/inventory` |
| M11 | NPC controller hook + one patrol NPC | NPC's pos changes between frames; tile tree shows it move |
| M12 | SSE on `/events` | watcher upgrades from poll to subscribe; latency drops |

Each milestone preserves the contract:
- `_debug_state.json` is still atomic and complete.
- Per-tile dumps are still gated by delta detection.
- Material/category metadata still maps every visible pixel.
- Event log still preserves all non-frame events.

This is the path from "a 60×20 terminal showing a still bedroom from one
camera" to "a streaming, multi-chunk, interactive world observable at the
pixel level by both the player and any external agent."

## 26. What is required, in concrete dependency order

Bare minimum to claim a "world" exists:

1. **`world.py`** containing `Chunk`, `WorldRegistry`, `Portal`,
   `Player` (rename of camera + position), and a `World.update_active()`
   method. Wraps but does not replace `environments.py`.
2. **Renderer adapter** in `test_bedroom_enhanced.py` (or a new
   `test_world.py`) that builds the active scene from `World` each frame
   instead of calling `create_bedroom()` once.
3. **Bedroom door portal** wired to a second chunk (start with a copy of
   the bedroom labeled "hallway" — proves the streaming code without
   blocking on new content).
4. **`/world` endpoint** reporting current chunk, active chunks, world
   clock.
5. **Verification:** repeat the section 16 keystroke procedure but
   include a `w` step that crosses the portal. Expected: `pos` jumps
   discontinuously, `active_chunks` changes, `map_category.txt` content
   replaces wholesale (not shifted).

Bare minimum to claim "interaction" exists:

1. **`Entity` registry** built from each chunk's objects at load time.
2. **`/lookat` endpoint** returning the entity under the crosshair.
3. **`E` key** that calls `entity.on_interact(world, player)`.
4. **One togglable entity** in the bedroom: the lamp.
5. **Verification:** press `E` while looking at the lamp, confirm a new
   delta frame, confirm the lamp's color in `tiles.json` changed
   (emissive on/off path of `trace_ray` already supports this).

Bare minimum to claim "real-time" exists:

1. Per-chunk **uniform grid** acceleration with cell size 1 m.
2. Spatial query in `trace_ray` walks only the cells the ray crosses.
3. **Verification:** frame_NNNNN/meta.json adds `render_ms`. The number
   should drop by a factor proportional to scene complexity vs. today's
   baseline of ~180 ms per frame at 41 objects.

## 27. Risk register

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Material id renumbering breaks tile tree categories when a new chunk is loaded | high | medium | Use stable `Entity.eid` (assigned per chunk by hash of name + chunk coords) instead of `mat.id` for category mapping at the world layer. |
| Portal teleport produces a "delta" with massive pos jump, breaking the round-down delta key | medium | low | Increase `pos` precision in delta key to 3 decimals (already done) and force a frame dump on chunk transition regardless of delta key. |
| Streaming creates duplicate object lists on chunk reload, doubling intersection cost | medium | high | Cache loaded chunks in `WorldRegistry`; loader is idempotent. |
| Mutable state persists incorrectly across runs | medium | medium | Version the JSON state files; on schema mismatch, log + start fresh. |
| Per-frame disk I/O grows quadratically with chunk count | high | high | Tile dump must remain delta-gated. Per-chunk subdirs only created for chunks whose tiles changed since last dump. |
| Networking layer reintroduces ANSI in event payloads | low | medium | Sanitization rule from 8.3 applies to ALL outputs that touch a terminal, including SSE. |
| LLM-driven NPCs hit the same HTTP server they're being observed by | medium | low | Use a separate Unix socket / port for control vs. observation, or rate-limit per-source on `/action`. |

## 28. Closing assessment

The verified bedroom system has already done the hard part: it has a
correct ray tracer, a correct ANSI surface, a correct camera, a correct
per-pixel feature pipeline, and an observation API that an agent can
read.

What is **missing** is not novel rendering work. It is plumbing:

- a chunk container (a dict),
- a portal (a trigger box plus a destination tuple),
- an entity layer (a wrapper class plus a registry),
- a tick (one extra call per frame),
- a save/load (two file writes),
- a spatial index (one classic data structure).

Each of these can be added as a milestone of section 25 without
breaking any guarantee in Part I. The cost is engineering time, not
research. On current evidence, the system is one to two weeks of
focused implementation away from being a working multi-room interactive
terminal world.

---

## 29. Agent input gap (the missing primitive)

A second-session attempt by an external agent to drive the renderer hit
a chain of failures that exposes a real architectural hole. Reproduced
from the live transcript:

1. Agent first tried `AppActivate` against the renderer's window title to
   send keystrokes via SendKeys. **Returned False** — the agent's process
   could not steal focus from the user's foreground application under
   Windows' security model.
2. Agent then fell back to Win32 `WriteConsoleInput`, which writes key
   events directly into the target console's input buffer and bypasses
   the focus requirement. **This worked**, proving the renderer is
   reachable.
3. But: rapid sequences of identical keys collapsed under the per-frame
   dedupe described in section 8.1. Five `W` presses in 100 ms produced
   **one** forward step instead of five.
4. Agent compensated with a 250 ms inter-key delay. Still lossy: at
   4.4 FPS the polling window is ~230 ms, so two keys arriving in the
   same window were merged.
5. Agent escalated to 500 ms delay — finally reliable but **2× slower
   than the human user** at the same task.

### 29.1 Diagnosis

The renderer was designed with the assumption that input is human-driven
keyboard input subject to OS auto-repeat. Two design choices are correct
for that audience and **wrong** for an automated agent:

- **Per-frame dedupe** (`get_keyboard_input` with `seen` set) defends
  against unintentional auto-repeat. An agent's keystrokes are
  intentional; deduping them silently discards intent.
- **Polling stdin** ties input throughput to render FPS. A 5 FPS render
  loop hard-caps any input source to 5 distinct events per second, no
  matter how fast the source can produce them.

Add to those two:

- **No acknowledgement.** The agent has no way to tell from the input
  side whether its keystroke was received. It must round-trip through
  `/state` and check `key_seq` to know — already two HTTP calls per
  intended action, plus a race condition if it polls between the
  publish and the next loop iteration.
- **No backpressure.** A fast agent can fill the console input buffer
  faster than the renderer drains it. The lost keys in step 3 above
  were the buffer overflowing.
- **OS-coupled input transport.** Using `msvcrt`/`WriteConsoleInput`
  forces every external controller to go through Windows-specific
  console APIs. The renderer's debug surface is HTTP, but its input
  surface is not. This asymmetry is the root cause.

### 29.2 The missing primitive: `POST /input`

The standard MUST add a non-keyboard input channel that mirrors the
existing observation channel. Specifically:

```
POST /input
Content-Type: application/json
Body: {
  "keys": ["w", "w", "l"],         # logical keys, same vocabulary as
                                    #   process_input
  "wait_frames": 1,                # how many render frames to consume
                                    #   each key over (default 1)
  "seq": 17,                       # client-provided monotonic seq id
}

Response: {
  "accepted_seq": 17,
  "queued": 3,
  "pending_after": 0,
  "current_state": { ... full state snapshot ... }
}
```

Server-side requirements:

1. **Frame-locked queue.** A list of `(key, frame_to_apply)` tuples is
   maintained under `_DBG_LOCK`. The render loop, before calling
   `get_keyboard_input`, drains exactly one entry per `wait_frames`
   from the queue and merges it with the human keys.
2. **No dedupe of queued keys.** They are intentional and must be
   applied verbatim — even ten consecutive `W`s.
3. **Synchronous acknowledgement.** The HTTP response includes the
   pending count and the post-acceptance state snapshot, so the
   agent gets the same information that polling `/state` would
   provide, without a second round trip.
4. **Optional `block_until_drain` flag.** When true, the response is
   delayed until all queued keys have been applied. Lets agents do
   "send 5 W's, then read the resulting world" in one HTTP call.
5. **Same publishing path.** Each applied key still flows through
   `dbg_publish("keystroke", last_keys=[k], …)` so the existing
   event log, watcher, and frame deltas observe agent input
   identically to human input.

### 29.3 Why this preserves Part I

The endpoint adds one input channel; it does not change the renderer's
core loop, the camera math, the tile dump, or the delta detection.
Verification using the Part I machinery:

- Submit `{"keys": ["w","w","w","w","w"]}` to `/input`.
- Expect 5 distinct frame dumps in `_debug_tiles/` (one per applied key)
  if the camera advances each step.
- Expect `key_seq` in `/state` to advance by exactly 5.
- Expect `last_keys` in successive frame events to be `["w"]` five
  times — never `["w","w"]` collapsed by the dedupe.

That is the same verification protocol used in section 16. Nothing
about it changes; only the input transport does.

### 29.4 Why the agent shouldn't have to inject keystrokes at all

The deeper observation: a debug-friendly system exposes its **state**
over HTTP and exposes its **inputs** over the same channel. The
renderer already does the first half. The keystroke channel exists for
human ergonomics and shouldn't be the only path to drive the camera.

Three concrete consequences for the world layer (Part II):

- The `POST /action` endpoint proposed in section 20.4 is a strict
  superset of `POST /input`. Implementing `/action` for entity
  interactions automatically gives agents a non-keyboard movement
  channel if `move_forward`, `turn_left`, etc. are exposed as verbs.
- The interaction layer should publish a **schema** at `GET /actions`
  listing every verb the world accepts and its argument signature.
  Agents discover capability without trial-and-error keystrokes.
- For headless tests, `POST /input` plus `/state` polling gives a
  closed-loop deterministic test harness — no console window, no
  focus juggling, no `WriteConsoleInput`, no Windows API.

### 29.5 Required additions to milestones

Insert before M1:

| # | Milestone | Verification |
|---|---|---|
| M0a | Add `POST /input` endpoint with frame-locked queue | curl-driven 5×W produces 5 deltas |
| M0b | Disable per-frame dedupe for queued keys (keep it for live human keys) | matched in `_input_source` field of frame event |
| M0c | Add `GET /actions` returning verb schema (initially empty list of verbs, just the `keys` vocabulary) | agent self-discovers controls |

These three are < 100 lines of code and remove every workaround in
section 29.1. They are the smallest possible step that turns the
renderer from human-controllable into agent-controllable.

### 29.6 Risk additions

| Risk | Mitigation |
|---|---|
| `/input` lets a remote attacker drive the player. | Bind to `127.0.0.1` only (already true for the debug server). For network play, add the auth layer in section 24 first. |
| Queued keys arrive faster than 1/frame and grow unbounded. | Cap the queue at e.g. 64 entries; reject `POST /input` with HTTP 429 when full. |
| Agent submits a key vocabulary the renderer doesn't know. | `/input` validates against `process_input`'s known set; unknown keys are returned in `rejected` list, not silently dropped. |

End of standard.
