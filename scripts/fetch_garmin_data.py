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


def write_step_summary(title, lines):
    """Writes to the GitHub Actions Step Summary panel, which shows up
    prominently on the run page (and is what you see first when you click
    into a failed run from the email notification)."""
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary_path:
        return  # not running in Actions, e.g. local testing
    with open(summary_path, "a") as f:
        f.write(f"## {title}\n\n")
        for line in lines:
            f.write(f"{line}\n")
        f.write("\n")


def login():
    try:
        garmin = Garmin()
        garmin.login(str(TOKEN_DIR))
        print("Logged in using restored token.")
        return garmin
    except (GarminConnectAuthenticationError, GarminConnectConnectionError) as e:
        print(f"ERROR: could not log in with saved token: {e}")
        write_step_summary("❌ Garmin token expired", [
            "Your saved Garmin login token has stopped working — this happens",
            "every few months and is expected, not a bug.",
            "",
            "**To fix it (5 minutes), on your own computer:**",
            "1. `python login_once.py`",
            "2. `python pack_token.py`",
            "3. Copy the contents of `garmin_tokens_b64.txt`",
            "4. GitHub repo → Settings → Secrets and variables → Actions",
            "   → edit `GARMIN_TOKENS_B64` → paste the new value → Save",
            "5. Re-run this workflow from the Actions tab to confirm it works",
            "",
            f"_Raw error: {e}_",
        ])
        sys.exit(1)
    except GarminConnectTooManyRequestsError as e:
        print(f"Rate limited by Garmin: {e}")
        write_step_summary("⏳ Rate limited by Garmin", [
            "No action needed — Garmin temporarily rate-limited this run.",
            "It will self-heal on the next scheduled run.",
            "",
            f"_Raw error: {e}_",
        ])
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
    max_metrics = safe_get(garmin.get_max_metrics, day_iso, default=None)
    readiness = safe_get(garmin.get_training_readiness, day_iso, default=None)

    sleep_summary = sleep.get("dailySleepDTO", {}) if isinstance(sleep, dict) else {}

    # get_max_metrics returns a list of metric groups; find VO2 max entries
    vo2max_running = None
    vo2max_cycling = None
    if isinstance(max_metrics, list):
        for entry in max_metrics:
            generic = (entry.get("generic") or {}) if isinstance(entry, dict) else {}
            cycling = (entry.get("cycling") or {}) if isinstance(entry, dict) else {}
            if generic.get("vo2MaxValue"):
                vo2max_running = generic.get("vo2MaxValue")
            if cycling.get("vo2MaxValue"):
                vo2max_cycling = cycling.get("vo2MaxValue")

    # get_training_readiness returns a list, most recent first
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


def fetch_recent_activities(garmin, limit=50):
    """Pulls recent activities with pace/HR/duration for the dashboard's
    weekly mileage and running-stats views. Returns a list of dicts keyed
    by activity ID so re-runs can upsert without duplicating."""
    raw = safe_get(garmin.get_activities, 0, limit, default=[]) or []
    out = {}
    for a in raw:
        activity_id = a.get("activityId")
        if not activity_id:
            continue
        activity_type = (a.get("activityType") or {}).get("typeKey", "unknown")
        distance_m = a.get("distance") or 0
        duration_s = a.get("duration") or 0
        avg_speed_mps = a.get("averageSpeed")  # meters/sec, Garmin's raw field

        # Pace as min/km, only meaningful for run/walk/hike where distance>0
        pace_min_per_km = None
        if distance_m and duration_s:
            pace_min_per_km = round((duration_s / 60) / (distance_m / 1000), 2)

        out[str(activity_id)] = {
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
    return out


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

    # Activities (runs, rides, etc.) — pulls the most recent N; upserts by
    # activity ID so re-runs don't duplicate, and old activities outside
    # this window stay untouched.
    activity_fetch_limit = int(os.environ.get("ACTIVITY_FETCH_LIMIT", "50"))
    print(f"Fetching last {activity_fetch_limit} activities ...")
    activities = load_activities()
    new_activities = fetch_recent_activities(garmin, limit=activity_fetch_limit)
    activities.update(new_activities)
    save_activities(activities)
    print(f"Saved {len(activities)} total activities to {ACTIVITIES_FILE}")

    write_step_summary("✅ Garmin sync OK", [
        f"Fetched the last {backfill_days} days. {len(history)} total days now stored.",
        f"Fetched last {activity_fetch_limit} activities. {len(activities)} total activities now stored.",
        f"Most recent date pulled: {today.isoformat()}",
    ])


if __name__ == "__main__":
    main()
