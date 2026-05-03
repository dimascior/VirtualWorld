#!/usr/bin/env python
"""
Multi-Environment 3D Terminal Renderer
Switch between different 3D scenes with number keys

Controls:
    WASD        - Move camera
    Arrow Keys  - Look around
    1-6         - Switch environments
    SPACE       - Toggle auto/manual mode
    Q           - Quit
"""

import time, sys, math
import msvcrt  # Windows keyboard input

sys.path.insert(0, '.')
from render_engine_v2.environments import (
    Environment, get_environment, list_environments, MATERIALS
)
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
WIDTH = 80       # Terminal characters wide
HEIGHT = 40      # Terminal characters tall
TARGET_FPS = 15  # Target FPS

# Internal render resolution: 2x2 pixels per character (quadrant blocks)
RENDER_WIDTH = WIDTH * 2    # 160 pixels wide
RENDER_HEIGHT = HEIGHT * 2  # 80 pixels tall

# Camera controls
TURN_SPEED = 0.15
PITCH_SPEED = 0.10
MOVE_SPEED = 0.25
MAX_PITCH = 1.3


class Camera:
    """First-person camera."""
    __slots__ = ['x', 'y', 'z', 'yaw', 'pitch', 'fov',
                 '_cos_yaw', '_sin_yaw', '_cos_pitch', '_sin_pitch', '_fov_tan']
    
    def __init__(self, x=0, y=1.6, z=0):
        self.x, self.y, self.z = x, y, z
        self.yaw = 0.0
        self.pitch = 0.0
        self.fov = 90
        self.update_trig()
    
    def update_trig(self):
        self._cos_yaw = math.cos(self.yaw)
        self._sin_yaw = math.sin(self.yaw)
        self._cos_pitch = math.cos(self.pitch)
        self._sin_pitch = math.sin(self.pitch)
        self._fov_tan = math.tan(self.fov * math.pi / 360)
    
    def get_ray(self, px, py, width, height):
        """Get ray direction for screen pixel."""
        nx = (px - width * 0.5) / width * 2
        ny = (py - height * 0.5) / height * 2
        
        aspect = width / height * 2.2
        
        dx = nx * self._fov_tan * aspect
        dy = -ny * self._fov_tan
        dz = 1
        
        # Rotate by yaw
        rx = dx * self._cos_yaw + dz * self._sin_yaw
        rz = -dx * self._sin_yaw + dz * self._cos_yaw
        
        # Rotate by pitch
        ry = dy * self._cos_pitch - rz * self._sin_pitch
        rz2 = dy * self._sin_pitch + rz * self._cos_pitch
        
        # Normalize
        length = math.sqrt(rx*rx + ry*ry + rz2*rz2)
        return rx/length, ry/length, rz2/length
    
    def move(self, forward, strafe):
        """Move camera relative to facing direction."""
        self.x += self._sin_yaw * forward + self._cos_yaw * strafe
        self.z += self._cos_yaw * forward - self._sin_yaw * strafe
    
    def clamp(self, bounds):
        """Clamp position to bounds."""
        min_b, max_b = bounds
        self.x = max(min_b[0], min(max_b[0], self.x))
        self.y = max(min_b[1], min(max_b[1], self.y))
        self.z = max(min_b[2], min(max_b[2], self.z))


def get_input():
    """Non-blocking keyboard input."""
    keys = []
    while msvcrt.kbhit():
        ch = msvcrt.getch()
        if ch == b'\xe0':
            ch2 = msvcrt.getch()
            if ch2 == b'H': keys.append('UP')
            elif ch2 == b'P': keys.append('DOWN')
            elif ch2 == b'K': keys.append('LEFT')
            elif ch2 == b'M': keys.append('RIGHT')
        else:
            try:
                keys.append(ch.decode('utf-8').lower())
            except:
                pass
    return keys


