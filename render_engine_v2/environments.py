# render_engine_v2/environments.py
# Modular environment/scene definitions
# Each environment is a self-contained 3D scene with objects and materials

from typing import List, Tuple, Dict, Callable

# === MATERIAL DEFINITIONS ===
MATERIALS = {
    # Basic
    'white': (255, 255, 255),
    'black': (20, 20, 20),
    'gray': (128, 128, 128),
    'red': (200, 50, 50),
    'green': (50, 200, 50),
    'blue': (50, 50, 200),
    
    # Architectural
    'wall': (200, 190, 180),
    'floor_wood': (120, 80, 50),
    'floor_tile': (180, 180, 190),
    'ceiling': (240, 240, 235),
    'concrete': (160, 160, 155),
    'brick': (180, 100, 80),
    
    # Furniture
    'wood_dark': (60, 40, 25),
    'wood_light': (180, 140, 100),
    'fabric_red': (180, 50, 50),
    'fabric_blue': (50, 80, 180),
    'leather': (80, 60, 50),
    'metal': (180, 185, 190),
    'chrome': (220, 225, 230),
    
    # Nature
    'grass': (80, 160, 60),
    'dirt': (120, 90, 60),
    'water': (60, 120, 180),
    'sand': (220, 200, 160),
    'rock': (130, 125, 120),
    'tree_bark': (90, 70, 50),
    'leaves': (60, 140, 50),
    
    # Sci-Fi
    'glow_blue': (100, 200, 255),
    'glow_green': (100, 255, 150),
    'glow_orange': (255, 180, 80),
    'glow_purple': (180, 100, 255),
    'glow_red': (255, 100, 100),
    'panel_dark': (40, 45, 50),
    'panel_light': (70, 75, 85),
    'hologram': (150, 220, 255),
    
    # Sky
    'sky_day': (135, 180, 230),
    'sky_sunset': (255, 150, 100),
    'sky_night': (20, 25, 40),
    'void': (15, 15, 20),
}


class Box:
    """Axis-aligned box primitive."""
    __slots__ = ['min_x', 'min_y', 'min_z', 'max_x', 'max_y', 'max_z', 'color', 'emissive']
    
    def __init__(self, min_pos: Tuple[float,float,float], max_pos: Tuple[float,float,float], 
                 color: Tuple[int,int,int], emissive: float = 0.0):
        self.min_x, self.min_y, self.min_z = min_pos
        self.max_x, self.max_y, self.max_z = max_pos
        self.color = color
        self.emissive = emissive
    
    def intersect(self, ox, oy, oz, dx, dy, dz):
        """Ray-box intersection. Returns (t, nx, ny, nz) or None."""
        t_min = 0.001
        t_max = 1000.0
        
        # X slab
        if abs(dx) > 1e-9:
            inv = 1.0 / dx
            t0 = (self.min_x - ox) * inv
            t1 = (self.max_x - ox) * inv
            if t0 > t1: t0, t1 = t1, t0
            t_min = max(t_min, t0)
            t_max = min(t_max, t1)
            if t_min > t_max: return None
        elif ox < self.min_x or ox > self.max_x:
            return None
        
        # Y slab
        if abs(dy) > 1e-9:
            inv = 1.0 / dy
            t0 = (self.min_y - oy) * inv
            t1 = (self.max_y - oy) * inv
            if t0 > t1: t0, t1 = t1, t0
            t_min = max(t_min, t0)
            t_max = min(t_max, t1)
            if t_min > t_max: return None
        elif oy < self.min_y or oy > self.max_y:
            return None
        
        # Z slab
        if abs(dz) > 1e-9:
            inv = 1.0 / dz
            t0 = (self.min_z - oz) * inv
            t1 = (self.max_z - oz) * inv
            if t0 > t1: t0, t1 = t1, t0
            t_min = max(t_min, t0)
            t_max = min(t_max, t1)
            if t_min > t_max: return None
        elif oz < self.min_z or oz > self.max_z:
            return None
        
        # Calculate normal
        hx = ox + dx * t_min
        hy = oy + dy * t_min
        hz = oz + dz * t_min
        
        eps = 0.001
        nx, ny, nz = 0, 0, 0
        if abs(hx - self.min_x) < eps: nx = -1
        elif abs(hx - self.max_x) < eps: nx = 1
        elif abs(hy - self.min_y) < eps: ny = -1
        elif abs(hy - self.max_y) < eps: ny = 1
        elif abs(hz - self.min_z) < eps: nz = -1
        else: nz = 1
        
        return (t_min, nx, ny, nz)


