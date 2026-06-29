#!/usr/bin/env python3
"""
One-time historical backfill — pulls years of past Garmin data.

This is SEPARATE from scripts/fetch_garmin_data.py (your daily job).
Run this once (likely across a few re-runs, since Garmin rate-limits),
then your existing daily workflow keeps things topped up going forward.

Designed to be resumable: it skips any date already present in
data/history.json, so if it gets rate-limited or times out, just
re-run it (or re-trigger the GitHub Action) and it picks up where it
left off.

Env vars:
    BACKFILL_YEARS      how many years back to go (default 5)
    REQUEST_DELAY_SEC    seconds to sleep between each day's batch of
                         API calls, to stay polite to Garmin's servers
                         (default 1.5)
    MAX_DAYS_PER_RUN     safety cap on how many NEW days to fetch in a
                         single execution, so one run doesn't hammer
                         Garmin for hours straight (default 600)
"""

import base64
import io
import json
import os
import sys
import tarfile
import time
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
        sys.exit(1)
    except GarminConnectTooManyRequestsError as e:
        print(f"Rate limited at login: {e}")
        sys.exit(1)


def safe_get(fn, *args, default=None):
    try:
        return fn(*args)
    except GarminConnectTooManyRequestsError:
        raise  # bubble up — caller needs to stop the whole run
    except Exception as e:
        print(f"    (skipped one field: {e})")
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
    ordered = dict(sorted(history.items()))
    payload = json.dumps(ordered, indent=2)
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    DATA_FILE.write_text(payload)
    DOCS_DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    DOCS_DATA_FILE.write_text(payload)


def main():
    years = float(os.environ.get("BACKFILL_YEARS", "5"))
    delay = float(os.environ.get("REQUEST_DELAY_SEC", "1.5"))
    max_days_per_run = int(os.environ.get("MAX_DAYS_PER_RUN", "600"))

    restore_token_from_secret()
    garmin = login()

    history = load_history()

    today = date.today()
    total_days = int(years * 365.25)
    all_dates = [(today - timedelta(days=i)).isoformat() for i in range(total_days)]

    # Skip anything we already have AND that already has real data
    # (not a null-filled placeholder from a day with no watch sync).
    todo = [d for d in all_dates if d not in history or history[d].get("steps") is None]

    print(f"Backfill target: {total_days} days ({all_dates[-1]} -> {all_dates[0]})")
    print(f"Already have: {total_days - len(todo)} days")
    print(f"Remaining to fetch: {len(todo)} days")

    if not todo:
        print("Nothing left to backfill. Done.")
        return

    batch = todo[:max_days_per_run]
    print(f"Fetching {len(batch)} days this run (cap = {max_days_per_run})...\n")

    fetched_count = 0
    try:
        for i, day_iso in enumerate(batch):
            print(f"[{i+1}/{len(batch)}] {day_iso} ...")
            history[day_iso] = fetch_day(garmin, day_iso)
            fetched_count += 1

            # Save progress every 25 days, so a crash doesn't lose everything
            if fetched_count % 25 == 0:
                save_history(history)
                print(f"  -- progress saved ({fetched_count} fetched so far) --")

            time.sleep(delay)

    except GarminConnectTooManyRequestsError as e:
        print(f"\nRate limited by Garmin after {fetched_count} days: {e}")
        print("Saving progress. Re-run this workflow later to continue.")

    save_history(history)
    remaining = len(todo) - fetched_count
    print(f"\nSaved. Fetched {fetched_count} days this run. {remaining} days still remaining.")
    if remaining > 0:
        print("Re-run the 'Backfill Garmin History' workflow again to continue from here.")
    else:
        print("Backfill complete!")


if __name__ == "__main__":
    main()
