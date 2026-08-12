import json

import pytest

from garmin_dashboard.data import (
    activity_to_record,
    fetch_day,
    fetch_recent_activities,
    has_daily_data,
    load_json_object,
    safe_get,
    save_activities,
    save_history,
)


class FakeGarmin:
    def get_user_summary(self, day):
        return {
            "totalSteps": 12_345,
            "totalKilocalories": 2_450,
            "totalDistanceMeters": 10_125,
            "bodyBatteryChargedValue": 78,
        }

    def get_heart_rates(self, day):
        return {"restingHeartRate": 48}

    def get_sleep_data(self, day):
        return {"dailySleepDTO": {"sleepTimeSeconds": 28_800}}

    def get_stress_data(self, day):
        return {"avgStressLevel": 22}

    def get_max_metrics(self, day):
        return [{"generic": {"vo2MaxValue": 55}, "cycling": {"vo2MaxValue": 51}}]

    def get_training_readiness(self, day):
        return [{"score": 82}]

    def get_activities(self, start, limit):
        return [
            {
                "activityId": 42,
                "activityName": "Morning Run",
                "activityType": {"typeKey": "running"},
                "startTimeLocal": "2026-08-12 07:00:00",
                "distance": 5_000,
                "duration": 1_500,
                "averageHR": 150,
            },
            {"activityName": "Missing ID"},
        ]


def test_has_daily_data_treats_zero_as_real_data():
    assert has_daily_data({"steps": 0})
    assert not has_daily_data({"steps": None, "resting_hr": None})
    assert not has_daily_data(None)


def test_fetch_day_normalizes_all_metrics():
    record = fetch_day(FakeGarmin(), "2026-08-12")

    assert record == {
        "date": "2026-08-12",
        "steps": 12_345,
        "calories": 2_450,
        "distance_km": 10.12,
        "resting_hr": 48,
        "avg_stress": 22,
        "sleep_seconds": 28_800,
        "body_battery_charged": 78,
        "vo2max_running": 55,
        "vo2max_cycling": 51,
        "training_readiness": 82,
    }


def test_safe_get_can_default_or_propagate_selected_errors():
    class RateLimited(Exception):
        pass

    def fail():
        raise RateLimited("slow down")

    assert safe_get(fail, default={}) == {}
    with pytest.raises(RateLimited):
        safe_get(fail, default={}, reraise=(RateLimited,))


def test_activity_conversion_calculates_metric_pace():
    record = activity_to_record(
        {
            "activityId": 7,
            "activityType": {"typeKey": "running"},
            "distance": 10_000,
            "duration": 3_000,
        }
    )

    assert record["distance_km"] == 10
    assert record["pace_min_per_km"] == 5


def test_recent_activities_are_keyed_and_missing_ids_are_skipped():
    records = fetch_recent_activities(FakeGarmin(), limit=10)

    assert list(records) == ["42"]
    assert records["42"]["name"] == "Morning Run"


def test_history_and_activities_are_mirrored_in_stable_order(tmp_path):
    history_path = tmp_path / "data" / "history.json"
    docs_history_path = tmp_path / "docs" / "history.json"
    save_history(
        {"2026-08-12": {"steps": 2}, "2026-08-11": {"steps": 1}},
        history_path,
        docs_history_path,
    )
    assert history_path.read_bytes() == docs_history_path.read_bytes()
    assert list(load_json_object(history_path)) == ["2026-08-11", "2026-08-12"]

    activities_path = tmp_path / "data" / "activities.json"
    docs_activities_path = tmp_path / "docs" / "activities.json"
    save_activities(
        {
            "old": {"start_local": "2026-01-01"},
            "new": {"start_local": "2026-08-12"},
        },
        activities_path,
        docs_activities_path,
    )
    assert json.loads(activities_path.read_text(encoding="utf-8")) == {
        "new": {"start_local": "2026-08-12"},
        "old": {"start_local": "2026-01-01"},
    }
    assert activities_path.read_bytes() == docs_activities_path.read_bytes()
