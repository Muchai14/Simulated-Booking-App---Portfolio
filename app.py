"""
Streamlit app for the SIMULATED booking-app events.

Run:  streamlit run app.py
All calculations live in analysis.py (which has unit tests); this file only
handles the page layout.
"""
import pandas as pd
import streamlit as st

import analysis

st.set_page_config(page_title="Booking app analytics (simulated)", layout="wide")
st.title("Booking app: funnel and retention")
st.caption("All data is simulated. Three patterns were designed into it, so this "
           "app demonstrates the method, not real findings.")


@st.cache_data
def get_events():
    return analysis.load_events("events.csv")


events = get_events()

# ---------------- data quality (always on the full dataset) ----------------
problems = analysis.validate_events(events)
if problems:
    st.error("Data quality problems: " + "; ".join(problems))
else:
    st.success(f"Data quality checks passed ({len(events):,} events, "
               f"{events['distinct_id'].nunique():,} users).")

# ---------------- filters ----------------
st.sidebar.header("Filters")
channels = sorted(events["acquisition_channel"].unique())
platforms = sorted(events["platform"].unique())
sel_channels = st.sidebar.multiselect("Acquisition channel", channels, default=channels)
sel_platforms = st.sidebar.multiselect("Platform", platforms, default=platforms)

df = events[events["acquisition_channel"].isin(sel_channels) & events["platform"].isin(sel_platforms)]
if df.empty:
    st.warning("No events match these filters.")
    st.stop()

# ---------------- funnel ----------------
st.header("Signup to booking funnel")
funnel = analysis.funnel_counts(df)
left, right = st.columns([1, 1])
with left:
    show = funnel.copy()
    show["step_conversion"] = show["step_conversion"].map(lambda v: "" if pd.isna(v) else f"{v:.1%}")
    show["overall_conversion"] = show["overall_conversion"].map(lambda v: f"{v:.1%}")
    st.dataframe(show, hide_index=True)
with right:
    st.bar_chart(funnel.set_index("step")["users"])
st.caption("Unique users, steps in order, 7-day window from signup.")

# ---------------- last step by group ----------------
st.header("Last step: booking_started to booking_completed")
tab_channel, tab_platform = st.tabs(["By channel", "By platform"])
for tab, col in [(tab_channel, "acquisition_channel"), (tab_platform, "platform")]:
    with tab:
        table = analysis.last_step_by_group(df, col)
        view = table.copy()
        view["last_step_rate"] = view["last_step_rate"].map(lambda v: f"{v:.1%}")
        view["overall_rate"] = view["overall_rate"].map(lambda v: f"{v:.1%}")
        st.dataframe(view, hide_index=True)
        st.bar_chart(table.set_index(col)["last_step_rate"])

# ---------------- significance test ----------------
st.header("Compare two channels (z-test)")
by_channel = analysis.last_step_by_group(events, "acquisition_channel").set_index("acquisition_channel")
c1, c2 = st.columns(2)
a = c1.selectbox("Channel A", list(by_channel.index), index=0)
b = c2.selectbox("Channel B", list(by_channel.index), index=1)
if a == b:
    st.info("Pick two different channels.")
else:
    ra, rb = by_channel.loc[a], by_channel.loc[b]
    res = analysis.two_proportion_ztest(int(ra["completed"]), int(ra["started"]),
                                        int(rb["completed"]), int(rb["started"]))
    st.write(f"**{a}:** {res['p1']:.1%} ({int(ra['completed'])}/{int(ra['started'])})   |   "
             f"**{b}:** {res['p2']:.1%} ({int(rb['completed'])}/{int(rb['started'])})")
    st.write(f"Difference {res['diff']:+.1%}, z = {res['z']:.2f}, p-value = {res['p_value']:.2g}")
    st.caption("Two-sided z-test for two proportions on the full dataset (filters above are not "
               "applied). The patterns are simulated, so a small p-value only confirms the "
               "generator did what it was told.")

# ---------------- retention ----------------
st.header("Retention by signup week")
retention = analysis.retention_by_signup_week(df)
view = retention.copy()
view["cohort_week"] = view["cohort_week"].dt.strftime("%Y-%m-%d")
for col in [c for c in view.columns if c.startswith("week_")]:
    view[col] = view[col].map(lambda v: "incomplete" if pd.isna(v) else f"{v:.1%}")
st.dataframe(view, hide_index=True)
st.caption("Share of each signup cohort that returned in week k after signup. Cells that cannot "
           "be fully observed yet are shown as 'incomplete' instead of a misleading low number. "
           "Weeks start on Monday (UTC).")