class Sphere:
    """Sphere primitive."""
    __slots__ = ['cx', 'cy', 'cz', 'radius', 'radius_sq', 'color', 'emissive']
    
    def __init__(self, center: Tuple[float,float,float], radius: float,
                 color: Tuple[int,int,int], emissive: float = 0.0):
        self.cx, self.cy, self.cz = center
        self.radius = radius
        self.radius_sq = radius * radius
        self.color = color
        self.emissive = emissive
    
    def intersect(self, ox, oy, oz, dx, dy, dz):
        """Ray-sphere intersection."""
        ocx = ox - self.cx
        ocy = oy - self.cy
        ocz = oz - self.cz
        
        a = dx*dx + dy*dy + dz*dz
        b = 2 * (ocx*dx + ocy*dy + ocz*dz)
        c = ocx*ocx + ocy*ocy + ocz*ocz - self.radius_sq
        
        disc = b*b - 4*a*c
        if disc < 0:
            return None
        
        import math
        sqrt_disc = math.sqrt(disc)
        t = (-b - sqrt_disc) / (2*a)
        if t < 0.001:
            t = (-b + sqrt_disc) / (2*a)
            if t < 0.001:
                return None
        
        # Calculate normal
        hx = ox + dx * t
        hy = oy + dy * t
        hz = oz + dz * t
        
        inv_r = 1.0 / self.radius
        nx = (hx - self.cx) * inv_r
        ny = (hy - self.cy) * inv_r
        nz = (hz - self.cz) * inv_r
        
        return (t, nx, ny, nz)


class Environment:
    """Base environment class."""
    
    def __init__(self, name: str):
        self.name = name
        self.objects: List = []
        self.lights: List[Tuple[float,float,float,float]] = []  # (x, y, z, intensity)
        self.ambient = 0.15
        self.sky_color = MATERIALS['void']
        self.spawn_pos = (0, 1.6, 0)
        self.spawn_yaw = 0.0
        self.bounds = ((-10, -1, -10), (10, 10, 10))  # min, max for camera clamping
    
    def add_box(self, min_pos, max_pos, material: str, emissive: float = 0.0):
        color = MATERIALS.get(material, (128, 128, 128))
        self.objects.append(Box(min_pos, max_pos, color, emissive))
        return self
    
    def add_sphere(self, center, radius, material: str, emissive: float = 0.0):
        color = MATERIALS.get(material, (128, 128, 128))
        self.objects.append(Sphere(center, radius, color, emissive))
        return self
    
    def add_light(self, x, y, z, intensity=1.0):
        self.lights.append((x, y, z, intensity))
        return self
    
    def trace(self, ox, oy, oz, dx, dy, dz) -> Tuple[int, int, int]:
        """Trace a ray through this environment."""
        closest_t = 1e9
        closest_obj = None
        closest_normal = (0, 1, 0)
        
        for obj in self.objects:
            hit = obj.intersect(ox, oy, oz, dx, dy, dz)
            if hit and hit[0] < closest_t:
                closest_t = hit[0]
                closest_obj = obj
                closest_normal = (hit[1], hit[2], hit[3])
        
        if not closest_obj:
            return self.sky_color
        
        # Emissive objects
        if closest_obj.emissive > 0:
            c = closest_obj.color
            boost = 1.0 + closest_obj.emissive * 0.5
            return (min(255, int(c[0]*boost)), min(255, int(c[1]*boost)), min(255, int(c[2]*boost)))
        
        # Hit point
        hx = ox + dx * closest_t
        hy = oy + dy * closest_t
        hz = oz + dz * closest_t
        nx, ny, nz = closest_normal
        
        # Lighting
        total_light = self.ambient
        
        for lx, ly, lz, intensity in self.lights:
            # Direction to light
            ldx = lx - hx
            ldy = ly - hy
            ldz = lz - hz
            dist_sq = ldx*ldx + ldy*ldy + ldz*ldz
            dist = dist_sq ** 0.5
            
            if dist < 0.001:
                continue
            
            ldx /= dist
            ldy /= dist
            ldz /= dist
            
            # Diffuse
            ndotl = nx*ldx + ny*ldy + nz*ldz
            if ndotl <= 0:
                continue
            
            # Shadow check
            shadow = 1.0
            shadow_ox = hx + nx * 0.01
            shadow_oy = hy + ny * 0.01
            shadow_oz = hz + nz * 0.01
            
            for obj in self.objects:
                if obj is closest_obj:
                    continue
                hit = obj.intersect(shadow_ox, shadow_oy, shadow_oz, ldx, ldy, ldz)
                if hit and hit[0] < dist:
                    shadow = 0.3
                    break
            
            # Attenuation
            atten = intensity / (1.0 + dist * 0.15)
            total_light += ndotl * shadow * atten
        
        total_light = min(1.0, total_light)
        c = closest_obj.color
        
        return (int(c[0] * total_light), int(c[1] * total_light), int(c[2] * total_light))