def main():
    sys.stdout.write(HIDE_CURSOR + CLEAR)
    sys.stdout.flush()
    
    # Available environments
    env_names = list_environments()
    current_env_idx = 0
    
    # Load initial environment
    env = get_environment(env_names[current_env_idx])
    
    # Camera
    camera = Camera(*env.spawn_pos)
    camera.yaw = env.spawn_yaw
    camera.update_trig()
    
    # Frame pipeline - render at 2x resolution for quadrant blocks
    pipeline = FramePipeline(RENDER_WIDTH, RENDER_HEIGHT // 2)  # /2 because pipeline doubles height
    pipeline.target_fps = TARGET_FPS
    pipeline.min_scale = 1.0  # No dynamic scaling
    pipeline.scale = 1.0
    
    compositor = TerminalCompositor()
    compositor.merge_threshold = 25
    
    mode = 'manual'
    frame = 0
    env_changed = True  # Flag to redraw environment info
    
    try:
        while True:
            frame_start = time.time()
            
            # Input
            keys = get_input()
            
            # Quit
            if 'q' in keys:
                break
            
            # Environment switching (1-6)
            for i, key in enumerate(['1', '2', '3', '4', '5', '6']):
                if key in keys and i < len(env_names):
                    if i != current_env_idx:
                        current_env_idx = i
                        env = get_environment(env_names[current_env_idx])
                        camera = Camera(*env.spawn_pos)
                        camera.yaw = env.spawn_yaw
                        camera.update_trig()
                        env_changed = True
            
            # Mode toggle
            if ' ' in keys:
                mode = 'auto' if mode == 'manual' else 'manual'
            
            # Movement
            if mode == 'manual':
                forward = 0
                strafe = 0
                
                if 'w' in keys: forward += MOVE_SPEED
                if 's' in keys: forward -= MOVE_SPEED
                if 'a' in keys: strafe -= MOVE_SPEED
                if 'd' in keys: strafe += MOVE_SPEED
                
                # Look controls - Arrow keys AND IJKL
                if 'LEFT' in keys or 'j' in keys: camera.yaw -= TURN_SPEED
                if 'RIGHT' in keys or 'l' in keys: camera.yaw += TURN_SPEED
                if 'UP' in keys or 'i' in keys: camera.pitch = max(-MAX_PITCH, camera.pitch - PITCH_SPEED)
                if 'DOWN' in keys or 'k' in keys: camera.pitch = min(MAX_PITCH, camera.pitch + PITCH_SPEED)
                
                camera.move(forward, strafe)
                camera.clamp(env.bounds)
                camera.update_trig()
            else:
                # Auto mode - gentle rotation
                camera.yaw = math.sin(frame * 0.03) * 0.8
                camera.pitch = math.sin(frame * 0.02) * 0.25
                camera.update_trig()
            
            # Begin frame
            pipeline.begin_frame()
            
            # Render at full internal resolution (2x terminal size for quadrants)
            w = RENDER_WIDTH
            h = RENDER_HEIGHT
            
            # Trace function - renders at 2x2 per terminal character
            cx, cy, cz = camera.x, camera.y, camera.z
            
            def trace_pixel(px, py, width, height):
                dx, dy, dz = camera.get_ray(px, py, width, height)
                return env.trace(cx, cy, cz, dx, dy, dz)
            
            # Render at high resolution
            pipeline.render_full(trace_pixel)
            pipeline.end_frame()
            
            # Compose using quadrant blocks (200x100 pixels -> 100x50 characters)
            buffer = pipeline.get_buffer()
            lines = compositor.compose(buffer, WIDTH, HEIGHT, pipeline.gamma_table)
            
            # Output
            sys.stdout.write(HOME)
            
            # Title bar
            title = f" ═══ {env.name.upper()} ═══ "
            tx = (WIDTH - len(title)) // 2
            sys.stdout.write(goto(1, tx) + rgb(200, 200, 255) + BOLD + title + RESET)
            
            # Environment selector
            selector = " "
            for i, name in enumerate(env_names):
                if i == current_env_idx:
                    selector += f"{rgb(100,255,100)}[{i+1}:{name}]{RESET} "
                else:
                    selector += f"{rgb(100,100,100)}{i+1}:{name}{RESET} "
            sys.stdout.write(goto(2, 1) + selector)
            
            # Frame
            for i, line in enumerate(lines):
                sys.stdout.write(goto(3 + i, 1) + line)
            
            # Status bar
            fps = pipeline.current_fps
            status = f" FPS:{fps:4.1f} | {RENDER_WIDTH}x{RENDER_HEIGHT}px | {mode.upper()} | WASD:Move IJKL:Look 1-6:Env Q:Quit "
            sys.stdout.write(goto(HEIGHT + 4, 1) + bg_rgb(40, 40, 50) + rgb(200, 200, 200) + status + RESET)
            
            sys.stdout.flush()
            frame += 1
            
            # Frame limiting
            elapsed = time.time() - frame_start
            sleep_time = max(0.001, (1.0 / TARGET_FPS) - elapsed)
            time.sleep(sleep_time)
    
    except KeyboardInterrupt:
        pass
    finally:
        sys.stdout.write(SHOW_CURSOR + RESET)
        sys.stdout.write(goto(HEIGHT + 6, 1))
        print(f"\n✓ Session ended. {frame} frames rendered.")
        print(f"  Environment: {env.name}")
        print(f"  Average FPS: {pipeline.current_fps:.1f}")


if __name__ == "__main__":
    print("╔════════════════════════════════════════════════════════════════╗")
    print("║          MULTI-ENVIRONMENT 3D TERMINAL RENDERER                ║")
    print("║                                                                ║")
    print("║  Environments:                                                 ║")
    print("║    1: Bedroom    - Cozy room with bed and glowing monitor      ║")
    print("║    2: Office     - Modern office with cubicles                 ║")
    print("║    3: Corridor   - Sci-fi spaceship corridor                   ║")
    print("║    4: Park       - Outdoor park with trees and pond            ║")
    print("║    5: Dungeon    - Medieval dungeon with torches               ║")
    print("║    6: Abstract   - Floating shapes in void                     ║")
    print("║                                                                ║")
    print("║  Controls:                                                     ║")
    print("║    WASD - Move    IJKL/Arrows - Look    1-6 - Switch   Q - Quit ║")
    print("╚════════════════════════════════════════════════════════════════╝")
    print("\nStarting in 2 seconds...")
    time.sleep(2)
    main()
