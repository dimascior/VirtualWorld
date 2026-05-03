# render_engine_v2/frame_pipeline.py
# Dedicated frame rendering module for high-performance terminal 3D
# Separates ray tracing from output generation for better throughput

import math
import time
from typing import List, Tuple, Optional, Callable
from collections import deque

class FramePipeline:
    """
    Dedicated frame rendering pipeline.
    
    Features:
    - Double buffering for smooth output
    - Resolution scaling based on frame time
    - Scanline-based rendering for interleaved input
    - Pre-computed lookup tables
    """
    
    def __init__(self, width: int = 60, height: int = 20):
        self.base_width = width
        self.base_height = height
        self.scale = 1.0
        self.min_scale = 0.85  # Keep resolution high for full terminal use
        self.target_fps = 25
        
        # Double buffer
        self.buffer_a: List[List[Tuple[int,int,int]]] = []
        self.buffer_b: List[List[Tuple[int,int,int]]] = []
        self.active_buffer = 'a'
        
        # Frame timing
        self.frame_times = deque(maxlen=10)
        self.last_frame_start = 0.0
        
        # Pre-compute gamma table
        self.gamma = 2.2
        self.gamma_table = [int(pow(i/255.0, 1.0/self.gamma) * 255) for i in range(256)]
        
        # Scanline state for progressive rendering
        self.current_scanline = 0
        self.scanlines_per_batch = 4
        
        # Stats
        self.total_rays = 0
        self.frames_rendered = 0
    
    @property
    def effective_width(self) -> int:
        return max(20, int(self.base_width * self.scale))
    
    @property
    def effective_height(self) -> int:
        return max(8, int(self.base_height * self.scale))
    
    @property
    def current_fps(self) -> float:
        if not self.frame_times:
            return 0.0
        avg = sum(self.frame_times) / len(self.frame_times)
        return 1.0 / avg if avg > 0 else 0.0
    
    def get_buffer(self) -> List[List[Tuple[int,int,int]]]:
        """Get the current back buffer for writing."""
        return self.buffer_a if self.active_buffer == 'a' else self.buffer_b
    
    def swap_buffers(self):
        """Swap front and back buffers."""
        self.active_buffer = 'b' if self.active_buffer == 'a' else 'a'
    
    def begin_frame(self):
        """Start a new frame."""
        self.last_frame_start = time.time()
        self.current_scanline = 0
        
        # Ensure buffer size
        w, h = self.effective_width, self.effective_height * 2  # *2 for subpixels
        buffer = self.get_buffer()
        
        if len(buffer) != h or (buffer and len(buffer[0]) != w):
            # Resize buffer
            if self.active_buffer == 'a':
                self.buffer_a = [[(20,20,30)] * w for _ in range(h)]
            else:
                self.buffer_b = [[(20,20,30)] * w for _ in range(h)]
    
    def end_frame(self) -> float:
        """End frame and update timing."""
        frame_time = time.time() - self.last_frame_start
        self.frame_times.append(frame_time)
        self.frames_rendered += 1
        
        # Dynamic scaling
        self._adjust_scale(frame_time)
        
        return frame_time
    
    def _adjust_scale(self, frame_time: float):
        """Adjust resolution scale based on performance (DISABLED)."""
        # DISABLED - always maintain full resolution
        # User can enable if needed by setting min_scale < 1.0
        if self.min_scale >= 1.0:
            self.scale = 1.0
            return
            
        target_time = 1.0 / self.target_fps
        
        # Very conservative scaling - only reduce in extreme cases
        if frame_time > target_time * 2.0 and self.scale > self.min_scale:
            self.scale = max(self.min_scale, self.scale - 0.02)
        elif frame_time < target_time * 0.5 and self.scale < 1.0:
            self.scale = min(1.0, self.scale + 0.01)
    
    def gamma_correct(self, color: Tuple[int,int,int]) -> Tuple[int,int,int]:
        """Apply gamma correction."""
        return (
            self.gamma_table[max(0, min(255, color[0]))],
            self.gamma_table[max(0, min(255, color[1]))],
            self.gamma_table[max(0, min(255, color[2]))]
        )
    
    def render_scanlines(self, trace_func: Callable, num_lines: int = -1) -> bool:
        """
        Render a batch of scanlines.
        trace_func(x, y, width, height) -> (r, g, b)
        Returns True when frame is complete.
        """
        if num_lines < 0:
            num_lines = self.scanlines_per_batch
        
        buffer = self.get_buffer()
        w = self.effective_width
        h = self.effective_height * 2  # Subpixel height
        
        end_line = min(self.current_scanline + num_lines, h)
        
        for y in range(self.current_scanline, end_line):
            for x in range(w):
                color = trace_func(x, y, w, h)
                buffer[y][x] = color
                self.total_rays += 1
        
        self.current_scanline = end_line
        return self.current_scanline >= h
    
    def render_full(self, trace_func: Callable):
        """Render entire frame at once."""
        buffer = self.get_buffer()
        w = self.effective_width
        h = self.effective_height * 2
        
        for y in range(h):
            for x in range(w):
                buffer[y][x] = trace_func(x, y, w, h)
                self.total_rays += 1


