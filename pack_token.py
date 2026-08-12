#!/usr/bin/env python3
"""
Run this AFTER login_once.py succeeds.

GitHub Secrets only store single text strings, but Garmin's token store is a
folder of files. This script tars + base64-encodes that folder into one
string you can paste directly into a GitHub Secret named GARMIN_TOKENS_B64.
"""

from pathlib import Path

from garmin_dashboard.auth import pack_token_directory

TOKEN_DIR = Path("./garmin_tokens")


def main():
    if not TOKEN_DIR.exists():
        print("No ./garmin_tokens folder found. Run login_once.py first.")
        return

    encoded = pack_token_directory(TOKEN_DIR)

    out_file = Path("garmin_tokens_b64.txt")
    out_file.write_text(encoded, encoding="utf-8")

    print(f"Wrote encoded token to {out_file.resolve()}")
    print("\nNext steps:")
    print("1. Open that file, copy its ENTIRE contents.")
    print(
        "2. In your GitHub repo: Settings -> Secrets and variables -> "
        "Actions -> New repository secret"
    )
    print("3. Name it: GARMIN_TOKENS_B64")
    print("4. Paste the copied text as the value.")
    print("\nThen DELETE garmin_tokens_b64.txt locally — it contains your live session token.")


if __name__ == "__main__":
    main()
