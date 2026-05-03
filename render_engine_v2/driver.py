"""Headless driver: import the renderer as a library, simulate camera moves,
ground state after each step. No terminal window, no HTTP, no keystroke injection."""
import sys, os, math

os.environ["DBG_DISABLE"] = "1"

src_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "test_bedroom_enhanced.py")
with open(src_path, "r", encoding="utf-8") as f:
    src = f.read()
header = src[:src.index("# === MAIN ===")]
ns = {"__name__": "_headless", "__file__": src_path}
exec(compile(header, src_path, "exec"), ns)

Vec3 = ns["Vec3"]
Camera = ns["Camera"]
trace_ray = ns["trace_ray"]
create_bedroom = ns["create_bedroom"]
render_frame_enhanced = ns["render_frame_enhanced"]
MAT_NAME = ns["MAT_NAME"]
MAT_CATEGORY = ns["MAT_CATEGORY"]
MAT_CAT_CHAR = ns["MAT_CAT_CHAR"]
MOVE_SPEED = ns["MOVE_SPEED"]
TURN_SPEED = ns["TURN_SPEED"]
PITCH_SPEED = ns["PITCH_SPEED"]
WIDTH = ns["WIDTH"]
HEIGHT = ns["HEIGHT"]

bedroom = create_bedroom()
camera = Camera()
camera.pos = Vec3(0, 1.6, 0.5)
light = Vec3(0, 2.7, 2)


def ground(label, step, camera, tile_grid, ascii_preview):
    cx, cy = WIDTH // 2, HEIGHT
    ray = camera.get_ray_dir(cx, cy, WIDTH, HEIGHT * 2)
    _, hit_id, hit_t = trace_ray(camera.pos, ray, bedroom, light)
    hit_name = MAT_NAME.get(hit_id, f"id{hit_id}")
    depth = hit_t if hit_t != float('inf') else -1.0

    cat_lines = []
    for y in range(HEIGHT):
        row = []
        for x in range(WIDTH):
            top_mat, bot_mat, _, _ = tile_grid[y][x]
            top_cat = MAT_CATEGORY.get(top_mat, "unknown")
            bot_cat = MAT_CATEGORY.get(bot_mat, "unknown")
            if top_cat != bot_cat:
                row.append(MAT_CAT_CHAR.get("transition", "+"))
            else:
                row.append(MAT_CAT_CHAR.get(top_cat, "?"))
        cat_lines.append("".join(row))

    print(f"=== {label} (step {step}) ===")
    print(f"pos=({camera.pos.x:.4f}, {camera.pos.y:.4f}, {camera.pos.z:.4f})")
    print(f"yaw={math.degrees(camera.yaw):.3f} deg  pitch={math.degrees(camera.pitch):.3f} deg")
    print(f"center_hit={hit_id} ({hit_name})  depth={depth:.3f}")
    print()
    print("ASCII preview:")
    for i, row in enumerate(ascii_preview):
        print(f"  {i:2d}| {''.join(row)}")
    print()
    print("Category map:")
    for i, line in enumerate(cat_lines):
        print(f"  {i:2d}| {line}")
    print()


actions = [
    ("BASELINE",   None),
    ("TURN_RIGHT", "l"),
    ("LOOK_UP",    "i"),
    ("FORWARD",    "w"),
    ("FORWARD",    "w"),
    ("FORWARD",    "w"),
    ("TURN_LEFT",  "j"),
    ("TURN_LEFT",  "j"),
    ("TURN_LEFT",  "j"),
    ("FORWARD",    "w"),
]

for step, (label, key) in enumerate(actions):
    if key == 'l':
        camera.yaw += TURN_SPEED
    elif key == 'j':
        camera.yaw -= TURN_SPEED
    elif key == 'i':
        camera.pitch = max(-1.2, camera.pitch - PITCH_SPEED)
    elif key == 'k':
        camera.pitch = min(1.2, camera.pitch + PITCH_SPEED)
    elif key == 'w':
        camera.pos.x += math.sin(camera.yaw) * MOVE_SPEED
        camera.pos.z += math.cos(camera.yaw) * MOVE_SPEED
    elif key == 's':
        camera.pos.x -= math.sin(camera.yaw) * MOVE_SPEED
        camera.pos.z -= math.cos(camera.yaw) * MOVE_SPEED

    camera.pos.x = max(-2.3, min(2.3, camera.pos.x))
    camera.pos.z = max(-0.5, min(3.5, camera.pos.z))

    buf, colors, ascii_prev, tile_grid = render_frame_enhanced(camera, bedroom, light, step)
    ground(label, step, camera, tile_grid, ascii_prev)
