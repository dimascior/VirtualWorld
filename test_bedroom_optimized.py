#!/usr/bin/env python
"""
Optimized Bedroom 3D Renderer with Modular Frame Pipeline
Based on test_bedroom_enhanced.py with dedicated rendering module
"""

import time, sys, math, random
import msvcrt  # Windows keyboard input

# Import the dedicated frame pipeline
sys.path.insert(0, '.')
from render_engine_v2.frame_pipeline import FramePipeline, TerminalCompositor

# === ANSI CODES ===
CLEAR = "\033[2J"
HOME = "\033[H"
HIDE_CURSOR = "\033[?25l"
SHOW_CURSOR = "\033[?25h"
RESET = "\033[0m"
BOLD = "\033[1m"

def goto(row, col):
    return f"\033[{row};{col}H"

def rgb(r, g, b):
    return f"\033[38;2;{int(max(0,min(255,r)))};{int(max(0,min(255,g)))};{int(max(0,min(255,b)))}m"

def bg_rgb(r, g, b):
    return f"\033[48;2;{int(max(0,min(255,r)))};{int(max(0,min(255,g)))};{int(max(0,min(255,b)))}m"

# === SETTINGS ===
WIDTH = 80
HEIGHT = 35
TARGET_FPS = 30

# Camera controls
TURN_SPEED = 0.15
PITCH_SPEED = 0.10
MOVE_SPEED = 0.25
MAX_PITCH = 1.2

# === VECTOR CLASS (Optimized) ===
class Vec3:
    __slots__ = ['x', 'y', 'z']
    
    def __init__(self, x=0, y=0, z=0):
        self.x, self.y, self.z = x, y, z
    
    def __add__(self, o): return Vec3(self.x + o.x, self.y + o.y, self.z + o.z)
    def __sub__(self, o): return Vec3(self.x - o.x, self.y - o.y, self.z - o.z)
    def __mul__(self, s): return Vec3(self.x * s, self.y * s, self.z * s)
    def dot(self, o): return self.x * o.x + self.y * o.y + self.z * o.z
    def length(self): return math.sqrt(self.x*self.x + self.y*self.y + self.z*self.z)
    def normalize(self):
        l = self.length()
        return Vec3(self.x/l, self.y/l, self.z/l) if l > 0.001 else Vec3()

# === CAMERA ===
class Camera:
    def __init__(self):
        self.pos = Vec3(0, 1.6, 0)
        self.yaw = 0
        self.pitch = 0
        self.fov = 90
        # Precomputed values
        self._cos_yaw = 1.0
        self._sin_yaw = 0.0
        self._cos_pitch = 1.0
        self._sin_pitch = 0.0
        self._fov_tan = math.tan(math.radians(45))
    
    def update_trig(self):
        """Precompute trig values."""
        self._cos_yaw = math.cos(self.yaw)
        self._sin_yaw = math.sin(self.yaw)
        self._cos_pitch = math.cos(self.pitch)
        self._sin_pitch = math.sin(self.pitch)
        self._fov_tan = math.tan(self.fov * math.pi / 360)
    
    def get_ray_dir_fast(self, x, y, width, height):
        """Optimized ray direction with isometric-accurate proportions."""
        # Normalized screen coords
        nx = (x - width * 0.5) / width * 2
        ny = (y - height * 0.5) / height * 2
        
        # Isometric aspect: uniform detail density regardless of pitch
        aspect = width / height * 2.2
        
        # Minimal pitch adjustment (8% max) to preserve geometric proportions
        pitch_factor = 1.0 + self._sin_pitch * 0.08
        
        dx = nx * self._fov_tan * aspect * pitch_factor
        dy = -ny * self._fov_tan * pitch_factor
        dz = 1
        
        # Rotate by yaw
        rx = dx * self._cos_yaw + dz * self._sin_yaw
        rz = -dx * self._sin_yaw + dz * self._cos_yaw
        
        # Rotate by pitch
        ry = dy * self._cos_pitch - rz * self._sin_pitch
        rz2 = dy * self._sin_pitch + rz * self._cos_pitch
        
        # Normalize
        l = math.sqrt(rx*rx + ry*ry + rz2*rz2)
        return Vec3(rx/l, ry/l, rz2/l)

