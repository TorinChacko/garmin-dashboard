from garmin_dashboard.validation import validate_changed_record


def test_validation_accepts_a_well_formed_record():
    errors = []
    validate_changed_record(
        "2026-08-12",
        {"date": "2026-08-12", "steps": 12_000, "resting_hr": 48},
        errors,
    )
    assert errors == []


def test_validation_rejects_wrong_date_type_and_range():
    errors = []
    validate_changed_record(
        "2026-08-12",
        {"date": "2026-08-11", "steps": True, "resting_hr": 400},
        errors,
    )
    assert errors == [
        "2026-08-12: embedded date is '2026-08-11'",
        "2026-08-12: steps is not numeric (True)",
        "2026-08-12: resting_hr=400 is outside 20..250",
    ]


def test_validation_rejects_non_object_records():
    errors = []
    validate_changed_record("2026-08-12", [], errors)
    assert errors == ["2026-08-12: daily record is not an object"]
