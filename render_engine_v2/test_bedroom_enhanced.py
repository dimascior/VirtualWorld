import time, sys, math, random
import os, json, threading, http.server, socketserver

# Try to enable ANSI/UTF-8 in Windows console; skip on headless runners
try:
    import msvcrt  # Windows keyboard input
    import ctypes as _ctypes
    _k32 = _ctypes.windll.kernel32
    _k32.SetConsoleOutputCP(65001)  # UTF-8
    _k32.SetConsoleMode(_k32.GetStdHandle(-11),
                        0x0001 | 0x0002 | 0x0004)  # PROCESSED | WRAP | VIRTUAL_TERMINAL
except Exception:
    # Headless runner or non-Windows; msvcrt/ctypes may not be available
    # HTTP server will still start and serve debug data
    msvcrt = None

# === LIVE DEBUG SERVER ===
# Reports renderer state after every keystroke and every frame.
# Two channels:
#   - File: <script_dir>/_debug_state.json (atomic, rewritten per update)
#   - HTTP: http://127.0.0.1:8765/state  /ascii  /events
# Disable by setting env DBG_DISABLE=1
_DBG_DISABLED = os.environ.get("DBG_DISABLE") == "1"
_DBG_PORT = int(os.environ.get("DBG_PORT", "8765"))
_DBG_DIR = os.path.dirname(os.path.abspath(__file__))
_DBG_STATE_FILE = os.path.join(_DBG_DIR, "_debug_state.json")
_DBG_LOCK = threading.Lock()
_DBG_STATE = {
    "frame": 0,
    "phase": "init",          # "init" | "keystroke" | "frame"
    "fps": 0.0,
    "mode": "manual",
    "pos": [0.0, 1.6, 0.5],
    "yaw_deg": 0.0,
    "pitch_deg": 0.0,
    "use_msaa": False,
    "use_edge_detection": False,
    "skip_shadows": False,
    "show_debug": False,
    "last_keys": [],
    "key_seq": 0,             # increments on every keystroke batch
    "center_ray": [0.0, 0.0, 1.0],
    "center_hit_id": -1,
    "center_depth": -1.0,
    "ascii_preview": [],
    "timestamp": 0.0,
}
_DBG_EVENTS = []  # ring buffer of recent (ts, phase, summary)
_DBG_FRAMES = []  # ring buffer of distinct frames (delta-only)
_DBG_FRAMES_MAX = int(os.environ.get("DBG_FRAMES_MAX", "60"))
_DBG_LAST_PREVIEW_KEY = None  # (yaw, pitch, posx, posz, hash(ascii))
_DBG_LATEST_TILES = None      # last rendered tile grid (HEIGHT x WIDTH of tuples)
_DBG_TILES_DIR = os.path.join(_DBG_DIR, "_debug_tiles")
_DBG_AUTODUMP = bool(int(os.environ.get("DBG_AUTODUMP", "1")))
_DBG_INPUT_QUEUE = []   # [(key_str, wait_frames), ...] — from POST /input, no dedupe
_DBG_INPUT_SEQ = 0      # monotonic counter for accepted POST /input requests
_DBG_INPUT_WAIT = 0     # frames remaining before draining the next queued key


def _safe_rmtree(path):
    try:
        for root, dirs, files in os.walk(path, topdown=False):
            for f in files:
                try:
                    os.remove(os.path.join(root, f))
                except OSError:
                    pass
            for d in dirs:
                try:
                    os.rmdir(os.path.join(root, d))
                except OSError:
                    pass
        try:
            os.rmdir(path)
        except OSError:
            pass
    except Exception:
        pass


