"""Live tail of the renderer debug server.

Run in a separate terminal alongside test_bedroom_enhanced.py:
    python render_engine_v2/debug_watcher.py

Streams every keystroke event and every NEW distinct frame the renderer
publishes. Quiet between deltas so the terminal isn't spammed.

Flags:
    --host 127.0.0.1   server host
    --port 8765        server port
    --ascii            also print ASCII preview of each new distinct frame
    --interval 0.1     poll interval seconds
"""
import argparse, json, sys, time
from urllib.request import urlopen
from urllib.error import URLError


def get_json(url, timeout=1.0):
    with urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--interval", type=float, default=0.1)
    ap.add_argument("--ascii", action="store_true",
                    help="Print ASCII preview for each new distinct frame")
    args = ap.parse_args()

    base = f"http://{args.host}:{args.port}"
    print(f"[watcher] connecting to {base} ... (Ctrl+C to quit)")

    # Wait for server to come up
    for _ in range(50):
        try:
            get_json(base + "/state")
            break
        except URLError:
            time.sleep(0.2)
    else:
        print(f"[watcher] could not reach {base}/state - is the renderer running?")
        return 1

    print(f"[watcher] connected. tailing events + frame deltas...")
    print(f"[watcher] columns: TIME PHASE FRAME KEYS YAW PITCH FPS POS HIT DEPTH")
    print("-" * 90)

    last_ev_ts = 0.0
    last_frame_idx = -1

    try:
        while True:
            # 1) New non-frame events (keystrokes, init, quit)
            try:
                events = get_json(base + "/events")
                new_events = [e for e in events if e["ts"] > last_ev_ts and e["phase"] != "frame"]
                for e in new_events:
                    t = time.strftime("%H:%M:%S", time.localtime(e["ts"]))
                    print(f"{t} {e['phase']:9s} {e['summary']}")
                if events:
                    last_ev_ts = max(last_ev_ts, max(e["ts"] for e in events))
            except URLError:
                print("[watcher] server unreachable - retrying...")
                time.sleep(1.0)
                continue
            except Exception as ex:
                print(f"[watcher] events error: {ex}")

            # 2) New distinct frames
            try:
                idx = get_json(base + "/frames")
                count = idx["count"]
                if count > last_frame_idx + 1:
                    # Print summary lines for newly-added frames
                    new_frames = idx["frames"][max(0, last_frame_idx + 1):]
                    for fr in new_frames:
                        t = time.strftime("%H:%M:%S", time.localtime(fr["ts"]))
                        raw_keys = fr.get("trigger_keys") or []
                        # strip non-printable (\r, \n) so they don't wreck the line
                        clean = [repr(k)[1:-1] if (len(k) != 1 or not k.isprintable()) else k
                                 for k in raw_keys]
                        keys = ",".join(clean) or "-"
                        pos = fr.get("pos") or [0, 0, 0]
                        print(f"{t} delta     "
                              f"f={fr['frame']:>5d} keys={keys:<8s} "
                              f"Y{fr['yaw_deg']:+6.1f} P{fr['pitch_deg']:+5.1f} "
                              f"pos=({pos[0]:+.2f},{pos[1]:+.2f},{pos[2]:+.2f}) "
                              f"hit={fr['center_hit_id']:>2} t={fr['center_depth']:.2f}")
                        if args.ascii:
                            try:
                                full = get_json(f"{base}/frames/{fr['frame'] and (idx['count']-1) or 0}")
                            except Exception:
                                full = None
                            # Easier: pull /frames/latest for the most recent, else skip
                    if args.ascii:
                        try:
                            latest = get_json(base + "/frames/latest")
                            if latest:
                                preview = latest.get("ascii_preview") or []
                                print("-" * 60 + " ASCII " + "-" * 17)
                                for line in preview:
                                    print(line)
                                print("-" * 90)
                        except Exception:
                            pass
                    last_frame_idx = count - 1
                elif count == 0:
                    last_frame_idx = -1
            except URLError:
                pass
            except Exception as ex:
                print(f"[watcher] frames error: {ex}")

            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\n[watcher] bye")
        return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
