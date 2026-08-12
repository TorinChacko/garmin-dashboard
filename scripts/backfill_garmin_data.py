#!/usr/bin/env python3
"""Resumable historical backfill for Garmin daily summaries and activities."""

import os
import sys
import time
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
    activity_to_record,
    fetch_day,
    has_daily_data,
    load_json_object,
    safe_get,
    save_activities,
    save_history,
)

TOKEN_DIR = Path("./garmin_tokens")
DATA_FILE = Path("./data/history.json")
DOCS_DATA_FILE = Path("./docs/data/history.json")
ACTIVITIES_FILE = Path("./data/activities.json")
DOCS_ACTIVITIES_FILE = Path("./docs/data/activities.json")


def restore_token_from_secret() -> None:
    try:
        restore_token_from_environment(destination=Path("."))
    except (OSError, ValueError) as exc:
        print(f"ERROR: could not restore Garmin token: {exc}")
        sys.exit(1)


def login():
    try:
        garmin = Garmin()
        garmin.login(str(TOKEN_DIR))
        print("Logged in using restored token.")
        return garmin
    except (GarminConnectAuthenticationError, GarminConnectConnectionError) as exc:
        print(f"ERROR: could not log in with saved token: {exc}")
        sys.exit(1)
    except GarminConnectTooManyRequestsError as exc:
        print(f"Rate limited at login: {exc}")
        sys.exit(1)


def backfill_activities(garmin, activities, delay, max_to_fetch):
    """Page backward through activities, adding only records not already stored."""
    page_size = 50
    start = 0
    fetched_new = 0

    while fetched_new < max_to_fetch:
        print(f"  Fetching activities page (start={start}, size={page_size}) ...")
        page = (
            safe_get(
                garmin.get_activities,
                start,
                page_size,
                default=[],
                reraise=(GarminConnectTooManyRequestsError,),
            )
            or []
        )
        if not page:
            print("  No more activities returned. Reached end of history.")
            break

        new_in_this_page = 0
        for activity in page:
            activity_id = activity.get("activityId")
            if not activity_id:
                continue
            key = str(activity_id)
            if key not in activities:
                activities[key] = activity_to_record(activity)
                new_in_this_page += 1
                fetched_new += 1
                if fetched_new >= max_to_fetch:
                    break

        print(f"  +{new_in_this_page} new activities this page ({fetched_new} total this run)")
        if new_in_this_page == 0:
            print("  Entire page already known; caught up with activity history.")
            break
        start += page_size
        time.sleep(delay)

    return activities


def main() -> None:
    years = float(os.environ.get("BACKFILL_YEARS", "5"))
    delay = float(os.environ.get("REQUEST_DELAY_SEC", "1.5"))
    max_days_per_run = int(os.environ.get("MAX_DAYS_PER_RUN", "600"))

    restore_token_from_secret()
    garmin = login()
    history = load_json_object(DATA_FILE)

    timezone = ZoneInfo(os.environ.get("GARMIN_TIMEZONE", "America/Los_Angeles"))
    today = datetime.now(timezone).date()
    total_days = int(years * 365.25)
    all_dates = [(today - timedelta(days=index + 1)).isoformat() for index in range(total_days)]
    todo = [day for day in all_dates if day not in history or not has_daily_data(history[day])]

    print(f"Backfill target: {total_days} days ({all_dates[-1]} -> {all_dates[0]})")
    print(f"Already have: {total_days - len(todo)} days")
    print(f"Remaining to fetch: {len(todo)} days")

    if todo:
        batch = todo[:max_days_per_run]
        print(f"Fetching {len(batch)} days this run (cap = {max_days_per_run})...")
        fetched_count = 0
        try:
            for index, day_iso in enumerate(batch):
                print(f"[{index + 1}/{len(batch)}] {day_iso} ...")
                record = fetch_day(
                    garmin,
                    day_iso,
                    reraise=(GarminConnectTooManyRequestsError,),
                )
                if has_daily_data(record):
                    history[day_iso] = record
                else:
                    if day_iso in history and not has_daily_data(history[day_iso]):
                        del history[day_iso]
                    print("  No daily metrics returned; leaving this date empty.")
                fetched_count += 1

                if fetched_count % 25 == 0:
                    save_history(history, DATA_FILE, DOCS_DATA_FILE)
                    print(f"  -- progress saved ({fetched_count} fetched so far) --")
                time.sleep(delay)
        except GarminConnectTooManyRequestsError as exc:
            print(f"Rate limited after {fetched_count} days: {exc}")
            print("Saving progress. Re-run this workflow later to continue.")

        save_history(history, DATA_FILE, DOCS_DATA_FILE)
        remaining = len(todo) - fetched_count
        print(
            f"Saved daily summaries. Fetched {fetched_count} days this run. "
            f"{remaining} days still remaining."
        )
    else:
        print("Nothing left to backfill. Daily summaries are complete.")

    max_activities = int(os.environ.get("MAX_ACTIVITIES_PER_RUN", "500"))
    print(f"Backfilling activities (up to {max_activities} new this run)...")
    activities = load_json_object(ACTIVITIES_FILE)
    before_count = len(activities)
    try:
        activities = backfill_activities(garmin, activities, delay, max_activities)
    except GarminConnectTooManyRequestsError as exc:
        print(f"Rate limited during activity backfill: {exc}")
        print("Saving progress. Re-run this workflow later to continue.")
    save_activities(activities, ACTIVITIES_FILE, DOCS_ACTIVITIES_FILE)
    print(f"Activities: {before_count} -> {len(activities)} total stored.")


if __name__ == "__main__":
    main()
