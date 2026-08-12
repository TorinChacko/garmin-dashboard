#!/usr/bin/env python3
"""Reject unsafe Garmin data changes before a workflow commits them."""

import json
import os
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from garmin_dashboard.data import has_daily_data
from garmin_dashboard.validation import validate_changed_record

HISTORY_PATH = Path("data/history.json")
DOCS_HISTORY_PATH = Path("docs/data/history.json")
ACTIVITIES_PATH = Path("data/activities.json")
DOCS_ACTIVITIES_PATH = Path("docs/data/activities.json")

def load_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Cannot read valid JSON from {path}: {exc}") from exc


def load_previous(path):
    result = subprocess.run(
        ["git", "show", f"HEAD:{path.as_posix()}"],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if result.returncode != 0:
        raise ValueError(f"Cannot read previous {path} from HEAD: {result.stderr.strip()}")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Previous {path} is not valid JSON: {exc}") from exc


def validate():
    errors = []
    history = load_json(HISTORY_PATH)
    docs_history = load_json(DOCS_HISTORY_PATH)
    activities = load_json(ACTIVITIES_PATH)
    docs_activities = load_json(DOCS_ACTIVITIES_PATH)
    previous_history = load_previous(HISTORY_PATH)
    previous_activities = load_previous(ACTIVITIES_PATH)

    if history != docs_history:
        errors.append("data/history.json and docs/data/history.json differ")
    if activities != docs_activities:
        errors.append("data/activities.json and docs/data/activities.json differ")

    timezone = ZoneInfo(os.environ.get("GARMIN_TIMEZONE", "America/Los_Angeles"))
    local_today = datetime.now(timezone).date()

    for day, record in history.items():
        try:
            parsed_day = date.fromisoformat(day)
        except (TypeError, ValueError):
            errors.append(f"Invalid history date key: {day!r}")
            continue
        if parsed_day > local_today:
            errors.append(f"{day}: future date relative to {timezone.key}")

        previous = previous_history.get(day)
        if previous != record:
            validate_changed_record(day, record, errors)
        if day not in previous_history and not has_daily_data(record):
            errors.append(f"{day}: refusing to add an all-null daily record")
        elif has_daily_data(previous) and not has_daily_data(record):
            errors.append(f"{day}: refusing to replace populated data with an empty record")

    removed_days = set(previous_history) - set(history)
    destructive_removals = sorted(
        day for day in removed_days if has_daily_data(previous_history[day])
    )
    if destructive_removals:
        errors.append(
            "Refusing to remove populated history dates: " + ", ".join(destructive_removals[:10])
        )

    removed_activities = set(previous_activities) - set(activities)
    if removed_activities:
        errors.append(f"Refusing to remove {len(removed_activities)} existing activities")

    if errors:
        print("Garmin data validation FAILED:", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return 1

    changed_days = sum(previous_history.get(day) != record for day, record in history.items())
    print(
        "Garmin data validation passed: "
        f"{len(history)} days ({changed_days} changed), "
        f"{len(activities)} activities."
    )
    return 0


if __name__ == "__main__":
    try:
        sys.exit(validate())
    except ValueError as exc:
        print(f"Garmin data validation FAILED: {exc}", file=sys.stderr)
        sys.exit(1)