class TerminalCompositor:
    """
    Converts pixel buffer to terminal output with QUADRANT blocks.
    Each character represents 2x2 pixels for maximum density.
    """
    
    # Quadrant block characters - 2x2 pixel patterns per character
    # Pattern: bit 0=top-left, bit 1=top-right, bit 2=bottom-left, bit 3=bottom-right
    QUADRANTS = [
        ' ',   # 0000 - empty
        '▘',   # 0001 - top-left
        '▝',   # 0010 - top-right
        '▀',   # 0011 - top
        '▖',   # 0100 - bottom-left
        '▌',   # 0101 - left
        '▞',   # 0110 - diagonal /
        '▛',   # 0111 - missing bottom-right
        '▗',   # 1000 - bottom-right
        '▚',   # 1001 - diagonal \
        '▐',   # 1010 - right
        '▜',   # 1011 - missing bottom-left
        '▄',   # 1100 - bottom
        '▙',   # 1101 - missing top-right
        '▟',   # 1110 - missing top-left
        '█',   # 1111 - full
    ]
    
    def __init__(self):
        self.merge_threshold = 25  # Luminance threshold for on/off
        self.use_quadrants = True  # Enable 2x2 pixel mode
    
    def luminance(self, c: Tuple[int,int,int]) -> float:
        """Perceptual luminance."""
        return 0.299 * c[0] + 0.587 * c[1] + 0.114 * c[2]
    
    def compose(self, buffer: List[List[Tuple[int,int,int]]], 
                output_width: int, output_height: int,
                gamma_table: List[int] = None) -> List[str]:
        """
        Convert pixel buffer to ANSI terminal lines using quadrant blocks.
        Buffer should have 2x width and 2x height for quadrant mode.
        Each output character = 2x2 input pixels.
        """
        lines = []
        
        if not buffer or not buffer[0]:
            return [' ' * output_width] * output_height
        
        buf_h = len(buffer)
        buf_w = len(buffer[0])
        
        # Each output char = 2x2 pixels from buffer
        for char_y in range(output_height):
            line_parts = []
            
            for char_x in range(output_width):
                # Get 2x2 pixel block
                px = char_x * 2
                py = char_y * 2
                
                # Sample 4 pixels (or edge clamp)
                def get_pixel(bx, by):
                    bx = min(max(0, bx), buf_w - 1)
                    by = min(max(0, by), buf_h - 1)
                    return buffer[by][bx]
                
                tl = get_pixel(px, py)       # top-left
                tr = get_pixel(px+1, py)     # top-right
                bl = get_pixel(px, py+1)     # bottom-left
                br = get_pixel(px+1, py+1)   # bottom-right
                
                # Apply gamma
                if gamma_table:
                    tl = (gamma_table[tl[0]], gamma_table[tl[1]], gamma_table[tl[2]])
                    tr = (gamma_table[tr[0]], gamma_table[tr[1]], gamma_table[tr[2]])
                    bl = (gamma_table[bl[0]], gamma_table[bl[1]], gamma_table[bl[2]])
                    br = (gamma_table[br[0]], gamma_table[br[1]], gamma_table[br[2]])
                
                # Get luminances
                lums = [self.luminance(tl), self.luminance(tr), 
                        self.luminance(bl), self.luminance(br)]
                
                # Find min/max for foreground/background
                min_lum = min(lums)
                max_lum = max(lums)
                mid_lum = (min_lum + max_lum) / 2
                
                # Build pattern - which pixels are "bright" (foreground)
                pattern = 0
                if lums[0] >= mid_lum: pattern |= 1  # top-left
                if lums[1] >= mid_lum: pattern |= 2  # top-right
                if lums[2] >= mid_lum: pattern |= 4  # bottom-left
                if lums[3] >= mid_lum: pattern |= 8  # bottom-right
                
                # Compute foreground (bright pixels avg) and background (dark pixels avg)
                fg_pixels = []
                bg_pixels = []
                colors = [tl, tr, bl, br]
                for i, c in enumerate(colors):
                    if lums[i] >= mid_lum:
                        fg_pixels.append(c)
                    else:
                        bg_pixels.append(c)
                
                if fg_pixels:
                    fg = tuple(sum(p[i] for p in fg_pixels) // len(fg_pixels) for i in range(3))
                else:
                    fg = (128, 128, 128)
                    
                if bg_pixels:
                    bg = tuple(sum(p[i] for p in bg_pixels) // len(bg_pixels) for i in range(3))
                else:
                    bg = fg  # All same brightness
                
                # Select character
                char = self.QUADRANTS[pattern]
                
                # Construct ANSI
                if pattern == 0:
                    # All background
                    ansi = f"\033[48;2;{bg[0]};{bg[1]};{bg[2]}m "
                elif pattern == 15:
                    # All foreground
                    ansi = f"\033[38;2;{fg[0]};{fg[1]};{fg[2]}m█"
                else:
                    # Mixed - need both fg and bg colors
                    ansi = f"\033[38;2;{fg[0]};{fg[1]};{fg[2]}m\033[48;2;{bg[0]};{bg[1]};{bg[2]}m{char}"
                
                line_parts.append(ansi)
            
            lines.append("".join(line_parts) + "\033[0m")
        
        return lines


class RayCache:
    """
    Cache for ray directions to avoid recomputation.
    """
    
    def __init__(self, max_width: int = 100, max_height: int = 80):
        self.max_width = max_width
        self.max_height = max_height
        self.cache = {}
        self.last_yaw = None
        self.last_pitch = None
        self.last_fov = None
    
    def invalidate(self):
        """Clear cache."""
        self.cache.clear()
    
    def get_or_compute(self, x: int, y: int, width: int, height: int,
                       yaw: float, pitch: float, fov: float,
                       compute_func: Callable) -> Tuple[float, float, float]:
        """Get cached ray direction or compute it."""
        # Check if camera changed
        if yaw != self.last_yaw or pitch != self.last_pitch or fov != self.last_fov:
            self.invalidate()
            self.last_yaw = yaw
            self.last_pitch = pitch
            self.last_fov = fov
        
        key = (x, y, width, height)
        if key not in self.cache:
            self.cache[key] = compute_func(x, y, width, height)
        
        return self.cache[key]
