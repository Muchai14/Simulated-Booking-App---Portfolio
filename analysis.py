"""
Reusable analysis functions for the SIMULATED booking-app event data.

Keeping the logic in plain functions (instead of notebook cells) makes it
testable with pytest and reusable from the Streamlit app.
"""
import math

import pandas as pd

FUNNEL_STEPS = [
    "signup",
    "profile_completed",
    "provider_search",
    "booking_started",
    "booking_completed",
]
KNOWN_EVENTS = set(FUNNEL_STEPS) | {"session_attended", "return_visit"}
REQUIRED_COLUMNS = [
    "event", "distinct_id", "time", "platform", "acquisition_channel", "plan_type",
]


# --------------------------------------------------------------------------
# Loading and validation
# --------------------------------------------------------------------------
def load_events(path="events.csv"):
    """Read events.csv and parse the timestamp column as UTC datetimes."""
    df = pd.read_csv(path)
    df["time"] = pd.to_datetime(df["time"], utc=True)
    return df


def validate_events(df):
    """Run data-quality checks. Returns a list of problems (empty list = clean)."""
    problems = []

    missing_cols = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing_cols:
        # The remaining checks need these columns, so stop here.
        return [f"missing columns: {missing_cols}"]

    null_counts = df[REQUIRED_COLUMNS].isna().sum()
    for col, n in null_counts.items():
        if n > 0:
            problems.append(f"{n} null values in column '{col}'")

    unknown = sorted(set(df["event"].dropna()) - KNOWN_EVENTS)
    if unknown:
        problems.append(f"unknown event names: {unknown}")

    n_dupes = int(df.duplicated(subset=["event", "distinct_id", "time"]).sum())
    if n_dupes > 0:
        problems.append(f"{n_dupes} duplicate events (same event, user and time)")

    # Every user's events should happen at or after their signup.
    signup_time = (
        df[df["event"] == "signup"].groupby("distinct_id")["time"].min().rename("signup_time")
    )
    joined = df.join(signup_time, on="distinct_id")
    n_before = int((joined["time"] < joined["signup_time"]).sum())
    if n_before > 0:
        problems.append(f"{n_before} events happen before the user's signup")
    n_no_signup = int(joined["signup_time"].isna().sum())
    if n_no_signup > 0:
        problems.append(f"{n_no_signup} events belong to users with no signup event")

    return problems


# --------------------------------------------------------------------------
# Funnel
# --------------------------------------------------------------------------
def _reached_steps(df, steps=FUNNEL_STEPS, window_days=7):
    """Per user, which funnel steps were reached (in order, within the window).

    Returns a DataFrame indexed by distinct_id with one boolean column per step.
    A user reaches a step if they did it for the first time at or after the
    previous step, and within `window_days` of signup.
    """
    first_time = (
        df[df["event"].isin(steps)]
        .groupby(["distinct_id", "event"])["time"]
        .min()
        .unstack()
        .reindex(columns=steps)
    )
    # A step nobody reached becomes an all-NaN float column; force datetimes.
    for step in steps:
        first_time[step] = pd.to_datetime(first_time[step], utc=True)
    window = pd.Timedelta(days=window_days)
    reached = pd.DataFrame(index=first_time.index, columns=steps, dtype=bool)

    reached[steps[0]] = first_time[steps[0]].notna()
    for prev, step in zip(steps[:-1], steps[1:]):
        in_order = first_time[step] >= first_time[prev]
        in_window = (first_time[step] - first_time[steps[0]]) <= window
        reached[step] = reached[prev] & first_time[step].notna() & in_order & in_window
    return reached


def funnel_counts(df, steps=FUNNEL_STEPS, window_days=7):
    """Unique users per step plus step and overall conversion."""
    reached = _reached_steps(df, steps, window_days)
    users = reached.sum().astype(int)
    out = pd.DataFrame({"step": steps, "users": users.values})
    out["step_conversion"] = out["users"] / out["users"].shift(1)
    out["overall_conversion"] = out["users"] / out["users"].iloc[0]
    return out


def last_step_by_group(df, group_col, window_days=7):
    """booking_started -> booking_completed, split by a user property."""
    reached = _reached_steps(df, FUNNEL_STEPS, window_days)
    group = df.groupby("distinct_id")[group_col].first()
    tmp = reached.join(group)
    out = (
        tmp.groupby(group_col)
        .agg(
            signups=("signup", "sum"),
            started=("booking_started", "sum"),
            completed=("booking_completed", "sum"),
        )
        .astype(int)
    )
    out["last_step_rate"] = out["completed"] / out["started"]
    out["overall_rate"] = out["completed"] / out["signups"]
    return out.reset_index()


# --------------------------------------------------------------------------
# Significance test
# --------------------------------------------------------------------------
def two_proportion_ztest(x1, n1, x2, n2):
    """Two-sided z-test for the difference between two proportions (pooled).

    x = successes, n = trials. Uses the normal approximation, so it is only
    reliable when each group has a reasonable number of successes and failures.
    """
    if n1 <= 0 or n2 <= 0:
        raise ValueError("both groups need at least one observation")
    p1, p2 = x1 / n1, x2 / n2
    pooled = (x1 + x2) / (n1 + n2)
    se = math.sqrt(pooled * (1 - pooled) * (1 / n1 + 1 / n2))
    if se == 0:
        return {"p1": p1, "p2": p2, "diff": p1 - p2, "z": 0.0, "p_value": 1.0}
    z = (p1 - p2) / se
    p_value = math.erfc(abs(z) / math.sqrt(2))  # two-sided
    return {"p1": p1, "p2": p2, "diff": p1 - p2, "z": z, "p_value": p_value}


# --------------------------------------------------------------------------
# Retention
# --------------------------------------------------------------------------
def retention_by_signup_week(df, weeks=8, as_of=None):
    """Share of each weekly signup cohort that returned in week k after signup.

    Week k = days since signup // 7. Cells that cannot be fully observed yet
    (the cohort is too recent) are returned as NaN instead of a misleading low
    number. Cohort weeks start on Monday (UTC).
    """
    if as_of is None:
        as_of = df["time"].max()
    as_of = pd.Timestamp(as_of)
    if as_of.tzinfo is None:
        as_of = as_of.tz_localize("UTC")

    signups = (
        df[df["event"] == "signup"].groupby("distinct_id")["time"].min().rename("signup_time")
    )
    cohort = pd.DataFrame({"signup_time": signups})
    cohort["cohort_week"] = (
        cohort["signup_time"].dt.tz_localize(None).dt.to_period("W").dt.start_time
    )

    returns = df[df["event"] == "return_visit"].join(signups, on="distinct_id")
    returns["week_k"] = (returns["time"] - returns["signup_time"]).dt.days // 7
    returned = {k: set(returns.loc[returns["week_k"] == k, "distinct_id"]) for k in range(1, weeks + 1)}

    rows = []
    for cohort_week, grp in cohort.groupby("cohort_week"):
        users = list(grp.index)
        row = {"cohort_week": cohort_week, "users": len(users)}
        cohort_end = (cohort_week + pd.Timedelta(days=7)).tz_localize("UTC")
        for k in range(1, weeks + 1):
            fully_observed = cohort_end + pd.Timedelta(days=7 * (k + 1)) <= as_of
            if fully_observed:
                row[f"week_{k}"] = sum(u in returned[k] for u in users) / len(users)
            else:
                row[f"week_{k}"] = float("nan")
        rows.append(row)
    return pd.DataFrame(rows)
