"""Pure data transformation and JSON persistence helpers."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

DAILY_DATA_FIELDS = (
    "steps",
    "calories",
    "distance_km",
    "resting_hr",
    "avg_stress",
    "sleep_seconds",
    "body_battery_charged",
    "vo2max_running",
    "vo2max_cycling",
    "training_readiness",
)


def has_daily_data(record: object) -> bool:
    """Return whether a record contains at least one real Garmin metric."""
    return isinstance(record, dict) and any(
        record.get(field) is not None for field in DAILY_DATA_FIELDS
    )


def safe_get(
    function: Callable[..., Any],
    *args: Any,
    default: Any = None,
    reraise: tuple[type[BaseException], ...] = (),
) -> Any:
    """Call a Garmin endpoint while allowing selected failures to propagate."""
    try:
        return function(*args)
    except reraise:
        raise
    except Exception as exc:  # Garmin endpoints can raise several client errors.
        print(f"  (skipped one field: {exc})")
        return default


def fetch_day(
    garmin: Any,
    day_iso: str,
    *,
    reraise: tuple[type[BaseException], ...] = (),
) -> dict[str, Any]:
    """Normalize Garmin's daily endpoints into one dashboard record."""
    summary = safe_get(garmin.get_user_summary, day_iso, default={}, reraise=reraise) or {}
    heart_rate = safe_get(garmin.get_heart_rates, day_iso, default={}, reraise=reraise) or {}
    sleep = safe_get(garmin.get_sleep_data, day_iso, default={}, reraise=reraise) or {}
    stress = safe_get(garmin.get_stress_data, day_iso, default={}, reraise=reraise) or {}
    max_metrics = safe_get(garmin.get_max_metrics, day_iso, default=None, reraise=reraise)
    readiness = safe_get(garmin.get_training_readiness, day_iso, default=None, reraise=reraise)

    sleep_summary = sleep.get("dailySleepDTO", {}) if isinstance(sleep, dict) else {}
    vo2max_running = None
    vo2max_cycling = None
    if isinstance(max_metrics, list):
        for entry in max_metrics:
            generic = (entry.get("generic") or {}) if isinstance(entry, dict) else {}
            cycling = (entry.get("cycling") or {}) if isinstance(entry, dict) else {}
            if generic.get("vo2MaxValue") is not None:
                vo2max_running = generic["vo2MaxValue"]
            if cycling.get("vo2MaxValue") is not None:
                vo2max_cycling = cycling["vo2MaxValue"]

    readiness_score = None
    if isinstance(readiness, list) and readiness and isinstance(readiness[0], dict):
        readiness_score = readiness[0].get("score")

    distance_meters = summary.get("totalDistanceMeters")
    return {
        "date": day_iso,
        "steps": summary.get("totalSteps"),
        "calories": summary.get("totalKilocalories"),
        "distance_km": round(distance_meters / 1000, 2) if distance_meters else None,
        "resting_hr": heart_rate.get("restingHeartRate"),
        "avg_stress": stress.get("avgStressLevel") if isinstance(stress, dict) else None,
        "sleep_seconds": sleep_summary.get("sleepTimeSeconds"),
        "body_battery_charged": summary.get("bodyBatteryChargedValue"),
        "vo2max_running": vo2max_running,
        "vo2max_cycling": vo2max_cycling,
        "training_readiness": readiness_score,
    }


def activity_to_record(activity: dict[str, Any]) -> dict[str, Any]:
    """Normalize a Garmin activity for storage and visualization."""
    distance_meters = activity.get("distance") or 0
    duration_seconds = activity.get("duration") or 0
    pace_min_per_km = None
    if distance_meters and duration_seconds:
        pace_min_per_km = round((duration_seconds / 60) / (distance_meters / 1000), 2)

    return {
        "activity_id": activity.get("activityId"),
        "name": activity.get("activityName"),
        "type": (activity.get("activityType") or {}).get("typeKey", "unknown"),
        "start_local": activity.get("startTimeLocal"),
        "duration_seconds": duration_seconds,
        "distance_km": round(distance_meters / 1000, 2) if distance_meters else None,
        "calories": activity.get("calories"),
        "avg_hr": activity.get("averageHR"),
        "max_hr": activity.get("maxHR"),
        "pace_min_per_km": pace_min_per_km,
        "elevation_gain_m": activity.get("elevationGain"),
    }


def fetch_recent_activities(garmin: Any, limit: int = 50) -> dict[str, dict[str, Any]]:
    """Fetch recent activities and key them by ID for idempotent upserts."""
    raw = safe_get(garmin.get_activities, 0, limit, default=[]) or []
    records = {}
    for activity in raw:
        activity_id = activity.get("activityId")
        if activity_id:
            records[str(activity_id)] = activity_to_record(activity)
    return records


def load_json_object(path: Path) -> dict[str, Any]:
    """Load a JSON object, returning an empty object when the file is absent."""
    if not path.exists():
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object in {path}")
    return value


def _write_mirrored(payload: str, primary: Path, mirror: Path) -> None:
    for path in (primary, mirror):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(payload, encoding="utf-8")


def save_history(history: dict[str, Any], primary: Path, mirror: Path) -> None:
    """Write date-keyed history in stable order to source and Pages paths."""
    _write_mirrored(json.dumps(dict(sorted(history.items())), indent=2), primary, mirror)


def save_activities(activities: dict[str, dict[str, Any]], primary: Path, mirror: Path) -> None:
    """Write activities newest-first to source and Pages paths."""
    ordered = dict(
        sorted(
            activities.items(),
            key=lambda item: item[1].get("start_local") or "",
            reverse=True,
        )
    )
    _write_mirrored(json.dumps(ordered, indent=2), primary, mirror)
