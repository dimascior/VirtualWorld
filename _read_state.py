"""Read and print renderer state from debug server."""
import json, urllib.request, time, sys

def read_state(label="STATE"):
    try:
        raw = urllib.request.urlopen("http://127.0.0.1:8765/state", timeout=3).read()
    except Exception as e:
        print(f"Server not reachable: {e}")
        return None
    d = json.loads(raw)
    age = time.time() - d["timestamp"]
    pos = d["pos"]
    print(f"=== {label} ===")
    print(f"frame={d['frame']}  age={age:.1f}s  fps={d['fps']:.1f}")
    print(f"key_seq={d['key_seq']}  last_keys={d['last_keys']}")
    print(f"pos=({pos[0]:.4f}, {pos[1]:.4f}, {pos[2]:.4f})")
    print(f"yaw={d['yaw_deg']:.3f} deg   pitch={d['pitch_deg']:.3f} deg")
    print(f"center_hit_id={d['center_hit_id']}  center_depth={d['center_depth']:.3f}")
    print()
    preview = d.get("ascii_preview", [])
    for i, line in enumerate(preview):
        print(f"  {i:2d}| {line}")
    print()
    return d

if __name__ == "__main__":
    label = sys.argv[1] if len(sys.argv) > 1 else "STATE"
    read_state(label)