# === MATERIAL (Simplified) ===
class Material:
    __slots__ = ['color', 'emissive']
    
    def __init__(self, color, emissive=0):
        self.color = color
        self.emissive = emissive

# Pre-defined materials
MAT_WALL = Material((180, 175, 165))
MAT_FLOOR = Material((120, 90, 60))
MAT_CEILING = Material((240, 240, 235))
MAT_BED_FRAME = Material((80, 50, 30))
MAT_BED_SHEET = Material((200, 200, 220))
MAT_PILLOW = Material((250, 250, 250))
MAT_DESK = Material((60, 40, 25))
MAT_MONITOR_SCREEN = Material((100, 180, 255), 0.9)
MAT_CHAIR = Material((40, 40, 45))
MAT_WINDOW = Material((180, 220, 255), 0.4)
MAT_CURTAIN = Material((150, 60, 60))
MAT_LAMP = Material((255, 240, 200), 1.0)
MAT_NIGHTSTAND = Material((70, 45, 25))
MAT_RUG = Material((100, 50, 50))
MAT_BOOKSHELF = Material((90, 60, 35))
MAT_DOOR = Material((100, 70, 40))
MAT_PLANT = Material((50, 120, 50))

# === BOX (Optimized intersection) ===
class Box:
    __slots__ = ['min_x', 'min_y', 'min_z', 'max_x', 'max_y', 'max_z', 'mat']
    
    def __init__(self, min_p, max_p, material):
        self.min_x, self.min_y, self.min_z = min_p.x, min_p.y, min_p.z
        self.max_x, self.max_y, self.max_z = max_p.x, max_p.y, max_p.z
        self.mat = material
    
    def intersect(self, ox, oy, oz, dx, dy, dz):
        """Optimized slab intersection with unrolled loop."""
        tmin = 0.001
        tmax = 1e9
        
        # X
        if abs(dx) > 1e-8:
            inv = 1.0 / dx
            t1 = (self.min_x - ox) * inv
            t2 = (self.max_x - ox) * inv
            if t1 > t2: t1, t2 = t2, t1
            tmin = max(tmin, t1)
            tmax = min(tmax, t2)
            if tmin > tmax: return None
        elif ox < self.min_x or ox > self.max_x:
            return None
        
        # Y
        if abs(dy) > 1e-8:
            inv = 1.0 / dy
            t1 = (self.min_y - oy) * inv
            t2 = (self.max_y - oy) * inv
            if t1 > t2: t1, t2 = t2, t1
            tmin = max(tmin, t1)
            tmax = min(tmax, t2)
            if tmin > tmax: return None
        elif oy < self.min_y or oy > self.max_y:
            return None
        
        # Z
        if abs(dz) > 1e-8:
            inv = 1.0 / dz
            t1 = (self.min_z - oz) * inv
            t2 = (self.max_z - oz) * inv
            if t1 > t2: t1, t2 = t2, t1
            tmin = max(tmin, t1)
            tmax = min(tmax, t2)
            if tmin > tmax: return None
        elif oz < self.min_z or oz > self.max_z:
            return None
        
        # Hit point and normal
        hx = ox + dx * tmin
        hy = oy + dy * tmin
        hz = oz + dz * tmin
        
        eps = 0.002
        if abs(hx - self.min_x) < eps: nx, ny, nz = -1, 0, 0
        elif abs(hx - self.max_x) < eps: nx, ny, nz = 1, 0, 0
        elif abs(hy - self.min_y) < eps: nx, ny, nz = 0, -1, 0
        elif abs(hy - self.max_y) < eps: nx, ny, nz = 0, 1, 0
        elif abs(hz - self.min_z) < eps: nx, ny, nz = 0, 0, -1
        else: nx, ny, nz = 0, 0, 1
        
        return (tmin, hx, hy, hz, nx, ny, nz, self.mat)

