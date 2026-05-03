"""CI Harness for render_engine_v2.

Launches test_bedroom_enhanced.py as a subprocess, drives it entirely
via the HTTP debug server, collects debug output, validates structure,
and converts artifacts to JSON and XML for upload.

Usage:
    python render_engine_v2/ci_harness.py [--out-dir PATH] [--timeout 60]

Exit codes:
    0  All assertions passed
    1  One or more assertions failed
    2  Harness error (server unreachable, subprocess crash, etc.)
"""

import subprocess, sys, os, json, time, csv, shutil
import urllib.request, urllib.error
import xml.etree.ElementTree as ET
import argparse

BASE_URL  = "http://127.0.0.1:{port}"
HERE      = os.path.dirname(os.path.abspath(__file__))
RENDERER  = os.path.join(HERE, "test_bedroom_enhanced.py")
TILES_DIR = os.path.join(HERE, "_debug_tiles", "latest")

# ---------------------------------------------------------------------------
# Keystroke test script
# Each step sends 'keys' via POST /input with 'wait_frames' spacing,
# then waits until at least one new distinct frame appears in GET /frames.
# 'assert_changed' names a state field expected to differ from the previous step.
# ---------------------------------------------------------------------------
STEPS = [
    {"label": "BASELINE",    "keys": [],    "wait_frames": 3, "assert_changed": None},
    {"label": "TURN_RIGHT",  "keys": ["l"], "wait_frames": 4, "assert_changed": "yaw_deg"},
    {"label": "TURN_RIGHT2", "keys": ["l"], "wait_frames": 4, "assert_changed": "yaw_deg"},
    {"label": "LOOK_UP",     "keys": ["i"], "wait_frames": 4, "assert_changed": "pitch_deg"},
    {"label": "FORWARD",     "keys": ["w"], "wait_frames": 4, "assert_changed": "pos"},
    {"label": "FORWARD2",    "keys": ["w"], "wait_frames": 4, "assert_changed": "pos"},
    {"label": "FORWARD3",    "keys": ["w"], "wait_frames": 4, "assert_changed": "pos"},
    {"label": "TURN_LEFT",   "keys": ["j"], "wait_frames": 4, "assert_changed": "yaw_deg"},
    {"label": "LOOK_DOWN",   "keys": ["k"], "wait_frames": 4, "assert_changed": "pitch_deg"},
]


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------
def _get(url, timeout=4.0):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _post(url, payload, timeout=4.0):
    data = json.dumps(payload).encode("utf-8")
    req  = urllib.request.Request(url, data=data,
                                  headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _get_state(base):
    return _get(base + "/state")


def _get_frame_count(base):
    return _get(base + "/frames")["count"]


def _post_keys(base, keys, wait_frames):
    if not keys:
        return None
    return _post(base + "/input", {"keys": keys, "wait_frames": wait_frames})


def _tile_dump(base):
    return _post(base + "/tiles/dump", {}) if False else _get(base + "/tiles/dump")


# ---------------------------------------------------------------------------
# Wait helpers
# ---------------------------------------------------------------------------
def wait_for_server(base, timeout=45, proc=None):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc is not None and proc.poll() is not None:
            return False   # process already exited — no point waiting
        try:
            _get_state(base)
            return True
        except Exception:
            time.sleep(0.3)
    return False


def _drain_stderr(proc):
    """Read and print whatever the renderer wrote to stderr."""
    try:
        data = proc.stderr.read()
        if data and data.strip():
            print("[harness] --- renderer stderr ---", flush=True)
            print(data.decode("utf-8", errors="replace")[:4000], flush=True)
            print("[harness] --- end stderr ---", flush=True)
    except Exception:
        pass


def wait_for_new_frames(base, prev_count, needed=1, timeout=15):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            count = _get_frame_count(base)
            if count >= prev_count + needed:
                return count
        except Exception:
            pass
        time.sleep(0.1)
    return None


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------
def _field_changed(prev_state, curr_state, field):
    if field is None:
        return True
    pv = prev_state.get(field)
    cv = curr_state.get(field)
    return pv != cv


def validate_colors_csv(csv_path):
    """Returns (row_count, list_of_errors)."""
    errors = []
    rows = 0
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        required = {"x", "y", "top_r", "top_g", "top_b", "bot_r", "bot_g", "bot_b"}
        if not required.issubset(set(reader.fieldnames or [])):
            errors.append(f"colors.csv missing columns: {required - set(reader.fieldnames or [])}")
            return 0, errors
        for row in reader:
            rows += 1
            for col in ("top_r", "top_g", "top_b", "bot_r", "bot_g", "bot_b"):
                v = int(row[col])
                if not (0 <= v <= 255):
                    errors.append(f"Row {rows} col {col} out of range: {v}")
    if rows == 0:
        errors.append("colors.csv has no data rows")
    return rows, errors


def validate_tiles_json(tiles_path):
    """Returns (tile_count, list_of_errors)."""
    errors = []
    with open(tiles_path, encoding="utf-8") as f:
        data = json.load(f)
    tiles = data.get("tiles", [])
    for t in tiles:
        for key in ("x", "y", "top_mat_id", "bot_mat_id", "top_color", "bot_color"):
            if key not in t:
                errors.append(f"Tile missing key '{key}': {t}")
                break
        else:
            for v in t["top_color"] + t["bot_color"]:
                if not (0 <= v <= 255):
                    errors.append(f"Color out of range at ({t['x']},{t['y']}): {v}")
    return len(tiles), errors


# ---------------------------------------------------------------------------
# Format converters
# ---------------------------------------------------------------------------
def csv_to_json(csv_path, json_path):
    rows = []
    with open(csv_path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            rows.append({k: int(v) for k, v in row.items()})
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump({"source": os.path.basename(csv_path), "tiles": rows}, f, indent=2)
    return len(rows)


def csv_to_xml(csv_path, xml_path):
    root = ET.Element("render", source=os.path.basename(csv_path))
    tiles_el = ET.SubElement(root, "tiles")
    rows = 0
    with open(csv_path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            t = ET.SubElement(tiles_el, "tile",
                              x=row["x"], y=row["y"])
            top = ET.SubElement(t, "top")
            top.set("r", row["top_r"]); top.set("g", row["top_g"]); top.set("b", row["top_b"])
            bot = ET.SubElement(t, "bot")
            bot.set("r", row["bot_r"]); bot.set("g", row["bot_g"]); bot.set("b", row["bot_b"])
            rows += 1
    tree = ET.ElementTree(root)
    ET.indent(tree, space="  ")
    tree.write(xml_path, encoding="utf-8", xml_declaration=True)
    return rows


# ---------------------------------------------------------------------------
# Main harness
# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir",  default=os.path.join(HERE, "_ci_output"))
    ap.add_argument("--port",     type=int, default=8765)
    ap.add_argument("--timeout",  type=float, default=60.0,
                    help="Max seconds to wait for server startup")
    args = ap.parse_args()

    base = BASE_URL.format(port=args.port)
    os.makedirs(args.out_dir, exist_ok=True)

    env = dict(os.environ)
    env["DBG_DISABLE"]  = "0"
    env["DBG_AUTODUMP"] = "1"
    env["DBG_PORT"]     = str(args.port)

    print(f"[harness] starting renderer: {RENDERER}")
    proc = subprocess.Popen(
        [sys.executable, RENDERER],
        env=env,
        stdout=subprocess.DEVNULL,   # ANSI escape codes are not useful in CI
        stderr=subprocess.PIPE,
        stdin=subprocess.DEVNULL,    # keep msvcrt.kbhit() from blocking on inherited pipe
        cwd=HERE,
    )

    results = {
        "renderer_pid": proc.pid,
        "python":       sys.version,
        "steps":        [],
        "conversions":  [],
        "validations":  [],
        "assertions":   [],
        "overall":      "PENDING",
    }

    try:
        # ----------------------------------------------------------------
        # Phase 1: wait for HTTP server
        # ----------------------------------------------------------------
        print(f"[harness] waiting for server on {base} ...")
        time.sleep(0.5)  # Give subprocess time to start and daemon thread to bind socket
        if not wait_for_server(base, timeout=args.timeout, proc=proc):
            rc = proc.poll()
            msg = (f"renderer exited with code {rc}" if rc is not None
                   else f"server did not start within {args.timeout}s")
            print(f"[harness] ERROR: {msg}")
            proc.terminate()
            _drain_stderr(proc)
            results["overall"] = "FAIL"
            results["error"]   = msg
            _write_results(results, args.out_dir)
            return 2

        print(f"[harness] server is up (PID {proc.pid})")

        # ----------------------------------------------------------------
        # Phase 2: run keystroke script
        # ----------------------------------------------------------------
        prev_state = _get_state(base)
        prev_count = _get_frame_count(base)

        for step_num, step in enumerate(STEPS):
            label  = step["label"]
            keys   = step["keys"]
            wf     = step["wait_frames"]

            if keys:
                _post_keys(base, keys, wf)
                print(f"[harness] step {step_num:02d} {label:12s}  POST keys={keys}")
                new_count = wait_for_new_frames(base, prev_count, needed=1, timeout=12)
            else:
                print(f"[harness] step {step_num:02d} {label:12s}  (no keys, waiting for first frame)")
                # For keyless steps: if a frame already exists we're done; otherwise wait for one.
                if prev_count > 0:
                    new_count = prev_count
                else:
                    new_count = wait_for_new_frames(base, 0, needed=1, timeout=12)
            curr_state = _get_state(base)

            step_rec = {
                "step":      step_num,
                "label":     label,
                "keys":      keys,
                "frame_count": new_count,
                "yaw_deg":   curr_state.get("yaw_deg"),
                "pitch_deg": curr_state.get("pitch_deg"),
                "pos":       curr_state.get("pos"),
                "key_seq":   curr_state.get("key_seq"),
            }
            results["steps"].append(step_rec)

            # Assert the expected field changed
            field = step.get("assert_changed")
            if keys and field:
                changed = _field_changed(prev_state, curr_state, field)
                assertion = {
                    "step":    label,
                    "field":   field,
                    "before":  prev_state.get(field),
                    "after":   curr_state.get(field),
                    "status":  "PASS" if changed else "FAIL",
                }
                results["assertions"].append(assertion)
                indicator = "PASS" if changed else "FAIL"
                print(f"           assert {field} changed: {indicator}")

            if new_count is None:
                print(f"[harness] WARNING: no new distinct frames after step {label}")

            prev_state = curr_state
            prev_count = new_count or prev_count

        # ----------------------------------------------------------------
        # Phase 3: force tile dump for the final frame, then quit
        # ----------------------------------------------------------------
        print("[harness] requesting tile dump for final frame ...")
        try:
            _get(base + "/tiles/dump", timeout=8)
        except Exception as ex:
            print(f"[harness] tile dump request failed: {ex}")

        print("[harness] sending quit keystroke ...")
        try:
            _post_keys(base, ["q"], 1)
        except Exception:
            pass

        proc.wait(timeout=8)

    except KeyboardInterrupt:
        pass
    except Exception as ex:
        print(f"[harness] ERROR: {ex}")
        results["overall"] = "FAIL"
        results["error"]   = str(ex)
    finally:
        if proc.poll() is None:
            proc.terminate()
        _drain_stderr(proc)

    # ----------------------------------------------------------------
    # Phase 4: validate output files
    # ----------------------------------------------------------------
    csv_path   = os.path.join(TILES_DIR, "colors.csv")
    tiles_path = os.path.join(TILES_DIR, "tiles.json")
    meta_path  = os.path.join(TILES_DIR, "meta.json")
    ascii_path = os.path.join(TILES_DIR, "ascii_preview.txt")
    cat_path   = os.path.join(TILES_DIR, "map_category.txt")

    for path, label in [
        (csv_path,   "colors.csv"),
        (tiles_path, "tiles.json"),
        (meta_path,  "meta.json"),
        (ascii_path, "ascii_preview.txt"),
        (cat_path,   "map_category.txt"),
    ]:
        exists = os.path.isfile(path) and os.path.getsize(path) > 0
        results["validations"].append({
            "file":   label,
            "exists": exists,
            "bytes":  os.path.getsize(path) if os.path.isfile(path) else 0,
            "status": "PASS" if exists else "FAIL",
        })
        print(f"[harness] validate {label}: {'PASS' if exists else 'FAIL'}")

    if os.path.isfile(csv_path):
        row_count, csv_errors = validate_colors_csv(csv_path)
        status = "PASS" if not csv_errors else "FAIL"
        results["validations"].append({
            "check":  "colors_csv_values",
            "rows":   row_count,
            "errors": csv_errors[:10],
            "status": status,
        })
        print(f"[harness] validate colors.csv values ({row_count} rows): {status}")

    if os.path.isfile(tiles_path):
        tile_count, tile_errors = validate_tiles_json(tiles_path)
        status = "PASS" if not tile_errors else "FAIL"
        results["validations"].append({
            "check":  "tiles_json_structure",
            "tiles":  tile_count,
            "errors": tile_errors[:10],
            "status": status,
        })
        print(f"[harness] validate tiles.json structure ({tile_count} tiles): {status}")

    # ----------------------------------------------------------------
    # Phase 5: convert CSV -> JSON, CSV -> XML
    # ----------------------------------------------------------------
    if os.path.isfile(csv_path):
        json_out = os.path.join(args.out_dir, "colors.json")
        xml_out  = os.path.join(args.out_dir, "colors.xml")

        try:
            n = csv_to_json(csv_path, json_out)
            results["conversions"].append({"output": "colors.json", "rows": n, "status": "PASS"})
            print(f"[harness] converted colors.csv -> colors.json ({n} rows)")
        except Exception as ex:
            results["conversions"].append({"output": "colors.json", "status": "FAIL", "error": str(ex)})
            print(f"[harness] ERROR converting to JSON: {ex}")

        try:
            n = csv_to_xml(csv_path, xml_out)
            results["conversions"].append({"output": "colors.xml", "rows": n, "status": "PASS"})
            print(f"[harness] converted colors.csv -> colors.xml ({n} rows)")
        except Exception as ex:
            results["conversions"].append({"output": "colors.xml", "status": "FAIL", "error": str(ex)})
            print(f"[harness] ERROR converting to XML: {ex}")

    # Copy the raw debug files into the output dir
    for fname in ("colors.csv", "tiles.json", "meta.json", "ascii_preview.txt",
                  "map_category.txt", "map_top_mat.txt"):
        src = os.path.join(TILES_DIR, fname)
        if os.path.isfile(src):
            shutil.copy2(src, os.path.join(args.out_dir, fname))

    # ----------------------------------------------------------------
    # Phase 6: determine overall result
    # ----------------------------------------------------------------
    all_statuses = (
        [a["status"] for a in results["assertions"]]
        + [v["status"] for v in results["validations"]]
        + [c["status"] for c in results["conversions"]]
    )
    failed = [s for s in all_statuses if s != "PASS"]
    results["overall"] = "PASS" if not failed else "FAIL"

    _write_results(results, args.out_dir)
    print(f"\n[harness] OVERALL: {results['overall']}")
    return 0 if results["overall"] == "PASS" else 1


def _write_results(results, out_dir):
    path = os.path.join(out_dir, "ci_results.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)
    print(f"[harness] results written to {path}")


if __name__ == "__main__":
    sys.exit(main())
