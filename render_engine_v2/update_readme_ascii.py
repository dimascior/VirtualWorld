"""
Reads ascii_preview.txt from the latest debug tile dump and injects it into
both readme.md files, replacing the placeholder block between triple-backtick
fences labelled '[ASCII buffer will be inserted here by CI workflow]'.
"""
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).parent.parent
ASCII_PATH = ROOT / "render_engine_v2" / "_debug_tiles" / "latest" / "ascii_preview.txt"
READMES = [
    ROOT / "readme.md",
]

PLACEHOLDER_RE = re.compile(
    r"```\n\[ASCII buffer will be inserted here by CI workflow\]\n```",
    re.MULTILINE,
)

def main():
    if not ASCII_PATH.exists():
        print(f"ascii_preview.txt not found at {ASCII_PATH}", file=sys.stderr)
        sys.exit(1)

    ascii_buf = ASCII_PATH.read_text(encoding="utf-8").rstrip("\n")
    replacement = f"```\n{ascii_buf}\n```"

    for path in READMES:
        original = path.read_text(encoding="utf-8")
        updated, count = PLACEHOLDER_RE.subn(replacement, original)
        if count:
            path.write_text(updated, encoding="utf-8")
            print(f"Updated {path.name} ({count} replacement(s))")
        else:
            print(f"No placeholder found in {path.name} -- skipped")

if __name__ == "__main__":
    main()