# ============================================================
# ENVIRONMENT DEFINITIONS
# ============================================================

def create_bedroom() -> Environment:
    """Cozy bedroom with bed, desk, and glowing monitor."""
    env = Environment("Bedroom")
    env.sky_color = MATERIALS['void']
    env.spawn_pos = (0, 1.6, 0.5)
    env.bounds = ((-2.3, 0.5, -0.5), (2.3, 2.5, 3.5))
    
    # Room shell
    env.add_box((-2.5, -0.1, -1), (2.5, 0, 4), 'floor_wood')
    env.add_box((-2.5, 3, -1), (2.5, 3.1, 4), 'ceiling')
    env.add_box((-2.6, 0, -1), (-2.5, 3, 4), 'wall')
    env.add_box((2.5, 0, -1), (2.6, 3, 4), 'wall')
    env.add_box((-2.5, 0, 3.9), (2.5, 3, 4), 'wall')
    env.add_box((-2.5, 0, -1.1), (2.5, 3, -1), 'wall')
    
    # Bed
    env.add_box((0.8, 0, 2), (2.4, 0.5, 3.8), 'fabric_red')
    env.add_box((0.7, 0, 1.9), (2.5, 0.15, 3.9), 'wood_dark')
    
    # Desk
    env.add_box((-2.4, 0, 2), (-1.2, 0.75, 3.5), 'wood_light')
    
    # Monitor (emissive)
    env.add_box((-2.3, 0.75, 2.5), (-2.2, 1.25, 3.2), 'glow_blue', emissive=1.0)
    
    # Chair
    env.add_box((-1.5, 0, 2.3), (-1.1, 0.45, 2.8), 'metal')
    
    # Floating cube
    env.add_box((-0.3, 1.2, 1.5), (0.2, 1.7, 2.0), 'glow_purple', emissive=0.8)
    
    # Light
    env.add_light(0, 2.8, 2, 1.5)
    
    return env


def create_office() -> Environment:
    """Modern office space with cubicles."""
    env = Environment("Office")
    env.sky_color = MATERIALS['void']
    env.spawn_pos = (0, 1.6, 0)
    env.bounds = ((-4, 0.5, -4), (4, 2.5, 4))
    
    # Floor and ceiling
    env.add_box((-5, -0.1, -5), (5, 0, 5), 'floor_tile')
    env.add_box((-5, 3, -5), (5, 3.1, 5), 'ceiling')
    
    # Walls
    env.add_box((-5.1, 0, -5), (-5, 3, 5), 'wall')
    env.add_box((5, 0, -5), (5.1, 3, 5), 'wall')
    env.add_box((-5, 0, -5.1), (5, 3, -5), 'wall')
    env.add_box((-5, 0, 5), (5, 3, 5.1), 'wall')
    
    # Cubicle partitions
    env.add_box((-3, 0, -2), (-2.9, 1.5, 2), 'panel_light')
    env.add_box((0, 0, -2), (0.1, 1.5, 2), 'panel_light')
    env.add_box((2.9, 0, -2), (3, 1.5, 2), 'panel_light')
    
    # Desks
    for x_off in [-2, 1]:
        env.add_box((x_off - 0.8, 0, -1.5), (x_off + 0.8, 0.72, -0.5), 'wood_light')
        env.add_box((x_off - 0.8, 0, 0.5), (x_off + 0.8, 0.72, 1.5), 'wood_light')
        # Monitors
        env.add_box((x_off - 0.3, 0.72, -1.3), (x_off + 0.3, 1.1, -1.2), 'glow_blue', emissive=0.8)
        env.add_box((x_off - 0.3, 0.72, 0.7), (x_off + 0.3, 1.1, 0.8), 'glow_blue', emissive=0.8)
    
    # Ceiling lights
    env.add_box((-2, 2.9, -1), (-1, 2.95, 1), 'glow_orange', emissive=0.6)
    env.add_box((1, 2.9, -1), (2, 2.95, 1), 'glow_orange', emissive=0.6)
    
    env.add_light(-1.5, 2.8, 0, 1.2)
    env.add_light(1.5, 2.8, 0, 1.2)
    
    return env