# === SCENE ===
def create_bedroom():
    objects = []
    room_w, room_h, room_d = 5, 3, 4
    
    # Room shell
    objects.append(Box(Vec3(-room_w/2, -0.1, -1), Vec3(room_w/2, 0, room_d), MAT_FLOOR))
    objects.append(Box(Vec3(-room_w/2, room_h, -1), Vec3(room_w/2, room_h+0.1, room_d), MAT_CEILING))
    objects.append(Box(Vec3(-room_w/2, 0, room_d), Vec3(room_w/2, room_h, room_d+0.1), MAT_WALL))
    objects.append(Box(Vec3(-room_w/2-0.1, 0, -1), Vec3(-room_w/2, room_h, room_d), MAT_WALL))
    objects.append(Box(Vec3(room_w/2, 0, -1), Vec3(room_w/2+0.1, room_h, room_d), MAT_WALL))
    objects.append(Box(Vec3(-room_w/2, 0, -1.1), Vec3(-0.5, room_h, -1), MAT_WALL))
    objects.append(Box(Vec3(0.5, 0, -1.1), Vec3(room_w/2, room_h, -1), MAT_WALL))
    objects.append(Box(Vec3(-0.5, 2.2, -1.1), Vec3(0.5, room_h, -1), MAT_WALL))
    objects.append(Box(Vec3(-0.5, 0, -1.05), Vec3(0.5, 2.2, -1), MAT_DOOR))
    
    # Bed
    bed_x = 1.5
    objects.append(Box(Vec3(bed_x-0.5, 0, 2), Vec3(bed_x+1, 0.4, 3.8), MAT_BED_FRAME))
    objects.append(Box(Vec3(bed_x-0.45, 0.4, 2.05), Vec3(bed_x+0.95, 0.55, 3.75), MAT_BED_SHEET))
    objects.append(Box(Vec3(bed_x-0.3, 0.55, 3.3), Vec3(bed_x+0.8, 0.7, 3.7), MAT_PILLOW))
    
    # Nightstand + Lamp
    objects.append(Box(Vec3(bed_x-1, 0, 3.2), Vec3(bed_x-0.6, 0.5, 3.7), MAT_NIGHTSTAND))
    objects.append(Box(Vec3(bed_x-0.9, 0.5, 3.35), Vec3(bed_x-0.7, 0.75, 3.55), MAT_LAMP))
    
    # Desk
    desk_x = -1.8
    objects.append(Box(Vec3(desk_x-0.6, 0.7, 2.5), Vec3(desk_x+0.6, 0.75, 3.5), MAT_DESK))
    objects.append(Box(Vec3(desk_x-0.5, 0, 2.6), Vec3(desk_x-0.4, 0.7, 2.7), MAT_DESK))
    objects.append(Box(Vec3(desk_x+0.4, 0, 2.6), Vec3(desk_x+0.5, 0.7, 2.7), MAT_DESK))
    objects.append(Box(Vec3(desk_x-0.5, 0, 3.3), Vec3(desk_x-0.4, 0.7, 3.4), MAT_DESK))
    objects.append(Box(Vec3(desk_x+0.4, 0, 3.3), Vec3(desk_x+0.5, 0.7, 3.4), MAT_DESK))
    
    # Monitor
    objects.append(Box(Vec3(desk_x-0.3, 0.8, 3.05), Vec3(desk_x+0.3, 1.15, 3.1), MAT_MONITOR_SCREEN))
    
    # Chair
    objects.append(Box(Vec3(desk_x-0.25, 0.4, 2.0), Vec3(desk_x+0.25, 0.45, 2.5), MAT_CHAIR))
    
    # Window + Curtains
    objects.append(Box(Vec3(-0.6, 1.0, 3.95), Vec3(0.6, 2.2, 4.0), MAT_WINDOW))
    objects.append(Box(Vec3(-1.0, 0.8, 3.9), Vec3(-0.6, 2.4, 3.95), MAT_CURTAIN))
    objects.append(Box(Vec3(0.6, 0.8, 3.9), Vec3(1.0, 2.4, 3.95), MAT_CURTAIN))
    
    # Bookshelf
    shelf_x = -2.4
    objects.append(Box(Vec3(shelf_x-0.15, 0, 0.5), Vec3(shelf_x+0.15, 1.8, 1.5), MAT_BOOKSHELF))
    
    # Rug
    objects.append(Box(Vec3(-0.8, 0.01, 1.0), Vec3(0.8, 0.02, 2.5), MAT_RUG))
    
    # Plant
    objects.append(Box(Vec3(2.0, 0, 0.5), Vec3(2.3, 0.3, 0.8), MAT_BOOKSHELF))
    objects.append(Box(Vec3(2.05, 0.3, 0.55), Vec3(2.25, 0.8, 0.75), MAT_PLANT))
    
    return objects

