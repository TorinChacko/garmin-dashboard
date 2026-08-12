"""Validation helpers for Garmin dashboard records."""

from typing import Any

RANGES = {
    "steps": (0, 200_000),
    "calories": (0, 30_000),
    "distance_km": (0, 1_000),
    "resting_hr": (20, 250),
    # Garmin uses -1/-2 sentinels on some historical stress summaries.
    "avg_stress": (-2, 100),
    "sleep_seconds": (0, 172_800),
    "body_battery_charged": (0, 100),
    "vo2max_running": (0, 100),
    "vo2max_cycling": (0, 100),
    "training_readiness": (0, 100),
}


def validate_changed_record(day: str, record: object, errors: list[str]) -> None:
    """Append validation errors for a changed daily record."""
    if not isinstance(record, dict):
        errors.append(f"{day}: daily record is not an object")
        return
    if record.get("date") != day:
        errors.append(f"{day}: embedded date is {record.get('date')!r}")
    for field, (minimum, maximum) in RANGES.items():
        value: Any = record.get(field)
        if value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            errors.append(f"{day}: {field} is not numeric ({value!r})")
        elif not minimum <= value <= maximum:
            errors.append(f"{day}: {field}={value!r} is outside {minimum}..{maximum}")
