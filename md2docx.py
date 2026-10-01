#!/usr/bin/env python3
"""CLI: convert a markdown file to .docx next to it (same pipeline as the md-viewer export).

    md2docx.py <path-to-md>      ->  <path-to-md minus extension>.docx
"""
import sys
from pathlib import Path

from server import ApiError, MARKDOWN_SUFFIXES, markdown_to_docx


def main() -> int:
    if len(sys.argv) != 2 or sys.argv[1] in ("-h", "--help"):
        print("usage: mytools md2docx <path-to-md>", file=sys.stderr)
        return 2
    src = Path(sys.argv[1]).expanduser().resolve()
    if not src.is_file():
        print(f"md2docx: no such file: {src}", file=sys.stderr)
        return 1
    if src.suffix.lower() not in MARKDOWN_SUFFIXES:
        print(f"md2docx: '{src.suffix or src.name}' is not markdown", file=sys.stderr)
        return 1
    try:
        data = markdown_to_docx(src)
        out = src.with_suffix(".docx")
        out.write_bytes(data)
    except ApiError as e:
        print(f"md2docx: {e.message}", file=sys.stderr)
        return 1
    except OSError as e:
        print(f"md2docx: {e}", file=sys.stderr)
        return 1
    print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
