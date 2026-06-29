#!/usr/bin/env python3
"""
Runs in GitHub Actions on a schedule.

1. Restores the Garmin token from the GARMIN_TOKENS_B64 secret (no password
   needed — that's the whole point of doing login_once.py locally first).
2. Pulls a handful of daily stats.
3. Appends/updates a row in data/history.json (one row per day).
4. Re-saves the (possibly refreshed) token back out, so the next run still
   works even after Garmin rotates the access token.

If the refresh token itself has expired (this can happen every few months),
this script will fail with an auth error — see README "Token expired" section
for how to redo login_once.py.
"""

import base64
import io
import json
import os
import sys
import tarfile
from datetime import date, timedelta
from pathlib import Path

from garminconnect import (
    Garmin,
    GarminConnectAuthenticationError,
    GarminConnectConnectionError,
    GarminConnectTooManyRequestsError,
)

TOKEN_DIR = Path("./garmin_tokens")
DATA_FILE = Path("./data/history.json")
DOCS_DATA_FILE = Path("./docs/data/history.json")


def restore_token_from_secret():
    encoded = os.environ.get("GARMIN_TOKENS_B64")
    if not encoded:
        print("ERROR: GARMIN_TOKENS_B64 secret not set.")
        sys.exit(1)

    raw = base64.b64decode(encoded)
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as tar:
        tar.extractall(".")

    if not TOKEN_DIR.exists():
        print("ERROR: token extraction did not produce garmin_tokens/ folder.")
        sys.exit(1)


def login():
    try:
        garmin = Garmin()
        garmin.login(str(TOKEN_DIR))
        print("Logged in using restored token.")
        return garmin
    except (GarminConnectAuthenticationError, GarminConnectConnectionError) as e:
        print(f"ERROR: could not log in with saved token: {e}")
        print("The refresh token has likely expired. Redo login_once.py + pack_token.py locally,")
        print("then update the GARMIN_TOKENS_B64 secret.")
        sys.exit(1)
    except GarminConnectTooManyRequestsError as e:
        print(f"Rate limited by Garmin: {e}")
        sys.exit(1)


def safe_get(fn, *args, default=None):
    try:
        return fn(*args)
    except Exception as e:
        print(f"  (skipped one field: {e})")
        return default


def fetch_day(garmin, day_iso):
    summary = safe_get(garmin.get_user_summary, day_iso, default={}) or {}
    hr = safe_get(garmin.get_heart_rates, day_iso, default={}) or {}
    sleep = safe_get(garmin.get_sleep_data, day_iso, default={}) or {}
    stress = safe_get(garmin.get_stress_data, day_iso, default={}) or {}

    sleep_summary = sleep.get("dailySleepDTO", {}) if isinstance(sleep, dict) else {}

    return {
        "date": day_iso,
        "steps": summary.get("totalSteps"),
        "calories": summary.get("totalKilocalories"),
        "distance_km": round(summary.get("totalDistanceMeters", 0) / 1000, 2)
            if summary.get("totalDistanceMeters") else None,
        "resting_hr": hr.get("restingHeartRate"),
        "avg_stress": stress.get("avgStressLevel") if isinstance(stress, dict) else None,
        "sleep_seconds": sleep_summary.get("sleepTimeSeconds"),
        "body_battery_charged": summary.get("bodyBatteryChargedValue"),
    }


def load_history():
    if DATA_FILE.exists():
        return json.loads(DATA_FILE.read_text())
    return {}


def save_history(history):
    # keep stable, sorted-by-date output for clean git diffs
    ordered = dict(sorted(history.items()))
    payload = json.dumps(ordered, indent=2)

    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    DATA_FILE.write_text(payload)

    DOCS_DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    DOCS_DATA_FILE.write_text(payload)


def main():
    restore_token_from_secret()
    garmin = login()

    history = load_history()

    # Pull today + a short backfill window, so a missed run (e.g. Actions
    # outage) self-heals on the next successful run.
    backfill_days = int(os.environ.get("BACKFILL_DAYS", "5"))
    today = date.today()

    for i in range(backfill_days):
        day = today - timedelta(days=i)
        day_iso = day.isoformat()
        print(f"Fetching {day_iso} ...")
        history[day_iso] = fetch_day(garmin, day_iso)

    save_history(history)
    print(f"Saved {len(history)} total days to {DATA_FILE}")


if __name__ == "__main__":
    main()
