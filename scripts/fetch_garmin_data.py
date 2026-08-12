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

import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from garminconnect import (
    Garmin,
    GarminConnectAuthenticationError,
    GarminConnectConnectionError,
    GarminConnectTooManyRequestsError,
)

from garmin_dashboard.auth import restore_token_from_environment
from garmin_dashboard.data import (
    fetch_day,
    fetch_recent_activities,
    has_daily_data,
    load_json_object,
    save_activities,
    save_history,
)

TOKEN_DIR = Path("./garmin_tokens")
DATA_FILE = Path("./data/history.json")
DOCS_DATA_FILE = Path("./docs/data/history.json")
ACTIVITIES_FILE = Path("./data/activities.json")
DOCS_ACTIVITIES_FILE = Path("./docs/data/activities.json")


def restore_token_from_secret():
    try:
        restore_token_from_environment(destination=Path("."))
    except (OSError, ValueError) as exc:
        print(f"ERROR: could not restore Garmin token: {exc}")
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
        write_step_summary(
            "❌ Garmin token expired",
            [
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
            ],
        )
        sys.exit(1)
    except GarminConnectTooManyRequestsError as e:
        print(f"Rate limited by Garmin: {e}")
        write_step_summary(
            "⏳ Rate limited by Garmin",
            [
                "No action needed — Garmin temporarily rate-limited this run.",
                "It will self-heal on the next scheduled run.",
                "",
                f"_Raw error: {e}_",
            ],
        )
        sys.exit(1)


def main():
    restore_token_from_secret()
    garmin = login()

    history = load_json_object(DATA_FILE)

    # Pull today + a short backfill window, so a missed run (e.g. Actions
    # outage) self-heals on the next successful run.
    backfill_days = int(os.environ.get("BACKFILL_DAYS", "5"))
    timezone = ZoneInfo(os.environ.get("GARMIN_TIMEZONE", "America/Los_Angeles"))
    today = datetime.now(timezone).date()

    for i in range(backfill_days):
        day = today - timedelta(days=i)
        day_iso = day.isoformat()
        print(f"Fetching {day_iso} ...")
        record = fetch_day(garmin, day_iso)
        if has_daily_data(record):
            history[day_iso] = record
        else:
            # Garmin often returns an all-null summary shortly after midnight.
            # Never let that placeholder replace a valid row or become the
            # dashboard's newest day.
            if day_iso in history and not has_daily_data(history[day_iso]):
                del history[day_iso]
            print(f"Skipping {day_iso}: Garmin returned no daily metrics.")

    save_history(history, DATA_FILE, DOCS_DATA_FILE)
    print(f"Saved {len(history)} total days to {DATA_FILE}")

    # Activities (runs, rides, etc.) — pulls the most recent N; upserts by
    # activity ID so re-runs don't duplicate, and old activities outside
    # this window stay untouched.
    activity_fetch_limit = int(os.environ.get("ACTIVITY_FETCH_LIMIT", "50"))
    print(f"Fetching last {activity_fetch_limit} activities ...")
    activities = load_json_object(ACTIVITIES_FILE)
    new_activities = fetch_recent_activities(garmin, limit=activity_fetch_limit)
    activities.update(new_activities)
    save_activities(activities, ACTIVITIES_FILE, DOCS_ACTIVITIES_FILE)
    print(f"Saved {len(activities)} total activities to {ACTIVITIES_FILE}")

    write_step_summary(
        "✅ Garmin sync OK",
        [
            f"Fetched the last {backfill_days} days. {len(history)} total days now stored.",
            f"Fetched last {activity_fetch_limit} activities. "
            f"{len(activities)} total activities now stored.",
            f"Most recent date pulled: {today.isoformat()}",
        ],
    )


if __name__ == "__main__":
    main()
