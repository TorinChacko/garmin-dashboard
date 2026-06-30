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
ACTIVITIES_FILE = Path("./data/activities.json")
DOCS_ACTIVITIES_FILE = Path("./docs/data/activities.json")


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
    max_metrics = safe_get(garmin.get_max_metrics, day_iso, default=None)
    readiness = safe_get(garmin.get_training_readiness, day_iso, default=None)

    sleep_summary = sleep.get("dailySleepDTO", {}) if isinstance(sleep, dict) else {}

    vo2max_running = None
    vo2max_cycling = None
    if isinstance(max_metrics, list):
        for entry in max_metrics:
            generic = entry.get("generic", {}) if isinstance(entry, dict) else {}
            cycling = entry.get("cycling", {}) if isinstance(entry, dict) else {}
            if generic.get("vo2MaxValue"):
                vo2max_running = generic.get("vo2MaxValue")
            if cycling.get("vo2MaxValue"):
                vo2max_cycling = cycling.get("vo2MaxValue")

    readiness_score = None
    if isinstance(readiness, list) and readiness:
        readiness_score = readiness[0].get("score")

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
        "vo2max_running": vo2max_running,
        "vo2max_cycling": vo2max_cycling,
        "training_readiness": readiness_score,
    }


def activity_to_record(a):
    activity_id = a.get("activityId")
    activity_type = (a.get("activityType") or {}).get("typeKey", "unknown")
    distance_m = a.get("distance") or 0
    duration_s = a.get("duration") or 0

    pace_min_per_km = None
    if distance_m and duration_s:
        pace_min_per_km = round((duration_s / 60) / (distance_m / 1000), 2)

    return {
        "activity_id": activity_id,
        "name": a.get("activityName"),
        "type": activity_type,
        "start_local": a.get("startTimeLocal"),
        "duration_seconds": duration_s,
        "distance_km": round(distance_m / 1000, 2) if distance_m else None,
        "calories": a.get("calories"),
        "avg_hr": a.get("averageHR"),
        "max_hr": a.get("maxHR"),
        "pace_min_per_km": pace_min_per_km,
        "elevation_gain_m": a.get("elevationGain"),
    }


def load_activities():
    if ACTIVITIES_FILE.exists():
        return json.loads(ACTIVITIES_FILE.read_text())
    return {}


def save_activities(activities):
    ordered = dict(sorted(activities.items(), key=lambda kv: kv[1].get("start_local") or "", reverse=True))
    payload = json.dumps(ordered, indent=2)
    ACTIVITIES_FILE.parent.mkdir(parents=True, exist_ok=True)
    ACTIVITIES_FILE.write_text(payload)
    DOCS_ACTIVITIES_FILE.parent.mkdir(parents=True, exist_ok=True)
    DOCS_ACTIVITIES_FILE.write_text(payload)


def backfill_activities(garmin, activities, delay, max_to_fetch):
    """Pages through get_activities(start, limit) until it has fetched
    max_to_fetch NEW activities (ones not already in `activities`), or runs
    out of history. Garmin returns most-recent-first, so once we hit a page
    that's entirely already-known activities, we can stop early."""
    page_size = 50
    start = 0
    fetched_new = 0

    while fetched_new < max_to_fetch:
        print(f"  Fetching activities page (start={start}, size={page_size}) ...")
        try:
            page = safe_get(garmin.get_activities, start, page_size, default=[]) or []
        except GarminConnectTooManyRequestsError:
            raise

        if not page:
            print("  No more activities returned. Reached end of history.")
            break

        new_in_this_page = 0
        for a in page:
            activity_id = a.get("activityId")
            if not activity_id:
                continue
            key = str(activity_id)
            if key not in activities:
                activities[key] = activity_to_record(a)
                new_in_this_page += 1
                fetched_new += 1

        print(f"  +{new_in_this_page} new activities this page ({fetched_new} total this run)")

        if new_in_this_page == 0:
            print("  Entire page already known — caught up with activity history.")
            break

        start += page_size
        time.sleep(delay)

    return activities


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
    print(f"\nSaved daily summaries. Fetched {fetched_count} days this run. {remaining} days still remaining.")
    if remaining > 0:
        print("Re-run the 'Backfill Garmin History' workflow again to continue from here.")
    else:
        print("Daily summary backfill complete!")

    # --- Activities backfill (separate from daily summaries) ---
    max_activities_per_run = int(os.environ.get("MAX_ACTIVITIES_PER_RUN", "500"))
    print(f"\nBackfilling activities (up to {max_activities_per_run} new this run)...")
    activities = load_activities()
    before_count = len(activities)
    try:
        activities = backfill_activities(garmin, activities, delay, max_activities_per_run)
    except GarminConnectTooManyRequestsError as e:
        print(f"\nRate limited by Garmin during activity backfill: {e}")
        print("Saving progress. Re-run this workflow later to continue.")
    save_activities(activities)
    print(f"Activities: {before_count} -> {len(activities)} total stored.")


if __name__ == "__main__":
    main()
