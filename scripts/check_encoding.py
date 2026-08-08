#!/usr/bin/env python3
"""
Catch encoding corruption before it reaches the deployed site.

This exists because docs/index.html, the fetch/backfill scripts, and the
workflow YAML once got silently re-saved through a Windows-1252 round trip
(most likely a local editor/tool that mis-detected the file's encoding),
turning every em dash, arrow, and bullet into unreadable multi-character
garbage on the live page. Nothing caught it until it was already live.
This script scans the text files that make up the site and fails if any
of them:
  - aren't valid UTF-8, or
  - contain the tell-tale byte sequences left behind by a UTF-8 -> cp1252
    -> UTF-8 round trip (mangled dashes, quotes, ellipses, middle dots).

Run on every push (see .github/workflows/sanity-check.yml) so a bad commit
gets flagged in CI instead of silently going live on GitHub Pages.
"""

import re
import sys
from pathlib import Path

CHECKED_GLOBS = [
    "docs/*.html",
    "scripts/*.py",
    ".github/workflows/*.yml",
    "*.md",
]

# Byte sequences that only show up when UTF-8 text gets misread as
# Windows-1252 and re-encoded. Real UTF-8 text has no legitimate reason
# to contain these.
MOJIBAKE_PATTERNS = [
    re.compile(rb"\xc3\x82[\xa0-\xbf]"),  # C2 xx: "middle dot"/"degree"-style doubles
    re.compile(rb"\xc3\xa2\xe2\x82\xac"),  # E2 80 xx family (dashes, quotes, ellipsis)
]


def find_files():
    root = Path(__file__).resolve().parent.parent
    seen = set()
    for pattern in CHECKED_GLOBS:
        for path in root.glob(pattern):
            if path.is_file() and path not in seen:
                seen.add(path)
                yield path


def check_file(path: Path, errors: list):
    raw = path.read_bytes()
    try:
        raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        errors.append(f"{path}: not valid UTF-8 ({exc})")
        return
    for pattern in MOJIBAKE_PATTERNS:
        if pattern.search(raw):
            errors.append(
                f"{path}: contains a mojibake byte sequence ({pattern.pattern!r}) "
                "-- looks like it was saved through the wrong encoding"
            )
            return


def main():
    errors = []
    files = list(find_files())
    for path in files:
        check_file(path, errors)

    if errors:
        print("Encoding check FAILED:", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return 1

    print(f"Encoding check passed: {len(files)} files clean.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
