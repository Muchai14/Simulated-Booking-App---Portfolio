"""
Simulated product-event data for a FICTIONAL appointment-booking app.

* ALL DATA IS SIMULATED. No real users, no health information.
* Generates: events.csv, events.jsonl (Mixpanel-style), users.csv
* Three patterns are deliberately embedded so there is something to find:
    1. paid_social brings many signups but converts to completed bookings less often.
    2. Android users drop off more between booking_started and booking_completed.
    3. Users who signed up during the week of 2026-08-10 return much less often
       (think: a bad release or onboarding change that week).

Run:  python generate_sim_data.py
"""
import json
import random
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

SEED = 42
N_USERS = 6000
START = datetime(2026, 7, 1, tzinfo=timezone.utc)
END = datetime(2026, 9, 30, 23, 59, tzinfo=timezone.utc)  # last day of data
BAD_WEEK_START = datetime(2026, 8, 10, tzinfo=timezone.utc)
BAD_WEEK_END = BAD_WEEK_START + timedelta(days=7)

random.seed(SEED)
rng = np.random.default_rng(SEED)

CHANNELS = ["organic_search", "paid_social", "referral", "partner_program"]
CHANNEL_W = [0.30, 0.35, 0.20, 0.15]          # paid_social is the biggest source
PLATFORMS = ["iOS", "Android", "Web"]
PLATFORM_W = [0.40, 0.35, 0.25]
PLANS = ["free", "standard", "premium"]
PLAN_W = [0.55, 0.35, 0.10]


def maybe(p):
    return random.random() < p


def add_event(rows, uid, name, ts, props):
    if ts > END:
        return
    rows.append({
        "event": name,
        "distinct_id": uid,
        "time": ts,
        "platform": props["platform"],
        "acquisition_channel": props["channel"],
        "plan_type": props["plan"],
    })


def main():
    users, events = [], []
    total_days = (END - START).days - 1

    for i in range(N_USERS):
        uid = f"u_{i:05d}"
        channel = random.choices(CHANNELS, CHANNEL_W)[0]
        platform = random.choices(PLATFORMS, PLATFORM_W)[0]
        plan = random.choices(PLANS, PLAN_W)[0]
        signup = START + timedelta(
            days=int(rng.integers(0, total_days)),
            seconds=int(rng.integers(0, 86400)),
        )
        props = {"channel": channel, "platform": platform, "plan": plan}
        users.append({"distinct_id": uid, "signup_time": signup, **{
            "acquisition_channel": channel, "platform": platform, "plan_type": plan}})

        add_event(events, uid, "signup", signup, props)

        # ---- funnel probabilities (base) ----
        p_profile, p_search, p_start, p_complete, p_attend = 0.75, 0.80, 0.60, 0.70, 0.85

        # Pattern 1: paid_social completes bookings less often
        if channel == "paid_social":
            p_complete -= 0.22
        elif channel == "referral":
            p_complete += 0.08
        # Pattern 2: Android drops more between start and complete
        if platform == "Android":
            p_complete -= 0.18

        t = signup
        if not maybe(p_profile):
            continue
        t += timedelta(minutes=int(rng.integers(2, 60)))
        add_event(events, uid, "profile_completed", t, props)

        if not maybe(p_search):
            continue
        t += timedelta(hours=float(rng.uniform(0.1, 30)))
        add_event(events, uid, "provider_search", t, props)

        if not maybe(p_start):
            continue
        t += timedelta(hours=float(rng.uniform(0.1, 48)))
        add_event(events, uid, "booking_started", t, props)

        if not maybe(max(p_complete, 0.05)):
            continue
        t += timedelta(minutes=int(rng.integers(3, 90)))
        add_event(events, uid, "booking_completed", t, props)

        attended = maybe(p_attend)
        if attended:
            t_session = t + timedelta(days=int(rng.integers(1, 8)),
                                      hours=int(rng.integers(0, 12)))
            add_event(events, uid, "session_attended", t_session, props)
        else:
            t_session = t

        # ---- return visits, weeks 1..8 after signup ----
        base_return = 0.60 if attended else 0.25
        # Pattern 3: signups in the "bad week" return far less
        if BAD_WEEK_START <= signup < BAD_WEEK_END:
            base_return *= 0.45
        for week in range(1, 9):
            p_week = base_return * (0.82 ** (week - 1))
            if maybe(p_week):
                ts = signup + timedelta(weeks=week, hours=int(rng.integers(0, 120)))
                add_event(events, uid, "return_visit", ts, props)

    ev = pd.DataFrame(events).sort_values("time").reset_index(drop=True)
    us = pd.DataFrame(users)

    ev_out = ev.copy()
    ev_out["time"] = ev_out["time"].dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    ev_out.to_csv("events.csv", index=False)
    us_out = us.copy()
    us_out["signup_time"] = us_out["signup_time"].dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    us_out.to_csv("users.csv", index=False)

    # Mixpanel-style JSON lines (check Mixpanel's current import docs before using)
    with open("events.jsonl", "w") as f:
        for n, r in ev.iterrows():
            f.write(json.dumps({
                "event": r["event"],
                "properties": {
                    "time": int(r["time"].timestamp()),
                    "distinct_id": r["distinct_id"],
                    "$insert_id": f"sim-{n}",
                    "platform": r["platform"],
                    "acquisition_channel": r["acquisition_channel"],
                    "plan_type": r["plan_type"],
                },
            }) + "\n")

    print(f"users: {len(us):,}   events: {len(ev):,}")
    print(ev["event"].value_counts().to_string())


if __name__ == "__main__":
    main()
