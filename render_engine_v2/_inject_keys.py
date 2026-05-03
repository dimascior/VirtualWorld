"""Inject keystrokes into the renderer's console via WriteConsoleInput.

Usage: python _inject_keys.py <cmd_pid> <keys>
  e.g. python _inject_keys.py 7716 wwwjji
"""
import sys, ctypes, ctypes.wintypes, time

kernel32 = ctypes.windll.kernel32

STD_INPUT_HANDLE = -10
KEY_EVENT = 0x0001

class KEY_EVENT_RECORD(ctypes.Structure):
    _fields_ = [
        ("bKeyDown", ctypes.wintypes.BOOL),
        ("wRepeatCount", ctypes.wintypes.WORD),
        ("wVirtualKeyCode", ctypes.wintypes.WORD),
        ("wVirtualScanCode", ctypes.wintypes.WORD),
        ("uChar", ctypes.c_wchar),
        ("dwControlKeyState", ctypes.wintypes.DWORD),
    ]

class INPUT_RECORD_UNION(ctypes.Union):
    _fields_ = [("KeyEvent", KEY_EVENT_RECORD)]

class INPUT_RECORD(ctypes.Structure):
    _fields_ = [
        ("EventType", ctypes.wintypes.WORD),
        ("Event", INPUT_RECORD_UNION),
    ]

def make_key_event(ch, down):
    vk = ctypes.windll.user32.VkKeyScanW(ord(ch)) & 0xFF
    scan = ctypes.windll.user32.MapVirtualKeyW(vk, 0)
    rec = INPUT_RECORD()
    rec.EventType = KEY_EVENT
    rec.Event.KeyEvent.bKeyDown = down
    rec.Event.KeyEvent.wRepeatCount = 1
    rec.Event.KeyEvent.wVirtualKeyCode = vk
    rec.Event.KeyEvent.wVirtualScanCode = scan
    rec.Event.KeyEvent.uChar = ch
    rec.Event.KeyEvent.dwControlKeyState = 0
    return rec

def inject(pid, keys):
    kernel32.FreeConsole()
    if not kernel32.AttachConsole(pid):
        print(f"AttachConsole({pid}) failed: {ctypes.GetLastError()}")
        return False

    h = kernel32.GetStdHandle(STD_INPUT_HANDLE)
    if h == -1:
        print("GetStdHandle failed")
        return False

    written = ctypes.wintypes.DWORD(0)
    for ch in keys:
        down = make_key_event(ch, True)
        up = make_key_event(ch, False)
        records = (INPUT_RECORD * 2)(down, up)
        ok = kernel32.WriteConsoleInputW(h, records, 2, ctypes.byref(written))
        if not ok:
            print(f"WriteConsoleInput failed for '{ch}': {ctypes.GetLastError()}")
        time.sleep(0.50)

    kernel32.FreeConsole()
    return True

if __name__ == "__main__":
    pid = int(sys.argv[1])
    keys = sys.argv[2]
    print(f"Injecting '{keys}' into PID {pid}")
    ok = inject(pid, keys)
    print(f"Done: {'success' if ok else 'FAILED'}")