# === LIGHTING ===
LIGHT_POS = Vec3(0, 2.7, 2)

def trace_ray(ox, oy, oz, dx, dy, dz, objects):
    """Trace ray and return color."""
    closest = None
    
    for obj in objects:
        hit = obj.intersect(ox, oy, oz, dx, dy, dz)
        if hit:
            if closest is None or hit[0] < closest[0]:
                closest = hit
    
    if closest is None:
        return (35, 35, 50)  # Background
    
    t, hx, hy, hz, nx, ny, nz, mat = closest
    
    # Emissive materials
    if mat.emissive > 0:
        e = mat.emissive
        return (
            min(255, int(mat.color[0] * (0.7 + e * 0.5))),
            min(255, int(mat.color[1] * (0.7 + e * 0.5))),
            min(255, int(mat.color[2] * (0.7 + e * 0.5)))
        )
    
    # Lighting
    lx = LIGHT_POS.x - hx
    ly = LIGHT_POS.y - hy
    lz = LIGHT_POS.z - hz
    ld = math.sqrt(lx*lx + ly*ly + lz*lz)
    lx, ly, lz = lx/ld, ly/ld, lz/ld
    
    ndotl = max(0, nx*lx + ny*ly + nz*lz)
    
    # Simple shadow (skip for speed)
    ambient = 0.35
    diffuse = ndotl * 0.65
    atten = 1.0 / (1 + ld * 0.08)
    brightness = ambient + diffuse * atten
    
    return (
        int(mat.color[0] * brightness),
        int(mat.color[1] * brightness),
        int(mat.color[2] * brightness)
    )

# === INPUT ===
def get_keyboard_input():
    keys = []
    while msvcrt.kbhit():
        ch = msvcrt.getch()
        if ch == b'\xe0':
            ch2 = msvcrt.getch()
            if ch2 == b'I': keys.append('UP')
            elif ch2 == b'K': keys.append('DOWN')
            elif ch2 == b'J': keys.append('LEFT')
            elif ch2 == b'L': keys.append('RIGHT')
        elif ch == b'\x00':
            msvcrt.getch()
        else:
            try:
                keys.append(ch.decode('utf-8').lower())
            except:
                pass
    return keys

def process_input(keys, camera, mode):
    for key in keys:
        if key == 'q':
            return None, mode
        if key == ' ':
            mode = 'auto' if mode == 'manual' else 'manual'
        
        # Look
        if key in ('LEFT', 'j'): camera.yaw -= TURN_SPEED
        if key in ('RIGHT', 'l'): camera.yaw += TURN_SPEED
        if key in ('UP', 'i'): camera.pitch = max(-MAX_PITCH, camera.pitch - PITCH_SPEED)
        if key in ('DOWN', 'k'): camera.pitch = min(MAX_PITCH, camera.pitch + PITCH_SPEED)
        
        # Move
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
        
        # Clamp
        camera.pos.x = max(-2.3, min(2.3, camera.pos.x))
        camera.pos.z = max(-0.5, min(3.5, camera.pos.z))
    
    camera.update_trig()
    return camera, mode

