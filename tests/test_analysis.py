"""Unit tests for analysis.py (small hand-made datasets, so every expected
number can be checked by hand)."""
import math
import os

import pandas as pd
import pytest

import analysis


def make_events(rows):
    """rows: list of (user, event, time_string). Properties are fixed."""
    df = pd.DataFrame(rows, columns=["distinct_id", "event", "time"])
    df["time"] = pd.to_datetime(df["time"], utc=True)
    df["platform"] = "iOS"
    df["acquisition_channel"] = "organic_search"
    df["plan_type"] = "free"
    return df


# ---------------- validation ----------------
def test_clean_data_has_no_problems():
    df = make_events([
        ("u1", "signup", "2026-07-01 10:00"),
        ("u1", "profile_completed", "2026-07-01 10:30"),
    ])
    assert analysis.validate_events(df) == []


def test_detects_missing_column():
    df = make_events([("u1", "signup", "2026-07-01 10:00")]).drop(columns=["platform"])
    problems = analysis.validate_events(df)
    assert len(problems) == 1 and "missing columns" in problems[0]


def test_detects_unknown_event_and_duplicates():
    df = make_events([
        ("u1", "signup", "2026-07-01 10:00"),
        ("u1", "signup", "2026-07-01 10:00"),          # exact duplicate
        ("u1", "mystery_event", "2026-07-01 11:00"),   # unknown name
    ])
    problems = " | ".join(analysis.validate_events(df))
    assert "unknown event names" in problems
    assert "duplicate events" in problems


def test_detects_event_before_signup():
    df = make_events([
        ("u1", "signup", "2026-07-02 10:00"),
        ("u1", "profile_completed", "2026-07-01 10:00"),  # earlier than signup
    ])
    assert any("before the user's signup" in p for p in analysis.validate_events(df))


# ---------------- funnel ----------------
def test_funnel_counts_by_hand():
    df = make_events([
        # u1 completes the whole funnel
        ("u1", "signup", "2026-07-01 10:00"),
        ("u1", "profile_completed", "2026-07-01 10:10"),
        ("u1", "provider_search", "2026-07-01 11:00"),
        ("u1", "booking_started", "2026-07-01 12:00"),
        ("u1", "booking_completed", "2026-07-01 12:30"),
        # u2 stops after the profile
        ("u2", "signup", "2026-07-01 10:00"),
        ("u2", "profile_completed", "2026-07-01 10:20"),
        # u3 only signs up
        ("u3", "signup", "2026-07-01 10:00"),
    ])
    out = analysis.funnel_counts(df)
    assert out["users"].tolist() == [3, 2, 1, 1, 1]
    assert out["step_conversion"].iloc[1] == pytest.approx(2 / 3)
    assert out["overall_conversion"].iloc[4] == pytest.approx(1 / 3)


def test_funnel_ignores_out_of_order_and_late_events():
    df = make_events([
        # u1 books before searching, so the search step is not reached in order
        ("u1", "signup", "2026-07-01 10:00"),
        ("u1", "profile_completed", "2026-07-01 10:10"),
        ("u1", "booking_started", "2026-07-01 11:00"),
        ("u1", "provider_search", "2026-07-01 12:00"),
        # u2 completes the profile after the 7-day window
        ("u2", "signup", "2026-07-01 10:00"),
        ("u2", "profile_completed", "2026-07-10 10:00"),
    ])
    out = analysis.funnel_counts(df)
    assert out["users"].tolist() == [2, 1, 1, 0, 0]


def test_last_step_by_group():
    df = make_events([
        ("u1", "signup", "2026-07-01 10:00"), ("u1", "profile_completed", "2026-07-01 10:05"),
        ("u1", "provider_search", "2026-07-01 10:10"), ("u1", "booking_started", "2026-07-01 10:20"),
        ("u1", "booking_completed", "2026-07-01 10:30"),
        ("u2", "signup", "2026-07-01 10:00"), ("u2", "profile_completed", "2026-07-01 10:05"),
        ("u2", "provider_search", "2026-07-01 10:10"), ("u2", "booking_started", "2026-07-01 10:20"),
    ])
    df.loc[df["distinct_id"] == "u2", "platform"] = "Android"
    out = analysis.last_step_by_group(df, "platform").set_index("platform")
    assert out.loc["iOS", "last_step_rate"] == 1.0
    assert out.loc["Android", "last_step_rate"] == 0.0


# ---------------- significance test ----------------
def test_ztest_known_values():
    # 50/100 vs 30/100: pooled p = 0.4, z = 0.2 / sqrt(0.4*0.6*0.02) = 2.8868
    res = analysis.two_proportion_ztest(50, 100, 30, 100)
    assert res["z"] == pytest.approx(2.8868, abs=1e-3)
    assert res["p_value"] == pytest.approx(0.0039, abs=1e-4)
    assert res["diff"] == pytest.approx(0.2)


def test_ztest_identical_groups_gives_p_one():
    res = analysis.two_proportion_ztest(30, 100, 30, 100)
    assert res["z"] == 0 and res["p_value"] == pytest.approx(1.0)


def test_ztest_rejects_empty_group():
    with pytest.raises(ValueError):
        analysis.two_proportion_ztest(1, 0, 1, 10)


# ---------------- retention ----------------
def test_retention_marks_unobserved_cells_as_nan():
    df = make_events([
        ("u1", "signup", "2026-07-06 10:00"),          # Monday, cohort week of Jul 6
        ("u1", "return_visit", "2026-07-14 10:00"),    # 8 days later -> week 1
        ("u2", "signup", "2026-07-06 12:00"),
        ("u3", "signup", "2026-09-28 10:00"),          # very recent cohort
    ])
    out = analysis.retention_by_signup_week(df, weeks=2, as_of="2026-09-30 23:59")
    first = out[out["cohort_week"] == pd.Timestamp("2026-07-06")].iloc[0]
    assert first["users"] == 2
    assert first["week_1"] == pytest.approx(0.5)
    recent = out[out["cohort_week"] == pd.Timestamp("2026-09-28")].iloc[0]
    assert math.isnan(recent["week_1"])  # not observable yet, must NOT show as 0%


# ---------------- reconciliation with Mixpanel ----------------
@pytest.mark.skipif(not os.path.exists("events.csv"), reason="events.csv not generated")
def test_funnel_matches_numbers_seen_in_mixpanel():
    df = analysis.load_events("events.csv")
    assert analysis.validate_events(df) == []
    out = analysis.funnel_counts(df)
    assert out["users"].tolist() == [6000, 4467, 3575, 2099, 1192]