def create_sci_fi_corridor() -> Environment:
    """Futuristic spaceship corridor."""
    env = Environment("Sci-Fi Corridor")
    env.sky_color = MATERIALS['void']
    env.spawn_pos = (0, 1.6, 0)
    env.bounds = ((-1.8, 0.5, -10), (1.8, 2.5, 10))
    env.ambient = 0.1
    
    # Corridor structure
    env.add_box((-2, -0.1, -12), (2, 0, 12), 'panel_dark')
    env.add_box((-2, 3, -12), (2, 3.1, 12), 'panel_dark')
    env.add_box((-2.1, 0, -12), (-2, 3, 12), 'panel_dark')
    env.add_box((2, 0, -12), (2.1, 3, 12), 'panel_dark')
    
    # Floor lights
    for z in range(-10, 11, 4):
        env.add_box((-0.1, -0.05, z-0.5), (0.1, 0.01, z+0.5), 'glow_blue', emissive=1.0)
    
    # Wall lights
    for z in range(-10, 11, 3):
        env.add_box((-1.95, 1.5, z-0.2), (-1.9, 2.0, z+0.2), 'glow_orange', emissive=0.9)
        env.add_box((1.9, 1.5, z-0.2), (1.95, 2.0, z+0.2), 'glow_orange', emissive=0.9)
    
    # Wall panels
    for z in range(-9, 10, 3):
        env.add_box((-1.98, 0.5, z-1), (-1.92, 2.5, z+1), 'panel_light')
        env.add_box((1.92, 0.5, z-1), (1.98, 2.5, z+1), 'panel_light')
    
    # Doorway frames
    env.add_box((-1.5, 0, 8), (-1.3, 2.5, 8.2), 'chrome')
    env.add_box((1.3, 0, 8), (1.5, 2.5, 8.2), 'chrome')
    env.add_box((-1.5, 2.3, 8), (1.5, 2.5, 8.2), 'chrome')
    
    # Holographic display
    env.add_sphere((0, 1.5, 5), 0.4, 'hologram', emissive=0.7)
    
    env.add_light(0, 2.8, 0, 0.8)
    env.add_light(0, 2.8, 5, 0.8)
    env.add_light(0, 2.8, -5, 0.8)
    
    return env


def create_outdoor_park() -> Environment:
    """Simple outdoor park scene."""
    env = Environment("Park")
    env.sky_color = MATERIALS['sky_day']
    env.spawn_pos = (0, 1.6, 0)
    env.bounds = ((-15, 0.5, -15), (15, 5, 15))
    env.ambient = 0.35
    
    # Ground
    env.add_box((-20, -0.5, -20), (20, 0, 20), 'grass')
    
    # Path
    env.add_box((-1, 0, -15), (1, 0.02, 15), 'concrete')
    
    # Bench
    env.add_box((2, 0, -0.5), (4, 0.45, 0.5), 'wood_light')
    env.add_box((2.1, 0.45, 0.3), (3.9, 0.9, 0.5), 'wood_light')
    
    # Trees (simplified as boxes)
    for tx, tz in [(-5, 3), (6, -4), (-4, -6), (8, 5)]:
        # Trunk
        env.add_box((tx-0.2, 0, tz-0.2), (tx+0.2, 2, tz+0.2), 'tree_bark')
        # Foliage
        env.add_box((tx-1, 1.5, tz-1), (tx+1, 3.5, tz+1), 'leaves')
    
    # Pond
    env.add_box((-8, -0.2, 2), (-4, 0.01, 6), 'water')
    
    # Rocks
    env.add_sphere((-6.5, 0.3, 2.5), 0.4, 'rock')
    env.add_sphere((-4.5, 0.25, 5.5), 0.35, 'rock')
    
    # Sun (distant light)
    env.add_light(20, 30, 10, 2.0)
    
    return env