# === MAIN ===
def main():
    sys.stdout.write(HIDE_CURSOR + CLEAR)
    sys.stdout.flush()
    
    # Initialize
    bedroom = create_bedroom()
    camera = Camera()
    camera.pos = Vec3(0, 1.6, 0.5)
    camera.update_trig()
    
    # Frame pipeline
    pipeline = FramePipeline(WIDTH, HEIGHT)
    pipeline.target_fps = TARGET_FPS
    pipeline.min_scale = 1.0  # DISABLE dynamic scaling - always full resolution
    pipeline.scale = 1.0      # Force 100% scale
    
    compositor = TerminalCompositor()
    compositor.merge_threshold = 18
    
    mode = 'manual'  # Start in manual mode - no auto rotation
    frame = 0
    
    try:
        while True:
            frame_start = time.time()
            
            # Input
            keys = get_keyboard_input()
            result, mode = process_input(keys, camera, mode)
            if result is None:
                break
            camera = result
            
            # Auto mode
            if mode == 'auto':
                camera.yaw = math.sin(frame * 0.035) * 0.9
                camera.pitch = math.sin(frame * 0.02) * 0.2
                camera.update_trig()
            
            # Start frame
            pipeline.begin_frame()
            
            # Get effective dimensions
            w = pipeline.effective_width
            h = pipeline.effective_height * 2
            
            # Create trace function
            ox, oy, oz = camera.pos.x, camera.pos.y, camera.pos.z
            
            def trace_pixel(x, y, width, height):
                rd = camera.get_ray_dir_fast(x, y, width, height)
                return trace_ray(ox, oy, oz, rd.x, rd.y, rd.z, bedroom)
            
            # Render
            pipeline.render_full(trace_pixel)
            
            # End frame
            frame_time = pipeline.end_frame()
            
            # Compose output - ALWAYS use full WIDTH/HEIGHT, not scaled
            buffer = pipeline.get_buffer()
            lines = compositor.compose(buffer, WIDTH, HEIGHT, pipeline.gamma_table)
            
            # Output
            sys.stdout.write(HOME)
            
            # Title
            title = " ═══ BEDROOM 3D PRO ═══ "
            tx = (WIDTH - len(title)) // 2
            sys.stdout.write(goto(1, tx) + rgb(200, 180, 255) + BOLD + title + RESET)
            
            # Frame
            for i, line in enumerate(lines):
                sys.stdout.write(goto(2 + i, 1) + line)
            
            # Status
            fps = pipeline.current_fps
            res = int(pipeline.scale * 100)
            status = f"FPS:{fps:4.1f} | {w}x{h//2} ({res}%) | {mode.upper()} | WASD:Move Arrows:Look Q:Quit"
            sys.stdout.write(goto(HEIGHT + 3, 1) + rgb(120, 180, 120) + status + RESET)
            
            sys.stdout.flush()
            frame += 1
            
            # Frame limit
            elapsed = time.time() - frame_start
            sleep_time = max(0.001, (1.0 / TARGET_FPS) - elapsed)
            time.sleep(sleep_time)
    
    except KeyboardInterrupt:
        pass
    finally:
        sys.stdout.write(SHOW_CURSOR + RESET)
        sys.stdout.write(goto(HEIGHT + 5, 1))
        print(f"\n✓ Session ended. {frame} frames rendered.")
        print(f"  Average FPS: {pipeline.current_fps:.1f}")
        print(f"  Total rays traced: {pipeline.total_rays:,}")

if __name__ == "__main__":
    main()