def dump_tile_tree(frame_num, tile_grid, ascii_preview, meta):
    """Write a directory tree describing every tile of the current frame.

    Layout:
        _debug_tiles/frame_NNNN/
            meta.json                  - camera + frame info
            tiles.json                 - full HEIGHT x WIDTH grid
            map_category.txt           - 60x20 single-char category map
            map_top_mat.txt            - 60x20 hex mat-id map (top half)
            ascii_preview.txt
            colors.csv                 - x,y,top_r,top_g,top_b,bot_r,bot_g,bot_b
            by_category/<category>/<x>_<y>.json   - per-tile records
            transitions/<x>_<y>.json   - tiles whose top != bot category
        _debug_tiles/latest/           - mirror of the most recent frame

    Returns the per-frame directory path.
    """
    if not tile_grid:
        return None
    h = len(tile_grid)
    w = len(tile_grid[0]) if h else 0

    base_dir = os.path.join(_DBG_TILES_DIR, f"frame_{frame_num:05d}")
    latest_dir = os.path.join(_DBG_TILES_DIR, "latest")
    _safe_rmtree(base_dir)
    _safe_rmtree(latest_dir)
    os.makedirs(base_dir, exist_ok=True)
    os.makedirs(latest_dir, exist_ok=True)

    # Build flat tile list + grouped categories
    tiles_flat = []
    cat_groups = {}
    transitions = []

    cat_map_lines = []
    mat_map_lines = []

    for y in range(h):
        cat_row = []
        mat_row = []
        for x in range(w):
            top_mat, bot_mat, top_color, bot_color = tile_grid[y][x]
            top_cat = MAT_CATEGORY.get(top_mat, "unknown")
            bot_cat = MAT_CATEGORY.get(bot_mat, "unknown")
            top_name = MAT_NAME.get(top_mat, f"id{top_mat}")
            bot_name = MAT_NAME.get(bot_mat, f"id{bot_mat}")
            is_transition = (top_cat != bot_cat)

            tile_rec = {
                "x": x, "y": y,
                "top_mat_id": top_mat, "top_name": top_name, "top_category": top_cat,
                "bot_mat_id": bot_mat, "bot_name": bot_name, "bot_category": bot_cat,
                "top_color": list(top_color), "bot_color": list(bot_color),
                "top_hex": "#{:02x}{:02x}{:02x}".format(*top_color),
                "bot_hex": "#{:02x}{:02x}{:02x}".format(*bot_color),
                "transition": is_transition,
                "ascii": ascii_preview[y][x] if y < len(ascii_preview) and x < len(ascii_preview[y]) else " ",
            }
            tiles_flat.append(tile_rec)

            # Category map char (mark transition tiles distinctly)
            if is_transition:
                cat_row.append(MAT_CAT_CHAR.get("transition", "+"))
            else:
                cat_row.append(MAT_CAT_CHAR.get(top_cat, "?"))
            mat_row.append(format(max(0, top_mat) & 0xff, "x"))

            # Group by top category
            cat_groups.setdefault(top_cat, []).append(tile_rec)
            if is_transition:
                transitions.append(tile_rec)
        cat_map_lines.append("".join(cat_row))
        mat_map_lines.append("".join(mat_row))

    # Write meta + summaries
    with open(os.path.join(base_dir, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)
    with open(os.path.join(base_dir, "tiles.json"), "w", encoding="utf-8") as f:
        json.dump({"width": w, "height": h, "tiles": tiles_flat}, f)
    with open(os.path.join(base_dir, "map_category.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(cat_map_lines) + "\n")
    with open(os.path.join(base_dir, "map_top_mat.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(mat_map_lines) + "\n")
    with open(os.path.join(base_dir, "ascii_preview.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join("".join(row) for row in ascii_preview) + "\n")
    with open(os.path.join(base_dir, "colors.csv"), "w", encoding="utf-8") as f:
        f.write("x,y,top_r,top_g,top_b,bot_r,bot_g,bot_b\n")
        for t in tiles_flat:
            tc, bc = t["top_color"], t["bot_color"]
            f.write(f"{t['x']},{t['y']},{tc[0]},{tc[1]},{tc[2]},{bc[0]},{bc[1]},{bc[2]}\n")

    # Per-category folders + per-tile files
    by_cat_root = os.path.join(base_dir, "by_category")
    os.makedirs(by_cat_root, exist_ok=True)
    for cat, tiles in cat_groups.items():
        cat_dir = os.path.join(by_cat_root, cat)
        os.makedirs(cat_dir, exist_ok=True)
        # Index file
        with open(os.path.join(cat_dir, "_index.txt"), "w", encoding="utf-8") as f:
            f.write(f"category={cat} count={len(tiles)}\n")
            for t in tiles:
                f.write(f"{t['x']:>3},{t['y']:>3} top={t['top_name']:<14} "
                        f"bot={t['bot_name']:<14} {t['top_hex']}/{t['bot_hex']}\n")
        # Per-tile JSON
        for t in tiles:
            fn = os.path.join(cat_dir, f"{t['x']:02d}_{t['y']:02d}.json")
            with open(fn, "w", encoding="utf-8") as f:
                json.dump(t, f)

    # Transition tiles directory
    trans_dir = os.path.join(base_dir, "transitions")
    os.makedirs(trans_dir, exist_ok=True)
    with open(os.path.join(trans_dir, "_index.txt"), "w", encoding="utf-8") as f:
        f.write(f"transition_count={len(transitions)}\n")
        for t in transitions:
            f.write(f"{t['x']:>3},{t['y']:>3} {t['top_category']}->{t['bot_category']} "
                    f"{t['top_name']}->{t['bot_name']}\n")
    for t in transitions:
        fn = os.path.join(trans_dir, f"{t['x']:02d}_{t['y']:02d}.json")
        with open(fn, "w", encoding="utf-8") as f:
            json.dump(t, f)

    # Mirror the top-level summaries into latest/ (skip per-tile files for speed)
    try:
        for fname in ("meta.json", "tiles.json", "map_category.txt",
                      "map_top_mat.txt", "ascii_preview.txt", "colors.csv"):
            src = os.path.join(base_dir, fname)
            dst = os.path.join(latest_dir, fname)
            with open(src, "rb") as r, open(dst, "wb") as wdst:
                wdst.write(r.read())
    except Exception:
        pass

    # Summary stats
    counts = {cat: len(tiles) for cat, tiles in cat_groups.items()}
    counts["__transitions"] = len(transitions)
    return {"path": base_dir, "counts": counts, "total_tiles": w * h}

def _dbg_atomic_write():
    try:
        tmp = _DBG_STATE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(_DBG_STATE, f, indent=2)
        os.replace(tmp, _DBG_STATE_FILE)
    except Exception:
        pass

def dbg_publish(phase, **fields):
    """Push state update + write JSON file. Called after keystrokes and frames."""
    if _DBG_DISABLED:
        return
    global _DBG_LAST_PREVIEW_KEY
    # Pull out non-state side-channel args (don't put in JSON state)
    _tile_grid = fields.pop("_tile_grid", None)
    _ascii_grid = fields.pop("_ascii_preview_grid", None)
    with _DBG_LOCK:
        _DBG_STATE["phase"] = phase
        _DBG_STATE["timestamp"] = time.time()
        _DBG_STATE.update(fields)
        # event log: keep all keystroke events (last 100), and last 50 frame events
        # sanitize keys for display (strip \r \n etc that wreck terminal output)
        _raw_keys = _DBG_STATE.get('last_keys') or []
        _disp_keys = [repr(k)[1:-1] if (len(k) != 1 or not k.isprintable()) else k
                      for k in _raw_keys]
        summary = (f"{phase} f={_DBG_STATE['frame']} keys={_disp_keys} "
                   f"Y{_DBG_STATE['yaw_deg']:+.1f} P{_DBG_STATE['pitch_deg']:+.1f} "
                   f"fps={_DBG_STATE['fps']:.1f}")
        _DBG_EVENTS.append({"ts": _DBG_STATE["timestamp"], "phase": phase, "summary": summary})
        non_frame = [e for e in _DBG_EVENTS if e["phase"] != "frame"]
        frame_ev = [e for e in _DBG_EVENTS if e["phase"] == "frame"][-50:]
        non_frame = non_frame[-100:]
        merged = sorted(non_frame + frame_ev, key=lambda e: e["ts"])
        _DBG_EVENTS[:] = merged

        # Frame delta archive: only when phase=='frame' AND preview/pose differs
        if phase == "frame":
            preview = _DBG_STATE.get("ascii_preview") or []
            preview_hash = hash("\n".join(preview)) if preview else 0
            key = (
                round(_DBG_STATE["yaw_deg"], 2),
                round(_DBG_STATE["pitch_deg"], 2),
                round(_DBG_STATE["pos"][0], 3),
                round(_DBG_STATE["pos"][1], 3),
                round(_DBG_STATE["pos"][2], 3),
                preview_hash,
            )
            if key != _DBG_LAST_PREVIEW_KEY:
                _DBG_LAST_PREVIEW_KEY = key
                _DBG_FRAMES.append({
                    "ts": _DBG_STATE["timestamp"],
                    "frame": _DBG_STATE["frame"],
                    "yaw_deg": _DBG_STATE["yaw_deg"],
                    "pitch_deg": _DBG_STATE["pitch_deg"],
                    "pos": list(_DBG_STATE["pos"]),
                    "center_hit_id": _DBG_STATE["center_hit_id"],
                    "center_depth": _DBG_STATE["center_depth"],
                    "ascii_preview": list(preview),
                    "trigger_keys": list(_DBG_STATE.get("last_keys") or []),
                })
                if len(_DBG_FRAMES) > _DBG_FRAMES_MAX:
                    del _DBG_FRAMES[0:len(_DBG_FRAMES) - _DBG_FRAMES_MAX]
                # Auto-dump tile tree on each new distinct frame
                if _DBG_AUTODUMP and _tile_grid and _ascii_grid:
                    try:
                        meta = {
                            "frame": _DBG_STATE["frame"],
                            "ts": _DBG_STATE["timestamp"],
                            "yaw_deg": _DBG_STATE["yaw_deg"],
                            "pitch_deg": _DBG_STATE["pitch_deg"],
                            "pos": list(_DBG_STATE["pos"]),
                            "trigger_keys": list(_DBG_STATE.get("last_keys") or []),
                            "width": len(_tile_grid[0]) if _tile_grid else 0,
                            "height": len(_tile_grid),
                        }
                        # Run dump outside the lock would be ideal but we're
                        # writing files - do it here, fast enough at <2 deltas/sec.
                        dump_tile_tree(_DBG_STATE["frame"], _tile_grid, _ascii_grid, meta)
                    except Exception:
                        pass

        _dbg_atomic_write()

class _DbgHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a, **kw):
        pass  # silence stdout - the renderer owns it
    def _send(self, status, body, ctype="application/json"):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)
    def do_GET(self):
        if self.path in ("/", "/state", "/state.json"):
            with _DBG_LOCK:
                self._send(200, json.dumps(_DBG_STATE, indent=2))
        elif self.path == "/ascii":
            with _DBG_LOCK:
                lines = list(_DBG_STATE.get("ascii_preview", []))
            self._send(200, "\n".join(lines), ctype="text/plain")
        elif self.path == "/events":
            with _DBG_LOCK:
                self._send(200, json.dumps(_DBG_EVENTS, indent=2))
        elif self.path == "/frames":
            # Index of distinct frames (no ASCII payload, lightweight)
            with _DBG_LOCK:
                idx = [
                    {k: v for k, v in fr.items() if k != "ascii_preview"}
                    for fr in _DBG_FRAMES
                ]
            self._send(200, json.dumps({"count": len(idx), "frames": idx}, indent=2))
        elif self.path == "/frames/latest":
            with _DBG_LOCK:
                fr = _DBG_FRAMES[-1] if _DBG_FRAMES else None
            self._send(200, json.dumps(fr, indent=2) if fr else "null")
        elif self.path.startswith("/frames/"):
            tail = self.path.split("/frames/", 1)[1]
            try:
                i = int(tail)
                with _DBG_LOCK:
                    fr = _DBG_FRAMES[i] if -len(_DBG_FRAMES) <= i < len(_DBG_FRAMES) else None
                if fr is None:
                    self._send(404, '{"error":"index out of range"}')
                else:
                    self._send(200, json.dumps(fr, indent=2))
            except ValueError:
                self._send(400, '{"error":"bad index"}')
        elif self.path == "/frames/clear":
            with _DBG_LOCK:
                _DBG_FRAMES.clear()
            self._send(200, '{"ok":true}')
        elif self.path == "/tiles":
            # Live in-memory tile grid (no disk I/O)
            with _DBG_LOCK:
                tg = globals().get('_DBG_LATEST_TILES')
            if not tg:
                self._send(404, '{"error":"no tiles yet"}')
                return
            grid = []
            for y, row in enumerate(tg):
                for x, cell in enumerate(row):
                    top_mat, bot_mat, top_color, bot_color = cell
                    grid.append({
                        "x": x, "y": y,
                        "top_mat_id": top_mat, "bot_mat_id": bot_mat,
                        "top_name": MAT_NAME.get(top_mat, f"id{top_mat}"),
                        "bot_name": MAT_NAME.get(bot_mat, f"id{bot_mat}"),
                        "top_category": MAT_CATEGORY.get(top_mat, "unknown"),
                        "bot_category": MAT_CATEGORY.get(bot_mat, "unknown"),
                        "top_color": list(top_color),
                        "bot_color": list(bot_color),
                        "transition": MAT_CATEGORY.get(top_mat) != MAT_CATEGORY.get(bot_mat),
                    })
            self._send(200, json.dumps({
                "width": len(tg[0]) if tg else 0,
                "height": len(tg),
                "tiles": grid,
            }))
        elif self.path == "/tiles/dump":
            # Force write of tile directory tree for current frame
            with _DBG_LOCK:
                tg = globals().get('_DBG_LATEST_TILES')
                preview_lines = list(_DBG_STATE.get("ascii_preview") or [])
                meta = {
                    "frame": _DBG_STATE["frame"],
                    "ts": time.time(),
                    "yaw_deg": _DBG_STATE["yaw_deg"],
                    "pitch_deg": _DBG_STATE["pitch_deg"],
                    "pos": list(_DBG_STATE["pos"]),
                    "manual_dump": True,
                }
            if not tg:
                self._send(404, '{"error":"no tiles yet"}')
                return
            ascii_grid = [list(line) for line in preview_lines]
            res = dump_tile_tree(meta["frame"], tg, ascii_grid, meta)
            self._send(200, json.dumps(res, indent=2))
        else:
            self._send(404, '{"error":"not found"}')
    def do_POST(self):
        global _DBG_INPUT_SEQ
        if self.path == "/input":
            try:
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length)) if length else {}
            except Exception:
                self._send(400, '{"error":"bad json"}')
                return
            raw_keys = body.get("keys", [])
            if not isinstance(raw_keys, list) or not raw_keys:
                self._send(400, '{"error":"keys must be a non-empty list of strings"}')
                return
            wait = max(1, int(body.get("wait_frames", 1)))
            with _DBG_LOCK:
                for k in raw_keys:
                    if isinstance(k, str) and k:
                        _DBG_INPUT_QUEUE.append((k, wait))
                _DBG_INPUT_SEQ += 1
                seq = _DBG_INPUT_SEQ
                queued = len(_DBG_INPUT_QUEUE)
                snap = dict(_DBG_STATE)
            snap["accepted_seq"] = seq
            snap["queued"] = queued
            self._send(200, json.dumps(snap, indent=2))
        else:
            self._send(404, '{"error":"not found"}')

def _start_dbg_server():
    if _DBG_DISABLED:
        return None
    def serve():
        try:
            # Allow reuse of the address to avoid "Address already in use" on restart
            class ReuseAddrTCPServer(socketserver.TCPServer):
                allow_reuse_address = True
            
            server = ReuseAddrTCPServer(("127.0.0.1", _DBG_PORT), _DbgHandler)
            print(f"[dbg] HTTP server started on port {_DBG_PORT}", file=sys.stderr, flush=True)
            server.serve_forever()
        except Exception as e:
            print(f"[dbg] HTTP server error: {e}", file=sys.stderr, flush=True)
            import traceback
            traceback.print_exc(file=sys.stderr)
    t = threading.Thread(target=serve, daemon=True)
    t.start()
    time.sleep(0.2)  # Give server a moment to bind
    return t

_DBG_THREAD = None  # started in __main__ only

# ANSI codes
CLEAR = "\033[2J"
HOME = "\033[H"
HIDE_CURSOR = "\033[?25l"
SHOW_CURSOR = "\033[?25h"
RESET = "\033[0m"
BOLD = "\033[1m"

# Camera control settings
TURN_SPEED = 0.12      # Radians per keypress (faster turning)
PITCH_SPEED = 0.08     # Radians per keypress
MOVE_SPEED = 0.2       # Units per keypress (faster movement)
MAX_PITCH = 1.2        # ~70 degrees up/down limit

def goto(row, col):
    return f"\033[{row};{col}H"

def rgb(r, g, b):
    return f"\033[38;2;{int(max(0,min(255,r)))};{int(max(0,min(255,g)))};{int(max(0,min(255,b)))}m"

def bg_rgb(r, g, b):
    return f"\033[48;2;{int(max(0,min(255,r)))};{int(max(0,min(255,g)))};{int(max(0,min(255,b)))}m"

# === ENHANCED SETTINGS ===
WIDTH = 60    # Reduced for speed
HEIGHT = 20   # Reduced (40 effective with half-blocks)
use_half_blocks = True  # Double vertical resolution
use_msaa = False        # Disabled for speed - toggle with 'M' key
MSAA_SAMPLES = 2        # 2x2 supersampling when enabled
use_edge_detection = False  # Disabled for speed - toggle with 'E' key
_SKIP_SHADOWS = False   # Toggle with 'T' key for ~2x speedup
USE_BACKGROUND_COLOR = True  # Solid surface rendering
TARGET_FPS = 30         # Target frame rate

# Gamma correction LUT (sRGB 2.2) — matches FramePipeline.gamma_correct()
_GAMMA_TABLE = [int(pow(i / 255.0, 1.0 / 2.2) * 255) for i in range(256)]

def _gamma(color):
    return (_GAMMA_TABLE[max(0, min(255, color[0]))],
            _GAMMA_TABLE[max(0, min(255, color[1]))],
            _GAMMA_TABLE[max(0, min(255, color[2]))])

# Half-block characters for 2x vertical resolution
UPPER_HALF = '▀'
LOWER_HALF = '▄'
FULL_BLOCK = '█'
EMPTY = ' '

# Enhanced character density ramp (70 levels)
DENSITY_CHARS = " .'`^\",:;Il!i><~+_-?][}{1)(|/tfjrxnuvczXYUJCLQ0OZmwqpdbkhao*#MW&8%B@$"

# Edge detection characters
EDGE_H = '─'
EDGE_V = '│'
EDGE_TL = '┌'
EDGE_TR = '┐'
EDGE_BL = '└'
EDGE_BR = '┘'
EDGE_CROSS = '┼'

class Vec3:
    __slots__ = ['x', 'y', 'z']  # Memory optimization
    
    def __init__(self, x=0, y=0, z=0):
        self.x, self.y, self.z = x, y, z
    
    def __add__(self, o): return Vec3(self.x + o.x, self.y + o.y, self.z + o.z)
    def __sub__(self, o): return Vec3(self.x - o.x, self.y - o.y, self.z - o.z)
    def __mul__(self, s): return Vec3(self.x * s, self.y * s, self.z * s)
    def dot(self, o): return self.x * o.x + self.y * o.y + self.z * o.z
    def length(self): return math.sqrt(self.x**2 + self.y**2 + self.z**2)
    def normalize(self):
        l = self.length()
        return Vec3(self.x/l, self.y/l, self.z/l) if l > 0.001 else Vec3()

class Camera:
    def __init__(self):
        self.pos = Vec3(0, 1.6, 0)
        self.yaw = 0
        self.pitch = 0
        self.fov = 90
    
    def get_ray_dir(self, screen_x, screen_y, width, height):
        # Normalize screen coordinates to [-1, 1]
        nx = (screen_x - width/2) / (width/2)
        ny = (screen_y - height/2) / (height/2)

        # Aspect ratio: width/height in render-pixels (half-blocks make pixels ~square)
        aspect = width / height
        fov_rad = self.fov * math.pi / 180
        tan_half = math.tan(fov_rad / 2)

        # Build ray in camera space (looking down +Z)
        dx = nx * tan_half * aspect
        dy = -ny * tan_half
        dz = 1.0

        # Rotate by pitch (around X axis): dy/dz coupled
        cos_p, sin_p = math.cos(self.pitch), math.sin(self.pitch)
        py = dy * cos_p - dz * sin_p
        pz = dy * sin_p + dz * cos_p

        # Rotate by yaw (around Y axis): dx/pz coupled
        cos_y, sin_y = math.cos(self.yaw), math.sin(self.yaw)
        rx = dx * cos_y + pz * sin_y
        rz = -dx * sin_y + pz * cos_y

        return Vec3(rx, py, rz).normalize()

class Material:
    __slots__ = ['color', 'roughness', 'emissive', 'id']
    _id_counter = 0
    
    def __init__(self, color, roughness=0.5, emissive=0):
        self.color = color
        self.roughness = roughness
        self.emissive = emissive
        self.id = Material._id_counter
        Material._id_counter += 1

# Room materials with unique IDs for edge detection
MAT_WALL = Material((180, 175, 165), 0.8)
MAT_FLOOR = Material((120, 90, 60), 0.6)
MAT_CEILING = Material((240, 240, 235), 0.9)
MAT_BED_FRAME = Material((80, 50, 30), 0.4)
MAT_BED_SHEET = Material((220, 220, 240), 0.7)
MAT_PILLOW = Material((250, 250, 250), 0.8)
MAT_DESK = Material((60, 40, 25), 0.3)
MAT_MONITOR = Material((20, 20, 25), 0.1)
MAT_MONITOR_SCREEN = Material((100, 150, 200), 0.1, 0.8)
MAT_CHAIR = Material((40, 40, 45), 0.5)
MAT_WINDOW = Material((180, 220, 255), 0.1, 0.3)
MAT_CURTAIN = Material((150, 60, 60), 0.9)
MAT_LAMP = Material((255, 240, 200), 0.2, 1.0)
MAT_NIGHTSTAND = Material((70, 45, 25), 0.4)
MAT_POSTER = Material((200, 150, 100), 0.8)
MAT_RUG = Material((100, 50, 50), 0.9)
MAT_PLANT = Material((50, 120, 50), 0.7)
MAT_BOOKSHELF = Material((90, 60, 35), 0.5)
MAT_DOOR = Material((100, 70, 40), 0.5)

# === MATERIAL METADATA (id -> name, category) ===
MAT_NAME = {
    0: "wall", 1: "floor", 2: "ceiling",
    3: "bed_frame", 4: "bed_sheet", 5: "pillow",
    6: "desk", 7: "monitor", 8: "monitor_screen", 9: "chair",
    10: "window", 11: "curtain",
    12: "lamp",
    13: "nightstand", 14: "poster", 15: "rug",
    16: "plant", 17: "bookshelf", 18: "door",
    -1: "sky",
}
MAT_CATEGORY = {
    0: "walls", 1: "floor", 2: "ceiling",
    3: "bed", 4: "bed", 5: "bed",
    6: "desk_setup", 7: "desk_setup", 8: "desk_setup", 9: "desk_setup",
    10: "windows", 11: "windows",
    12: "lights",
    13: "furniture", 17: "furniture",
    14: "decor", 15: "decor", 16: "decor",
    18: "doors",
    -1: "sky",
}
# Single-character category code for the map.txt overlay
MAT_CAT_CHAR = {
    "walls": "W", "floor": "F", "ceiling": "C",
    "bed": "b", "desk_setup": "d", "windows": "n",
    "lights": "L", "furniture": "f", "decor": "."  ,
    "doors": "D", "sky": "~", "transition": "+",
}

class Box:
    __slots__ = ['min', 'max', 'mat']
    
    def __init__(self, min_p, max_p, material):
        self.min = min_p
        self.max = max_p
        self.mat = material
    
    def intersect(self, ray_origin, ray_dir):
        # Unpack once (hot path)
        ox, oy, oz = ray_origin.x, ray_origin.y, ray_origin.z
        dx, dy, dz = ray_dir.x, ray_dir.y, ray_dir.z
        bmin = self.min; bmax = self.max

        # X slab
        if dx > 1e-6 or dx < -1e-6:
            inv = 1.0 / dx
            t1 = (bmin.x - ox) * inv
            t2 = (bmax.x - ox) * inv
            if t1 > t2: t1, t2 = t2, t1
            tmin = t1; tmax = t2
        else:
            if ox < bmin.x or ox > bmax.x: return None, None, None
            tmin = -1e18; tmax = 1e18

        # Y slab
        if dy > 1e-6 or dy < -1e-6:
            inv = 1.0 / dy
            t1 = (bmin.y - oy) * inv
            t2 = (bmax.y - oy) * inv
            if t1 > t2: t1, t2 = t2, t1
            if t1 > tmin: tmin = t1
            if t2 < tmax: tmax = t2
            if tmin > tmax: return None, None, None
        else:
            if oy < bmin.y or oy > bmax.y: return None, None, None

        # Z slab
        if dz > 1e-6 or dz < -1e-6:
            inv = 1.0 / dz
            t1 = (bmin.z - oz) * inv
            t2 = (bmax.z - oz) * inv
            if t1 > t2: t1, t2 = t2, t1
            if t1 > tmin: tmin = t1
            if t2 < tmax: tmax = t2
            if tmin > tmax: return None, None, None
        else:
            if oz < bmin.z or oz > bmax.z: return None, None, None

        if tmin < 0.001:
            tmin = tmax
        if tmin < 0.001:
            return None, None, None

        hx = ox + dx * tmin
        hy = oy + dy * tmin
        hz = oz + dz * tmin

        eps = 0.001
        if abs(hx - bmin.x) < eps: normal = Vec3(-1, 0, 0)
        elif abs(hx - bmax.x) < eps: normal = Vec3(1, 0, 0)
        elif abs(hy - bmin.y) < eps: normal = Vec3(0, -1, 0)
        elif abs(hy - bmax.y) < eps: normal = Vec3(0, 1, 0)
        elif abs(hz - bmin.z) < eps: normal = Vec3(0, 0, -1)
        else: normal = Vec3(0, 0, 1)

        return tmin, normal, self.mat.id

def create_bedroom():
    objects = []
    room_w, room_h, room_d = 5, 3, 4

    # Monolithic floor + ceiling (procedural checker applied in trace_ray)
    objects.append(Box(Vec3(-room_w/2, -0.1, -1), Vec3(room_w/2, 0, room_d), MAT_FLOOR))
    objects.append(Box(Vec3(-room_w/2, room_h, -1), Vec3(room_w/2, room_h+0.1, room_d), MAT_CEILING))
    
    # Back wall
    objects.append(Box(Vec3(-room_w/2, 0, room_d), Vec3(room_w/2, room_h, room_d+0.1), MAT_WALL))
    # Left wall
    objects.append(Box(Vec3(-room_w/2-0.1, 0, -1), Vec3(-room_w/2, room_h, room_d), MAT_WALL))
    # Right wall
    objects.append(Box(Vec3(room_w/2, 0, -1), Vec3(room_w/2+0.1, room_h, room_d), MAT_WALL))
    # Front wall parts (with door gap)
    objects.append(Box(Vec3(-room_w/2, 0, -1.1), Vec3(-0.5, room_h, -1), MAT_WALL))
    objects.append(Box(Vec3(0.5, 0, -1.1), Vec3(room_w/2, room_h, -1), MAT_WALL))
    objects.append(Box(Vec3(-0.5, 2.2, -1.1), Vec3(0.5, room_h, -1), MAT_WALL))
    objects.append(Box(Vec3(-0.5, 0, -1.05), Vec3(0.5, 2.2, -1), MAT_DOOR))
    
    # Bed
    bed_x = 1.5
    objects.append(Box(Vec3(bed_x-0.5, 0, 2), Vec3(bed_x+1, 0.4, 3.8), MAT_BED_FRAME))
    objects.append(Box(Vec3(bed_x-0.45, 0.4, 2.05), Vec3(bed_x+0.95, 0.6, 3.75), MAT_BED_SHEET))
    objects.append(Box(Vec3(bed_x-0.3, 0.6, 3.3), Vec3(bed_x+0.8, 0.75, 3.7), MAT_PILLOW))
    objects.append(Box(Vec3(bed_x-0.5, 0.4, 3.75), Vec3(bed_x+1, 1.2, 3.85), MAT_BED_FRAME))
    
    # Nightstand + Lamp
    objects.append(Box(Vec3(bed_x-1, 0, 3.2), Vec3(bed_x-0.6, 0.5, 3.7), MAT_NIGHTSTAND))
    objects.append(Box(Vec3(bed_x-0.9, 0.5, 3.35), Vec3(bed_x-0.7, 0.8, 3.55), MAT_LAMP))
    
    # Desk
    desk_x = -1.8
    objects.append(Box(Vec3(desk_x-0.6, 0.7, 2.5), Vec3(desk_x+0.6, 0.75, 3.5), MAT_DESK))
    objects.append(Box(Vec3(desk_x-0.55, 0, 2.55), Vec3(desk_x-0.45, 0.7, 2.65), MAT_DESK))
    objects.append(Box(Vec3(desk_x+0.45, 0, 2.55), Vec3(desk_x+0.55, 0.7, 2.65), MAT_DESK))
    objects.append(Box(Vec3(desk_x-0.55, 0, 3.35), Vec3(desk_x-0.45, 0.7, 3.45), MAT_DESK))
    objects.append(Box(Vec3(desk_x+0.45, 0, 3.35), Vec3(desk_x+0.55, 0.7, 3.45), MAT_DESK))
    
    # Monitor
    objects.append(Box(Vec3(desk_x-0.35, 0.75, 3.1), Vec3(desk_x+0.35, 1.2, 3.15), MAT_MONITOR))
    objects.append(Box(Vec3(desk_x-0.3, 0.8, 3.05), Vec3(desk_x+0.3, 1.15, 3.1), MAT_MONITOR_SCREEN))
    objects.append(Box(Vec3(desk_x-0.1, 0.75, 3.0), Vec3(desk_x+0.1, 0.78, 3.2), MAT_MONITOR))
    
    # Chair
    objects.append(Box(Vec3(desk_x-0.25, 0.4, 2.0), Vec3(desk_x+0.25, 0.45, 2.5), MAT_CHAIR))
    objects.append(Box(Vec3(desk_x-0.25, 0.45, 2.4), Vec3(desk_x+0.25, 0.9, 2.5), MAT_CHAIR))
    
    # Window + Curtains
    objects.append(Box(Vec3(-0.6, 1.0, 3.95), Vec3(0.6, 2.2, 4.0), MAT_WINDOW))
    objects.append(Box(Vec3(-1.0, 0.8, 3.9), Vec3(-0.6, 2.4, 3.95), MAT_CURTAIN))
    objects.append(Box(Vec3(0.6, 0.8, 3.9), Vec3(1.0, 2.4, 3.95), MAT_CURTAIN))
    
    # Bookshelf
    shelf_x = -2.4
    objects.append(Box(Vec3(shelf_x-0.15, 0, 0.5), Vec3(shelf_x+0.15, 1.8, 1.5), MAT_BOOKSHELF))
    for sy in [0.4, 0.9, 1.4]:
        objects.append(Box(Vec3(shelf_x-0.14, sy, 0.52), Vec3(shelf_x+0.14, sy+0.03, 1.48), MAT_BOOKSHELF))
    
    # Books with varied colors
    random.seed(42)  # Consistent books
    for i, bz in enumerate([0.6, 0.75, 0.9, 1.1, 1.25]):
        h = random.uniform(0.2, 0.35)
        c = [(180,50,50), (50,50,180), (50,150,50), (200,150,50), (150,50,150)][i % 5]
        objects.append(Box(Vec3(shelf_x-0.12, 0.43, bz), Vec3(shelf_x+0.12, 0.43+h, bz+0.1), Material(c, 0.6)))
    
    # Rug, Plant, Poster
    objects.append(Box(Vec3(-0.8, 0.01, 1.0), Vec3(0.8, 0.02, 2.5), MAT_RUG))
    objects.append(Box(Vec3(2.0, 0, 0.5), Vec3(2.3, 0.3, 0.8), Material((80, 60, 40), 0.5)))
    objects.append(Box(Vec3(2.05, 0.3, 0.55), Vec3(2.25, 0.9, 0.75), MAT_PLANT))
    objects.append(Box(Vec3(1.5, 1.5, 3.95), Vec3(2.2, 2.3, 3.98), MAT_POSTER))
    
    return objects

def trace_ray(ray_origin, ray_dir, objects, light_pos):
    """Trace ray and return (color, material_id, depth)"""
    closest_t = float('inf')
    closest_obj = None
    closest_normal = None
    closest_mat_id = -1
    
    for obj in objects:
        t, normal, mat_id = obj.intersect(ray_origin, ray_dir)
        if t and t < closest_t:
            closest_t = t
            closest_obj = obj
            closest_normal = normal
            closest_mat_id = mat_id
    
    if closest_obj is None:
        return (40, 40, 60), -1, float('inf')
    
    hit = Vec3(
        ray_origin.x + ray_dir.x * closest_t,
        ray_origin.y + ray_dir.y * closest_t,
        ray_origin.z + ray_dir.z * closest_t
    )
    
    mat = closest_obj.mat
    base_color = mat.color

    # Procedural checker on floor (id) and ceiling (id)
    if mat.id == MAT_FLOOR.id or mat.id == MAT_CEILING.id:
        if (int(math.floor(hit.x * 2)) + int(math.floor(hit.z * 2))) & 1:
            base_color = tuple(int(c * 0.7) for c in base_color)

    if mat.emissive > 0:
        glow = tuple(int(c * (0.5 + mat.emissive * 0.5)) for c in base_color)
        return glow, closest_mat_id, closest_t
    
    # Lighting
    light_dir = Vec3(light_pos.x - hit.x, light_pos.y - hit.y, light_pos.z - hit.z)
    light_dist = light_dir.length()
    light_dir = light_dir.normalize()
    
    ndotl = max(0, closest_normal.dot(light_dir))

    # Shadow (skip when face is back-lit; ndotl already 0 -> no diffuse anyway)
    shadow = 0.0
    if ndotl > 0.0 and not _SKIP_SHADOWS:
        shadow_origin = Vec3(hit.x + closest_normal.x * 0.01,
                             hit.y + closest_normal.y * 0.01,
                             hit.z + closest_normal.z * 0.01)
        for obj in objects:
            if obj is closest_obj:
                continue
            t, _, _ = obj.intersect(shadow_origin, light_dir)
            if t is not None and t < light_dist:
                shadow = 0.5
                break
    
    ambient = 0.3
    diffuse = ndotl * 0.7 * (1 - shadow)
    atten = 1.0 / (1 + light_dist * 0.1)
    brightness = ambient + diffuse * atten
    
    # Subtle texture noise
    noise = math.sin(hit.x * 10) * math.sin(hit.z * 10) * 0.03
    brightness = max(0.1, min(1.0, brightness + noise))
    
    color = tuple(int(c * brightness) for c in base_color)
    return color, closest_mat_id, closest_t

def trace_ray_msaa(camera, x, y, width, height, objects, light_pos):
    """Multi-sample anti-aliasing - average sub-pixel samples"""
    global use_msaa
    ray_dir = camera.get_ray_dir(x, y, width, height)
    color, mat_id, depth = trace_ray(camera.pos, ray_dir, objects, light_pos)
    
    if not use_msaa:
        return color, mat_id, depth, False
    
    # Simple 2-sample AA - diagonal
    ray_dir2 = camera.get_ray_dir(x + 0.5, y + 0.5, width, height)
    color2, mat_id2, _ = trace_ray(camera.pos, ray_dir2, objects, light_pos)
    
    # Average colors
    avg_color = tuple((color[i] + color2[i]) // 2 for i in range(3))
    is_edge = mat_id != mat_id2
    
    return avg_color, mat_id, depth, is_edge

def render_frame_enhanced(camera, objects, light_pos, frame):
    """Enhanced rendering with half-blocks - optimized for speed.
    Also builds an ASCII brightness preview and per-tile feature grid
    for the debug server."""
    global use_half_blocks, use_msaa, use_edge_detection
    
    # Render at double vertical resolution for half-blocks
    render_height = HEIGHT * 2 if use_half_blocks else HEIGHT
    
    # Compose output buffer directly
    buffer = [[' '] * WIDTH for _ in range(HEIGHT)]
    colors_out = [[RESET] * WIDTH for _ in range(HEIGHT)]
    ascii_preview = [[' '] * WIDTH for _ in range(HEIGHT)]
    # Per-tile feature grid: each cell is dict {top_mat,bot_mat,top_color,bot_color}
    tile_grid = [[None] * WIDTH for _ in range(HEIGHT)]
    n_density = len(DENSITY_CHARS)
    
    if use_half_blocks:
        # Render pairs of rows at once
        for char_y in range(HEIGHT):
            top_y = char_y * 2
            bot_y = char_y * 2 + 1
            
            for x in range(WIDTH):
                # Top pixel
                ray_dir1 = camera.get_ray_dir(x, top_y, WIDTH, render_height)
                top_color, top_mat, _ = trace_ray(camera.pos, ray_dir1, objects, light_pos)
                top_color = _gamma(top_color)
                
                # Bottom pixel
                ray_dir2 = camera.get_ray_dir(x, bot_y, WIDTH, render_height)
                bot_color, bot_mat, _ = trace_ray(camera.pos, ray_dir2, objects, light_pos)
                bot_color = _gamma(bot_color)
                
                # Output with half-block
                buffer[char_y][x] = UPPER_HALF
                colors_out[char_y][x] = rgb(*top_color) + bg_rgb(*bot_color)
                
                # ASCII preview from average luminance
                br = (top_color[0]*0.3 + top_color[1]*0.59 + top_color[2]*0.11
                      + bot_color[0]*0.3 + bot_color[1]*0.59 + bot_color[2]*0.11) / 510.0
                idx = max(0, min(n_density - 1, int(br * n_density)))
                ascii_preview[char_y][x] = DENSITY_CHARS[idx]
                
                # Tile feature record
                tile_grid[char_y][x] = (top_mat, bot_mat, top_color, bot_color)
    else:
        # Standard single-pixel rendering
        for y in range(HEIGHT):
            for x in range(WIDTH):
                ray_dir = camera.get_ray_dir(x, y, WIDTH, HEIGHT)
                color, mat, _ = trace_ray(camera.pos, ray_dir, objects, light_pos)
                
                brightness = (color[0] * 0.3 + color[1] * 0.59 + color[2] * 0.11) / 255
                char_idx = min(n_density - 1, int(brightness * n_density))
                
                buffer[y][x] = DENSITY_CHARS[char_idx]
                colors_out[y][x] = rgb(*color)
                ascii_preview[y][x] = DENSITY_CHARS[char_idx]
                tile_grid[y][x] = (mat, mat, color, color)
    
    return buffer, colors_out, ascii_preview, tile_grid

def draw_ui_enhanced(buffer, colors, frame, camera, fps, mode):
    """Draw UI overlay with controls help"""
    # Title with gradient
    title = " ═══ BEDROOM 3D ═══ "
    tx = (WIDTH - len(title)) // 2
    for i, c in enumerate(title):
        if 0 <= tx + i < WIDTH:
            buffer[0][tx + i] = c
            hue = (i * 15 + frame * 3) % 360
            r = int(180 + 75 * math.sin(hue * 0.017))
            g = int(180 + 75 * math.sin((hue + 120) * 0.017))
            b = int(180 + 75 * math.sin((hue + 240) * 0.017))
            colors[0][tx + i] = rgb(r, g, b) + BOLD
    
    # Controls help line - shorter for speed
    controls = "WASD:Move IJKL:Look SPACE:Auto M:MSAA E:Edge Q:Quit"
    for i, c in enumerate(controls):
        if 2 + i < WIDTH:
            buffer[1][2 + i] = c
            colors[1][2 + i] = rgb(100, 150, 200)
    
    # Crosshair
    cx, cy = WIDTH // 2, HEIGHT // 2
    if cy < HEIGHT and cx < WIDTH:
        buffer[cy][cx] = '◎'
        colors[cy][cx] = rgb(255, 255, 255) + BOLD
    
    # Status bar (compact, fits 60 cols)
    mode_str = "A" if mode == "auto" else "M"
    status = f"Y{math.degrees(camera.yaw):+5.0f} P{math.degrees(camera.pitch):+4.0f} F{fps:4.1f} [{mode_str}] MSAA:{'1' if use_msaa else '0'} E:{'1' if use_edge_detection else '0'}"
    for i, c in enumerate(status):
        if 1 + i < WIDTH:
            buffer[HEIGHT - 1][1 + i] = c
            colors[HEIGHT - 1][1 + i] = rgb(120, 180, 120)


def get_keyboard_input():
    """Non-blocking keyboard input for Windows. Dedupes within a frame so
    OS auto-repeat at low FPS doesn't catapult the camera."""
    if not msvcrt:
        return []  # Headless mode (CI runner)
    
    seen = set()
    keys = []
    while msvcrt.kbhit():
        ch = msvcrt.getch()
        if ch == b'\xe0':  # Arrow key prefix
            ch2 = msvcrt.getch()
            mapping = {b'H': 'UP', b'P': 'DOWN', b'K': 'LEFT', b'M': 'RIGHT'}
            k = mapping.get(ch2)
            if k and k not in seen:
                seen.add(k); keys.append(k)
        elif ch == b'\x00':  # Function key prefix
            msvcrt.getch()
        else:
            try:
                k = ch.decode('utf-8').lower()
                if k not in seen:
                    seen.add(k); keys.append(k)
            except Exception:
                pass
    return keys


def process_input(keys, camera, mode):
    """Process keyboard input and update camera"""
    global use_msaa, use_edge_detection
    
    for key in keys:
        # Quit
        if key == 'q':
            return None, mode
        
        # Toggle auto/manual mode (SPACE or '/' as agent-friendly alias)
        if key == ' ' or key == '/':
            mode = 'auto' if mode == 'manual' else 'manual'
        
        # Toggle features
        if key == 'm':
            use_msaa = not use_msaa
        if key == 'e':
            use_edge_detection = not use_edge_detection
        if key == 't':
            globals()['_SKIP_SHADOWS'] = not globals()['_SKIP_SHADOWS']
        if key == 'x':
            globals()['show_debug'] = not globals().get('show_debug', False)
        
        # Look controls (arrows and IJKL) - accumulate for smoother turning
        if key in ('LEFT', 'j'):
            camera.yaw -= TURN_SPEED
        if key in ('RIGHT', 'l'):
            camera.yaw += TURN_SPEED
        if key in ('UP', 'i'):
            camera.pitch = max(-MAX_PITCH, camera.pitch - PITCH_SPEED)
        if key in ('DOWN', 'k'):
            camera.pitch = min(MAX_PITCH, camera.pitch + PITCH_SPEED)
        
        # Movement controls (WASD)
        if key == 'w':
            camera.pos.x += math.sin(camera.yaw) * MOVE_SPEED
            camera.pos.z += math.cos(camera.yaw) * MOVE_SPEED
        if key == 's':
            camera.pos.x -= math.sin(camera.yaw) * MOVE_SPEED
            camera.pos.z -= math.cos(camera.yaw) * MOVE_SPEED
        if key == 'a':
            camera.pos.x -= math.cos(camera.yaw) * MOVE_SPEED
            camera.pos.z += math.sin(camera.yaw) * MOVE_SPEED
        if key == 'd':
            camera.pos.x += math.cos(camera.yaw) * MOVE_SPEED
            camera.pos.z -= math.sin(camera.yaw) * MOVE_SPEED
        
        # Vertical movement
        if key == 'r':
            camera.pos.y = min(2.5, camera.pos.y + 0.15)
        if key == 'f':
            camera.pos.y = max(0.5, camera.pos.y - 0.15)
        
        # Clamp position inside room
        camera.pos.x = max(-2.3, min(2.3, camera.pos.x))
        camera.pos.z = max(-0.5, min(3.5, camera.pos.z))
    
    return camera, mode

# === MAIN ===
if __name__ == "__main__":
    sys.stderr.write("[renderer] about to call _start_dbg_server()\n")
    sys.stderr.flush()
    _DBG_THREAD = _start_dbg_server()
    sys.stderr.write("[renderer] _DBG_THREAD created\n")
    sys.stderr.flush()

    sys.stdout.write(HIDE_CURSOR + CLEAR)
    sys.stdout.flush()

    bedroom = create_bedroom()
    camera = Camera()
    camera.pos = Vec3(0, 1.6, 0.5)

    main_light = Vec3(0, 2.7, 2)
    secondary_light = Vec3(-1.5, 2.0, 3.0)  # Near desk

    # Control mode: 'auto' for automatic rotation, 'manual' for keyboard control
    mode = 'manual'
    show_debug = False
    frame = 0
    last_time = time.time()
    fps = 0.0
    frame_times = []

    try:
        # Initial publish so /state has data even before first frame
        dbg_publish(
            "init",
            mode=mode,
            pos=[camera.pos.x, camera.pos.y, camera.pos.z],
            yaw_deg=math.degrees(camera.yaw),
            pitch_deg=math.degrees(camera.pitch),
            use_msaa=use_msaa,
            use_edge_detection=use_edge_detection,
            skip_shadows=_SKIP_SHADOWS,
            show_debug=show_debug,
        )
        sys.stderr.write("[renderer] initial publish done, entering main loop\n")
        sys.stderr.flush()
        while True:
            frame_start = time.time()

            # Drain queued keys from POST /input (one per wait_frames, no dedupe)
            queued_keys = []
            with _DBG_LOCK:
                if _DBG_INPUT_QUEUE:
                    if _DBG_INPUT_WAIT <= 0:
                        k, wf = _DBG_INPUT_QUEUE.pop(0)
                        queued_keys.append(k)
                        _DBG_INPUT_WAIT = wf - 1
                    else:
                        _DBG_INPUT_WAIT -= 1

            # Process keyboard input (human keys deduped, queued keys prepended verbatim)
            keys = get_keyboard_input()
            keys = queued_keys + keys
            result, mode = process_input(keys, camera, mode)
            if result is None:  # Quit requested
                dbg_publish("quit", last_keys=keys, mode=mode)
                break
            camera = result

            # Publish keystroke event immediately (BEFORE render) so the agent
            # sees pose updates even on slow frames
            if keys:
                with _DBG_LOCK:
                    _DBG_STATE["key_seq"] = _DBG_STATE.get("key_seq", 0) + 1
                dbg_publish(
                    "keystroke",
                    last_keys=keys,
                    mode=mode,
                    pos=[camera.pos.x, camera.pos.y, camera.pos.z],
                    yaw_deg=math.degrees(camera.yaw),
                    pitch_deg=math.degrees(camera.pitch),
                    use_msaa=use_msaa,
                    use_edge_detection=use_edge_detection,
                    skip_shadows=_SKIP_SHADOWS,
                    show_debug=show_debug,
                )

            # Auto-rotate in auto mode
            if mode == 'auto':
                camera.yaw = math.sin(frame * 0.04) * 1.0  # Faster, wider sweep
                camera.pitch = math.sin(frame * 0.025) * 0.25

            # Render
            buffer, colors, ascii_preview, tile_grid = render_frame_enhanced(camera, bedroom, main_light, frame)

            # Calculate FPS
            current_time = time.time()
            frame_times.append(current_time - frame_start)
            if len(frame_times) > 20:
                frame_times.pop(0)
            if frame_times:
                avg_frame_time = sum(frame_times) / len(frame_times)
                fps = 1.0 / avg_frame_time if avg_frame_time > 0 else 0

            # UI
            draw_ui_enhanced(buffer, colors, frame, camera, fps, mode)

            # Debug overlay (X to toggle): show center-pixel hit info
            if globals().get('show_debug', False):
                cx, cy = WIDTH // 2, HEIGHT
                ray = camera.get_ray_dir(cx, cy, WIDTH, HEIGHT * 2 if use_half_blocks else HEIGHT)
                _, hit_id, hit_t = trace_ray(camera.pos, ray, bedroom, main_light)
                dbg = f"R({ray.x:+.2f},{ray.y:+.2f},{ray.z:+.2f}) hit={hit_id} t={hit_t if hit_t!=float('inf') else -1:.2f}"
                for i, c in enumerate(dbg[:WIDTH - 2]):
                    if 2 + i < WIDTH:
                        buffer[2][2 + i] = c
                        colors[2][2 + i] = rgb(255, 220, 80)

            # Output
            sys.stdout.write(HOME)
            for y in range(HEIGHT):
                sys.stdout.write(goto(y + 1, 1))
                line = ''.join(colors[y][x] + buffer[y][x] for x in range(WIDTH))
                sys.stdout.write(line + RESET)
            sys.stdout.flush()

            # Publish per-frame debug state. Cheap one extra center-ray trace.
            cx_mid, cy_mid = WIDTH // 2, HEIGHT
            _ray = camera.get_ray_dir(cx_mid, cy_mid, WIDTH, HEIGHT * 2 if use_half_blocks else HEIGHT)
            _, _hid, _ht = trace_ray(camera.pos, _ray, bedroom, main_light)
            # Stash latest tile grid for /tiles endpoint (in-memory only)
            with _DBG_LOCK:
                globals()['_DBG_LATEST_TILES'] = tile_grid
            dbg_publish(
                "frame",
                frame=frame,
                fps=fps,
                mode=mode,
                pos=[camera.pos.x, camera.pos.y, camera.pos.z],
                yaw_deg=math.degrees(camera.yaw),
                pitch_deg=math.degrees(camera.pitch),
                use_msaa=use_msaa,
                use_edge_detection=use_edge_detection,
                skip_shadows=_SKIP_SHADOWS,
                show_debug=show_debug,
                center_ray=[_ray.x, _ray.y, _ray.z],
                center_hit_id=_hid,
                center_depth=(_ht if _ht != float('inf') else -1.0),
                ascii_preview=[''.join(row) for row in ascii_preview],
                last_keys=keys,  # may be empty for idle frames; reflects this-frame input
                _tile_grid=tile_grid,  # forwarded to dump on delta
                _ascii_preview_grid=ascii_preview,
            )

            frame += 1

            # Frame cap: sleep only when ahead of budget; skip sleep when over budget
            elapsed = time.time() - frame_start
            sleep_time = (1.0 / TARGET_FPS) - elapsed
            if sleep_time > 0:
                time.sleep(sleep_time)

        sys.stdout.write(goto(HEIGHT + 2, 1))
        print(f"\n{BOLD}{rgb(100, 255, 100)}Render complete ({frame} frames){RESET}")
        print(f"{rgb(200,200,200)}  Average FPS: {fps:.1f}{RESET}\n")

    except KeyboardInterrupt:
        sys.stdout.write(goto(HEIGHT + 2, 1))
        print(f"\n{rgb(255, 200, 100)}Render interrupted after {frame} frames{RESET}\n")
    finally:
        sys.stdout.write(SHOW_CURSOR + RESET)
        sys.stdout.flush()