def create_dungeon() -> Environment:
    """Dark medieval dungeon."""
    env = Environment("Dungeon")
    env.sky_color = (10, 8, 5)
    env.spawn_pos = (0, 1.6, 0)
    env.bounds = ((-4, 0.5, -8), (4, 2.5, 8))
    env.ambient = 0.08
    
    # Stone floor and ceiling
    env.add_box((-5, -0.1, -10), (5, 0, 10), 'rock')
    env.add_box((-5, 3.5, -10), (5, 3.6, 10), 'rock')
    
    # Stone walls
    env.add_box((-5.1, 0, -10), (-5, 3.5, 10), 'brick')
    env.add_box((5, 0, -10), (5.1, 3.5, 10), 'brick')
    env.add_box((-5, 0, -10.1), (5, 3.5, -10), 'brick')
    env.add_box((-5, 0, 10), (5, 3.5, 10.1), 'brick')
    
    # Pillars
    for z in [-6, -2, 2, 6]:
        env.add_box((-3.5, 0, z-0.4), (-2.9, 3.5, z+0.4), 'concrete')
        env.add_box((2.9, 0, z-0.4), (3.5, 3.5, z+0.4), 'concrete')
    
    # Torches (emissive)
    for z in [-5, 0, 5]:
        env.add_box((-4.9, 1.8, z-0.1), (-4.7, 2.2, z+0.1), 'glow_orange', emissive=1.2)
        env.add_box((4.7, 1.8, z-0.1), (4.9, 2.2, z+0.1), 'glow_orange', emissive=1.2)
        env.add_light(-4.8, 2.0, z, 0.6)
        env.add_light(4.8, 2.0, z, 0.6)
    
    # Treasure chest
    env.add_box((0, 0, 7), (1, 0.6, 7.8), 'wood_dark')
    env.add_box((0.1, 0.6, 7.1), (0.9, 0.8, 7.7), 'wood_dark')
    
    # Mysterious orb
    env.add_sphere((0, 1.2, 7.4), 0.25, 'glow_green', emissive=1.0)
    
    return env


def create_abstract() -> Environment:
    """Abstract floating shapes in void."""
    env = Environment("Abstract")
    env.sky_color = (5, 5, 15)
    env.spawn_pos = (0, 0, 0)
    env.bounds = ((-20, -20, -20), (20, 20, 20))
    env.ambient = 0.2
    
    import math
    
    # Floating cubes in spiral
    for i in range(12):
        angle = i * 0.5
        r = 3 + i * 0.3
        x = math.cos(angle) * r
        z = math.sin(angle) * r
        y = i * 0.4 - 2
        
        size = 0.3 + (i % 3) * 0.2
        colors = ['glow_blue', 'glow_purple', 'glow_green', 'glow_orange']
        env.add_box((x-size, y-size, z-size), (x+size, y+size, z+size), 
                   colors[i % 4], emissive=0.8)
    
    # Central sphere
    env.add_sphere((0, 0, 0), 1.0, 'hologram', emissive=0.5)
    
    # Floating rings (approximated with boxes)
    for angle_offset in [0, 1.57, 3.14]:
        for i in range(8):
            angle = i * 0.785 + angle_offset
            r = 5
            x = math.cos(angle) * r
            z = math.sin(angle) * r
            env.add_box((x-0.15, -0.15, z-0.15), (x+0.15, 0.15, z+0.15), 'chrome')
    
    # Ambient lights
    env.add_light(0, 5, 0, 1.0)
    env.add_light(5, 0, 5, 0.5)
    env.add_light(-5, 0, -5, 0.5)
    
    return env


# ============================================================
# ENVIRONMENT REGISTRY
# ============================================================

ENVIRONMENTS: Dict[str, Callable[[], Environment]] = {
    'bedroom': create_bedroom,
    'office': create_office,
    'corridor': create_sci_fi_corridor,
    'park': create_outdoor_park,
    'dungeon': create_dungeon,
    'abstract': create_abstract,
}

def get_environment(name: str) -> Environment:
    """Get environment by name."""
    if name in ENVIRONMENTS:
        return ENVIRONMENTS[name]()
    return create_bedroom()  # Default

def list_environments() -> List[str]:
    """Get list of available environment names."""
    return list(ENVIRONMENTS.keys())
