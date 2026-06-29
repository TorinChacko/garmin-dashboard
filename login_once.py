#!/usr/bin/env python3
"""
Run this ONCE on your own computer (not in GitHub Actions).

It logs into Garmin Connect interactively (handles MFA if needed) and
saves an auth token to ./garmin_tokens/ — you'll paste the contents of
that folder into a GitHub Secret in the next step.

Usage (Windows):
    .venv\\Scripts\\activate
    python login_once.py
"""

from pathlib import Path
from getpass import getpass

from garminconnect import Garmin

TOKEN_DIR = Path("./garmin_tokens")

def main():
    email = input("Garmin email: ").strip()
    password = getpass("Garmin password: ")

    garmin = Garmin(
        email=email,
        password=password,
        prompt_mfa=lambda: input("MFA code (check your email/app): ").strip(),
    )
    garmin.login(str(TOKEN_DIR))

    print(f"\nSuccess. Tokens saved to: {TOKEN_DIR.resolve()}")
    print("Next: run 'python pack_token.py' to prepare it for GitHub Secrets.")

if __name__ == "__main__":
    main()
