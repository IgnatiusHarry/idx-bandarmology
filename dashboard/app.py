"""Compact Streamlit dashboard for IDX broker-flow analysis."""

from __future__ import annotations

import sys
from datetime import date, timedelta
from html import escape
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import streamlit as st

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "src"))

from idx_bandarmology import analysis, config, pipeline, storage
import time  # noqa: E402


PROFILE_META = {
    "smart_foreign": ("Foreign Smart Money", "Directional foreign institutions"),
    "local_institutional": ("Local Institutions", "Local institution-like accounts"),
    "market_maker": ("Market Makers", "Active on both sides; net position matters"),
    "bandar_gorengan": ("Speculative Operators", "Potential pump-and-dump participants"),
    "retail": ("Retail-Dominant", "Retail-heavy platforms"),
    "lainnya": ("Other Brokers", "Outside defined behavioral profiles"),
}
SMART_PROFILES = {"smart_foreign", "local_institutional"}


def fmt_signal(value: object) -> str:
    if value is None or pd.isna(value):
        return "-"
    mapping = {
        "AKUMULASI_KUAT": "Strong Accumulation",
        "AKUMULASI": "Accumulation",
        "DISTRIBUSI_KUAT": "Strong Distribution",
        "DISTRIBUSI": "Distribution",
        "NETRAL": "Neutral",
    }
    text = str(value)
    return mapping.get(text, text.replace("_", " ").title())


def fmt_rp(value: object) -> str:
    if value is None or pd.isna(value):
        return "-"
    n = float(value)
    sign = "-" if n < 0 else ""
    n = abs(n)
    if n >= 1e12:
        return f"{sign}Rp {n / 1e12:.2f} T"
    if n >= 1e9:
        return f"{sign}Rp {n / 1e9:.2f} B"
    if n >= 1e6:
        return f"{sign}Rp {n / 1e6:.2f} M"
    return f"{sign}Rp {n:,.0f}"


def fmt_pct(value: object) -> str:
    if value is None or pd.isna(value):
        return "-"
    return f"{float(value):+.2%}"


def participant_label(value: object) -> str:
    return {"Asing": "FOREIGN", "Lokal": "LOCAL", "Pemerintah": "GOV"}.get(str(value), str(value or "-"))


def signed_color(value: float) -> str:
    return "#10b981" if value >= 0 else "#e11d48"


def price_at_or_before(price_df: pd.DataFrame, ts: pd.Timestamp) -> pd.Series | None:
    sub = price_df[price_df["date"] <= ts].sort_values("date")
    if sub.empty:
        return None
    return sub.iloc[-1]


def return_to_date(price_df: pd.DataFrame, ts: pd.Timestamp, periods: int) -> float | None:
    sub = price_df[price_df["date"] <= ts].sort_values("date")
    if len(sub) <= periods:
        return None
    latest = sub.iloc[-1]["close"]
    base = sub.iloc[-periods - 1]["close"]
    return latest / base - 1 if base else None


def profile_flow_from_activity(activity: pd.DataFrame) -> pd.DataFrame:
    if activity.empty:
        return pd.DataFrame()
    df = activity.copy()
    df["profile"] = df["broker_code"].map(analysis.broker_profile_of)
    broker_rows = (
        df.groupby(["profile", "broker_code", "participant_type"], dropna=False)
        .agg(net=("net_value", "sum"), buy=("buy_value", "sum"), sell=("sell_value", "sum"))
        .reset_index()
    )
    rows = []
    for profile, (label, desc) in PROFILE_META.items():
        members = broker_rows[broker_rows["profile"] == profile].copy()
        if members.empty:
            continue
        total_net = float(members["net"].sum())
        members["abs_net"] = members["net"].abs()
        rows.append({
            "profile": profile,
            "label": label,
            "description": desc,
            "net": total_net,
            "top_brokers": members.sort_values("abs_net", ascending=False).head(6)[
                ["broker_code", "participant_type", "net"]
            ].to_dict("records"),
        })
    return pd.DataFrame(rows)


def smart_daily_from_activity(activity: pd.DataFrame) -> pd.DataFrame:
    if activity.empty:
        return pd.DataFrame()
    df = activity.copy()
    df["profile"] = df["broker_code"].map(analysis.broker_profile_of)
    df = df[df["profile"].isin(SMART_PROFILES)]
    if df.empty:
        return pd.DataFrame()
    daily = df.groupby("date")["net_value"].sum().reset_index(name="smart_net").sort_values("date")
    daily["cumulative_net"] = daily["smart_net"].cumsum()
    return daily


def render_metric_card(label: str, value: str, note: str = "", tone: str = "neutral") -> None:
    color = {"positive": "#10b981", "negative": "#e11d48", "warning": "#f59e0b"}.get(tone, "#e5e7eb")
    st.markdown(
        f"""
        <div class="metric-card">
            <div class="metric-label">{escape(label)}</div>
            <div class="metric-value" style="color:{color}">{escape(value)}</div>
            <div class="metric-note">{escape(note)}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_verdict(text: str) -> None:
    st.markdown(
        f"""
        <div class="verdict-panel">
            <div class="panel-kicker">Current read</div>
            <div class="verdict-text">{escape(text)}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_profile_flow(profile_df: pd.DataFrame) -> None:
    if profile_df.empty:
        st.caption("No broker-profile flow for this date window.")
        return
    max_abs = max(float(profile_df["net"].abs().max()), 1.0)
    html = ['<div class="profile-panel">']
    for row in profile_df.sort_values("net", ascending=False).itertuples():
        net = float(row.net)
        width = max(2, min(100, abs(net) / max_abs * 100))
        color = signed_color(net)
        chips = []
        for broker in row.top_brokers:
            b_net = float(broker.get("net") or 0)
            chips.append(
                '<span class="broker-chip">'
                f'{escape(str(broker.get("broker_code", "-")))}'
                f'<span>{escape(participant_label(broker.get("participant_type")))}</span>'
                f'<b style="color:{signed_color(b_net)}">{escape(fmt_rp(b_net))}</b>'
                '</span>'
            )
        html.append(
            '<div class="profile-row">'
            '<div class="profile-head">'
            f'<div><b>{escape(row.label)}</b><small>{escape(row.description)}</small></div>'
            f'<strong style="color:{color}">{escape(fmt_rp(net))}</strong>'
            '</div>'
            '<div class="bar-track">'
            f'<div class="bar-fill" style="width:{width:.1f}%; background:{color};"></div>'
            '</div>'
            f'<div class="chip-row">{"".join(chips)}</div>'
            '</div>'
        )
    html.append("</div>")
    st.markdown("".join(html), unsafe_allow_html=True)


def plot_price_context(price_df: pd.DataFrame, broker_df: pd.DataFrame, ticker: str, start_ts: pd.Timestamp, end_ts: pd.Timestamp) -> plt.Figure:
    px = price_df[(price_df["date"] >= start_ts) & (price_df["date"] <= end_ts)].sort_values("date")
    br = broker_df[(broker_df["date"] >= start_ts) & (broker_df["date"] <= end_ts)].sort_values("date")
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10.5, 5.2), sharex=True, gridspec_kw={"height_ratios": [3, 1]})
    if px.empty:
        ax1.text(0.5, 0.5, "No price rows in selected broker window.", ha="center", va="center")
        ax1.set_axis_off()
        ax2.set_axis_off()
        return fig
    ax1.plot(px["date"], px["close"], color="#64748b", linewidth=1.8, label="Close")
    if not br.empty:
        overlay = px.merge(br[["date", "bandar_signal_score"]], on="date", how="inner")
        colors = overlay["bandar_signal_score"].map({2: "#10b981", 1: "#84cc16", 0: "#94a3b8", -1: "#fb923c", -2: "#ef4444"}).fillna("#94a3b8")
        ax1.scatter(overlay["date"], overlay["close"], c=colors, s=38, zorder=4, label="Signal date")
        ax2.bar(overlay["date"], overlay["bandar_signal_score"], color=colors, width=1.2)
    ax1.set_title(f"{ticker} price and broker-signal window")
    ax1.grid(alpha=0.18)
    ax1.legend(loc="upper left", fontsize=8)
    ax2.axhline(0, color="#64748b", linewidth=0.8)
    ax2.set_yticks([-2, -1, 0, 1, 2])
    ax2.set_yticklabels(["Strong D", "D", "N", "A", "Strong A"], fontsize=8)
    ax2.grid(alpha=0.15)
    fig.autofmt_xdate()
    fig.tight_layout()
    return fig


def plot_smart_flow(daily: pd.DataFrame) -> plt.Figure:
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10.5, 4.2), sharex=True, gridspec_kw={"height_ratios": [2, 1]})
    if daily.empty:
        ax1.text(0.5, 0.5, "No smart-money flow in selected window.", ha="center", va="center")
        ax1.set_axis_off()
        ax2.set_axis_off()
        return fig
    colors = np.where(daily["smart_net"] >= 0, "#10b981", "#e11d48")
    ax1.bar(daily["date"], daily["smart_net"] / 1e9, color=colors, width=0.8)
    ax1.axhline(0, color="#64748b", linewidth=0.8)
    ax1.set_ylabel("Daily net, Rp B")
    ax1.grid(axis="y", alpha=0.15)
    ax2.plot(daily["date"], daily["cumulative_net"] / 1e9, color="#38bdf8", linewidth=1.8)
    ax2.axhline(0, color="#64748b", linewidth=0.8)
    ax2.set_ylabel("Cumulative")
    ax2.grid(axis="y", alpha=0.15)
    fig.autofmt_xdate()
    fig.tight_layout()
    return fig


def style_table(df: pd.DataFrame, money_cols: list[str] | None = None, pct_cols: list[str] | None = None):
    money_cols = money_cols or []
    pct_cols = pct_cols or []
    fmt = {col: fmt_rp for col in money_cols if col in df.columns}
    fmt.update({col: fmt_pct for col in pct_cols if col in df.columns})
    return df.style.format(fmt)


st.set_page_config(page_title="IDX Smart Money", layout="wide")

plt.rcParams.update({
    "figure.facecolor": "#ffffff",
    "axes.facecolor": "#ffffff",
    "savefig.facecolor": "#ffffff",
    "axes.edgecolor": "#dee2e6",
    "axes.labelcolor": "#6c757d",
    "axes.titlecolor": "#212529",
    "xtick.color": "#6c757d",
    "ytick.color": "#6c757d",
    "grid.color": "#f8f9fa",
    "text.color": "#212529",
    "legend.facecolor": "#ffffff",
    "legend.edgecolor": "#dee2e6",
    "legend.labelcolor": "#212529",
    "font.family": "sans-serif",
    "font.sans-serif": ["Inter", "Arial"],
})

st.markdown(
    """
    
    
    
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&display=swap');
    
    :root {
      --bg: #ffffff; --panel: #ffffff; --panel2: #f8f9fa; --line: #e9ecef;
      --muted: #6c757d; --text: #212529; --strong: #000000;
      --green: #198754; --red: #dc3545; --blue: #0d6efd; --amber: #ffc107;
    }
    html, body, [data-testid="stAppViewContainer"], [data-testid="stApp"] { 
        background: var(--bg); 
        color: var(--text); 
        font-family: 'Inter', -apple-system, sans-serif;
    }
    .block-container { max-width: 1200px; padding-top: 2rem; padding-bottom: 2rem; }
    [data-testid="stSidebar"] { background: #f8f9fa; border-right: 1px solid var(--line); }
    h1 { font-family: 'Inter', -apple-system, sans-serif; font-weight: 600; font-size: 1.5rem !important; color: var(--strong); margin-bottom: 0 !important; }
    h2, h3 { font-family: 'Inter', -apple-system, sans-serif; font-weight: 500; font-size: 1.1rem !important; color: var(--strong); margin-top: 1.5rem !important; border-bottom: 1px solid var(--line); padding-bottom: 0.2rem;}
    [data-testid="stCaptionContainer"], small { color: var(--muted) !important; font-family: 'Inter', sans-serif; }
    
    /* Interactive Elements */
    .stButton button { background: #ffffff; color: var(--text); border: 1px solid var(--line); border-radius: 4px; height: 2.2rem; font-weight: 500;}
    .stButton button:hover { border-color: var(--text); color: var(--strong); background: #f8f9fa; }
    
    .stSelectbox [data-baseweb="select"] > div, .stTextInput [data-baseweb="input"] > div,
    .stNumberInput [data-baseweb="input"] > div, .stDateInput [data-baseweb="input"] > div,
    .stMultiSelect [data-baseweb="select"] > div {
      background: #ffffff !important; border-color: var(--line) !important; border-radius: 4px !important; color: var(--text) !important; 
    }
    .stSelectbox [data-baseweb="select"] > div:hover { border-color: var(--muted) !important; }
    
    /* Tables and Cards */
    [data-testid="stDataFrame"] { border: 1px solid var(--line); border-radius: 4px; overflow: hidden; background: #ffffff; }
    
    div[data-testid="stMetric"], .metric-card, .verdict-panel, .profile-panel {
      background: #ffffff; border: 1px solid var(--line); border-radius: 4px; 
    }
    
    div[data-testid="stMetric"] { padding: 12px; min-height: 70px; border-left: 3px solid var(--line); border-top: none; border-bottom: none; border-right: none; border-radius: 0; }
    [data-testid="stMetricLabel"] { color: var(--muted); font-size: 0.75rem; font-weight: 500; text-transform: uppercase; }
    [data-testid="stMetricValue"] { color: var(--strong); font-size: 1.2rem; font-weight: 600; font-variant-numeric: tabular-nums; }
    
    .metric-card { padding: 12px; min-height: 70px; display: flex; flex-direction: column; justify-content: space-between; border-left: 3px solid var(--line); border-top: 1px solid var(--line); border-bottom: 1px solid var(--line); border-right: 1px solid var(--line); border-radius: 4px;}
    .metric-label, .panel-kicker { color: var(--muted); font-size: 0.7rem; font-weight: 500; text-transform: uppercase; margin-bottom: 6px; }
    .metric-value { font-size: 1.2rem; font-weight: 600; font-variant-numeric: tabular-nums; line-height: 1.2; color: var(--strong); }
    .metric-note { color: var(--muted); font-size: 0.7rem; margin-top: 4px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
    
    .verdict-panel { padding: 16px; margin: 12px 0; background: #f8f9fa; border-left: 3px solid var(--text); border-top: 1px solid var(--line); border-right: 1px solid var(--line); border-bottom: 1px solid var(--line); border-radius: 4px;}
    .verdict-text { line-height: 1.5; color: var(--text); font-size: 0.9rem; }
    
    .profile-panel { padding: 16px; border-radius: 4px; }
    .profile-row { padding: 10px 0; border-bottom: 1px solid var(--line); }
    .profile-row:last-child { border-bottom: 0; padding-bottom: 0; }
    .profile-row:first-child { padding-top: 0; }
    .profile-head { display: flex; justify-content: space-between; gap: 12px; align-items: flex-start; }
    .profile-head b { color: var(--strong); font-size: 0.9rem; font-weight: 600;}
    .profile-head small { display: block; font-size: 0.75rem; margin-top: 2px; color: var(--muted); }
    
    .bar-track { height: 4px; background: var(--line); border-radius: 0; margin: 8px 0; overflow: hidden; }
    .bar-fill { height: 100%; border-radius: 0; }
    
    .chip-row { display: flex; flex-wrap: wrap; gap: 6px; }
    .broker-chip { display: inline-flex; gap: 6px; align-items: center; padding: 2px 6px; background: #ffffff; border: 1px solid var(--line); border-radius: 4px; font-size: 0.7rem; color: var(--text); }
    .broker-chip span { color: var(--muted); font-size: 0.6rem; border: 1px solid var(--line); border-radius: 2px; padding: 1px 3px; background: #f8f9fa; }
    
    /* Tabs */
    div[data-testid="stTabs"] button { color: var(--muted); font-weight: 500; padding-bottom: 8px;}
    div[data-testid="stTabs"] button[aria-selected="true"] { color: var(--strong); border-bottom-color: var(--text); border-bottom-width: 2px; }
    div[data-testid="stTabs"] button:hover { color: var(--text); }
    
    hr { border-color: var(--line); margin: 1rem 0; }
    </style>



    """,
    unsafe_allow_html=True,
)


all_broker = storage.read_broker_flow()
all_activity = storage.read_broker_activity()
available_tickers = sorted(set(all_broker["ticker"].unique()).intersection(set(all_activity["ticker"].unique()))) if not all_broker.empty and not all_activity.empty else []

with st.sidebar:
    st.header("Workspace")
    if not available_tickers:
        st.warning("No ticker has both broker-flow and broker-activity history yet.")
        st.stop()

    default_watchlist = ",".join(available_tickers)
    watchlist_input = st.text_input("Universe", value=default_watchlist)
    watchlist = [t.strip().upper() for t in watchlist_input.split(",") if t.strip()]
    watchlist = [t for t in watchlist if t in available_tickers]
    if not watchlist:
        st.warning("The selected universe has no broker history.")
        st.stop()

    selected_ticker = st.selectbox("Ticker", watchlist)
    ticker_dates = sorted(all_activity[all_activity["ticker"] == selected_ticker]["date"].dt.date.unique().tolist())
    analysis_date = st.selectbox("Analysis date", ticker_dates, index=len(ticker_dates) - 1)
    analysis_ts = pd.Timestamp(analysis_date)

    lookback_days = st.selectbox("Window", [20, 30, 60, 90, 180], index=2, format_func=lambda x: f"{x} calendar days")
    horizon = st.selectbox("Validation horizon", [1, 3, 5, 10], index=3, format_func=lambda x: f"{x} trading days")
    min_events = st.number_input("Min broker events", min_value=3, max_value=30, value=5, step=1)
    min_net_buy_b = st.number_input("Min net buy, Rp B", min_value=0.0, value=0.0, step=0.5)

    st.divider()
    if st.button("Run latest pipeline", use_container_width=True):
        result = pipeline.run(watchlist)
        st.success(f"Stored {result['n_broker']} flow rows and {result.get('n_activity', 0)} activity rows.")
        st.rerun()

    backfill_range = st.date_input("Backfill range", value=(date.today() - timedelta(days=90), date.today()))
    if st.button("Backfill broker history", use_container_width=True):
        if isinstance(backfill_range, tuple) and len(backfill_range) == 2:
            result = pipeline.backfill_broker_history(watchlist, backfill_range[0], backfill_range[1], refresh_prices=True)
            st.success(f"Stored {result['n_broker']} flow rows and {result.get('n_activity', 0)} activity rows.")
            st.rerun()


window_start = analysis_ts - pd.Timedelta(days=lookback_days)
price_df = storage.read_prices([selected_ticker])
broker_df = storage.read_broker_flow([selected_ticker])
activity_df = storage.read_broker_activity([selected_ticker])

price_window = price_df[(price_df["date"] >= window_start) & (price_df["date"] <= analysis_ts)].copy()
broker_window = broker_df[(broker_df["date"] >= window_start) & (broker_df["date"] <= analysis_ts)].copy()
activity_window = activity_df[(activity_df["date"] >= window_start) & (activity_df["date"] <= analysis_ts)].copy()

if broker_window.empty or activity_window.empty:
    st.warning("No broker history exists inside the selected date window.")
    st.stop()

px_row = price_at_or_before(price_df, analysis_ts)
signal_row_df = broker_df[broker_df["date"] == analysis_ts].copy()
signal_row = signal_row_df.iloc[-1].to_dict() if not signal_row_df.empty else {}
top_buy, top_sell = analysis.top_net_broker_summary(selected_ticker, trade_date=analysis_ts, top_n=6)
daily_smart = smart_daily_from_activity(activity_window)
profile_df = profile_flow_from_activity(activity_window)
scan_10d = analysis.broker_alpha_scan([selected_ticker], horizon=10, min_events=5, min_net_value=0, group_by=("ticker", "broker_code"))
scan_h = analysis.broker_alpha_scan([selected_ticker], horizon=horizon, min_events=int(min_events), min_net_value=float(min_net_buy_b) * 1e9, group_by=("ticker", "broker_code"))
sig_10d = scan_10d[scan_10d["significant"].eq(True)].copy() if not scan_10d.empty else pd.DataFrame()

close_value = px_row["close"] if px_row is not None else np.nan
ret_5d = return_to_date(price_df, analysis_ts, 5)
ret_10d = return_to_date(price_df, analysis_ts, 10)
smart_cum = float(daily_smart["cumulative_net"].iloc[-1]) if not daily_smart.empty else np.nan
top_buyer = top_buy.iloc[0] if not top_buy.empty else None
top_seller = top_sell.iloc[0] if not top_sell.empty else None

if sig_10d.empty:
    verdict = (
        f"{selected_ticker} has {fmt_pct(ret_5d)} over 5D and {fmt_pct(ret_10d)} over 10D. "
        "Recent accumulation is visible, but no broker-specific 10D pattern clears the statistical filter yet."
    )
else:
    best = sig_10d.sort_values(["p_value_one_sided", "mean_fwd_return"], ascending=[True, False]).iloc[0]
    verdict = (
        f"{selected_ticker} has {fmt_pct(ret_5d)} over 5D and {fmt_pct(ret_10d)} over 10D. "
        f"Broker {best['broker_code']} is the strongest historical 10D signal: "
        f"{int(best['n_events'])} events, mean return {fmt_pct(best['mean_fwd_return'])}, "
        f"win rate {best['win_rate']:.0%}, p-value {best['p_value_one_sided']:.4f}."
    )

st.title("IDX Smart Money")
st.caption(f"{selected_ticker} | analysis date {analysis_ts:%Y-%m-%d} | broker-history window {window_start:%Y-%m-%d} to {analysis_ts:%Y-%m-%d}")

k1, k2, k3, k4, k5 = st.columns(5)
with k1:
    render_metric_card("Close", f"Rp {close_value:,.0f}" if pd.notna(close_value) else "-", f"as of {analysis_ts:%Y-%m-%d}")
with k2:
    render_metric_card("5D return", fmt_pct(ret_5d), "price window", "positive" if (ret_5d or 0) >= 0 else "negative")
with k3:
    render_metric_card("10D return", fmt_pct(ret_10d), "price window", "positive" if (ret_10d or 0) >= 0 else "negative")
with k4:
    render_metric_card("Aggregate signal", fmt_signal(signal_row.get("bandar_signal")), "selected date")
with k5:
    render_metric_card("Smart cumulative", fmt_rp(smart_cum), f"{len(daily_smart)} broker days", "positive" if (smart_cum or 0) >= 0 else "negative")

render_verdict(verdict)

overview_tab, flow_tab, causality_tab, validation_tab, raw_tab = st.tabs(["Overview", "Broker Flow", "Causality Insight", "Validation", "Raw Tables"])

with overview_tab:
    left, right = st.columns([1.25, 1])
    with left:
        st.subheader("Price and signal context")
        st.pyplot(plot_price_context(price_df, broker_df, selected_ticker, window_start, analysis_ts), use_container_width=True)
    with right:
        st.subheader("Selected-date broker summary")
        cols = st.columns(2)
        with cols[0]:
            render_metric_card("Top net buyer", str(top_buyer["broker_code"]) if top_buyer is not None else "-", fmt_rp(top_buyer["net_value"]) if top_buyer is not None else "", "positive")
        with cols[1]:
            render_metric_card("Top net seller", str(top_seller["broker_code"]) if top_seller is not None else "-", fmt_rp(top_seller["net_value"]) if top_seller is not None else "", "negative")
        buyers_view = top_buy[["broker_code", "participant_type", "net_value"]].rename(columns={"broker_code": "Broker", "participant_type": "Type", "net_value": "Net"})
        sellers_view = top_sell[["broker_code", "participant_type", "net_value"]].rename(columns={"broker_code": "Broker", "participant_type": "Type", "net_value": "Net"})
        for df in (buyers_view, sellers_view):
            if not df.empty:
                df["Type"] = df["Type"].map(participant_label)
        btab, stab = st.tabs(["Net Buy", "Net Sell"])
        with btab:
            st.dataframe(style_table(buyers_view, money_cols=["Net"]), use_container_width=True, hide_index=True)
        with stab:
            st.dataframe(style_table(sellers_view, money_cols=["Net"]), use_container_width=True, hide_index=True)

    st.subheader("Broker profile flow")
    render_profile_flow(profile_df)

with flow_tab:
    left, right = st.columns([1.45, 1])
    with left:
        st.subheader("Smart-money daily flow")
        st.pyplot(plot_smart_flow(daily_smart), use_container_width=True)
    with right:
        st.subheader("Price performance")
        perf = analysis.price_performance_table(selected_ticker)
        perf = perf[perf["timeframe"].isin(["1D", "1W", "1M", "3M", "6M", "YTD", "1Y", "3Y"])]
        pcols = st.columns(2)
        for i, (_idx, row) in enumerate(perf.iterrows()):
            value = row["return"]
            tone = "positive" if pd.notna(value) and value >= 0 else "negative"
            with pcols[i % 2]:
                render_metric_card(str(row["timeframe"]), fmt_pct(value), "", tone)

    st.subheader("Broker distribution on selected date")
    dist = analysis.broker_distribution_table(selected_ticker, trade_date=analysis_ts, top_n=20)
    if dist.empty:
        st.caption("No distribution rows for this date.")
    else:
        dist_view = dist[["broker_code", "participant_type", "buy_value", "sell_value", "net_value", "frequency"]].rename(columns={
            "broker_code": "Broker", "participant_type": "Type", "buy_value": "Buy", "sell_value": "Sell", "net_value": "Net", "frequency": "Freq",
        })
        dist_view["Type"] = dist_view["Type"].map(participant_label)
        st.dataframe(style_table(dist_view, money_cols=["Buy", "Sell", "Net"]), use_container_width=True, hide_index=True)


with causality_tab:
    st.subheader("Causality Insight (Granger Causality)")
    st.markdown("This causality test measures whether the activity of certain participants **precedes** and **predicts** the stock's price movement in the following days. (P-value < 0.05 means significant).")
    
    with st.spinner("Calculating causality..."):
        c_cols = st.columns([1, 1.5])
        
        with c_cols[0]:
            st.markdown("### Foreign Net Flow vs Price")
            foreign_causality = analysis.causality_foreign_vs_price(selected_ticker, max_lags=5)
            if foreign_causality:
                if foreign_causality['is_significant']:
                    render_metric_card("Foreign Influence", "Significant", f"P-value: {foreign_causality['min_p_value']:.4f} at lag {foreign_causality['best_lag']}", "positive")
                    st.success("There is statistical evidence that Foreign net flow drives this stock's price.")
                else:
                    render_metric_card("Foreign Influence", "Not Significant", f"Min p-value: {foreign_causality['min_p_value']:.4f}", "neutral")
                    st.info("Foreign net flow does not show a strong causal effect on price returns.")
            else:
                st.caption("Insufficient data to calculate aggregate foreign causality.")

            st.markdown("### Participant Type")
            part_causality = analysis.causality_by_participant(selected_ticker, max_lags=5)
            if not part_causality.empty:
                part_view = part_causality.rename(columns={"participant_type": "Participant", "best_lag": "Lag", "p_value": "P-Value", "significant": "Sig."})
                st.dataframe(
                    part_view.style.format({"P-Value": "{:.4f}"}).applymap(lambda x: 'background-color: #064e3b; color: #a7f3d0' if x is True else '', subset=["Sig."]),
                    use_container_width=True, hide_index=True
                )
            else:
                st.caption("Insufficient data.")

        with c_cols[1]:
            st.markdown("### Top Brokers Causality")
            broker_causality = analysis.causality_by_broker(selected_ticker, top_n=15, max_lags=5)
            if not broker_causality.empty:
                sig_brokers = broker_causality[broker_causality["significant"]]
                if not sig_brokers.empty:
                    st.success(f"Found {len(sig_brokers)} brokers whose movement significantly precedes the stock price.")
                else:
                    st.warning("No dominant broker's movement predicts the stock price in this window.")
                    
                b_view = broker_causality.rename(columns={"broker_code": "Broker", "best_lag": "Lag", "p_value": "P-Value", "significant": "Sig."})
                st.dataframe(
                    b_view.style.format({"P-Value": "{:.4f}"}).applymap(lambda x: 'background-color: #064e3b; color: #a7f3d0' if x is True else '', subset=["Sig."]),
                    use_container_width=True, hide_index=True
                )
            else:
                st.caption("Insufficient data to calculate individual broker causality.")


with validation_tab:
    st.subheader("Broker-specific return validation")
    if scan_h.empty:
        st.caption("No broker passes the current validation settings.")
    else:
        view = scan_h[[
            "broker_code", "n_events", "mean_fwd_return", "median_fwd_return",
            "win_rate", "avg_net_value", "total_net_value", "p_value_one_sided", "significant", "status",
        ]].rename(columns={
            "broker_code": "Broker", "n_events": "Events", "mean_fwd_return": "Mean Return",
            "median_fwd_return": "Median Return", "win_rate": "Win Rate", "avg_net_value": "Avg Net Buy",
            "total_net_value": "Total Net Buy", "p_value_one_sided": "P Value", "significant": "Significant", "status": "Status",
        })
        st.dataframe(
            view.style.format({
                "Mean Return": "{:+.2%}", "Median Return": "{:+.2%}", "Win Rate": "{:.0%}",
                "Avg Net Buy": "Rp {:,.0f}", "Total Net Buy": "Rp {:,.0f}", "P Value": "{:.4f}",
            }),
            use_container_width=True,
            hide_index=True,
        )

    st.subheader("Accumulation event study")
    event_table = analysis.event_study_table(
        tickers=[selected_ticker],
        horizons=(1, 3, 5, 10),
        lookback_days=lookback_days,
        signals=["STRONG_ACCUMULATION", "ACCUMULATION", "NET_BUY", "AKUMULASI_KUAT", "AKUMULASI"],
    )
    if event_table.empty:
        st.caption("No accumulation events in this window.")
    else:
        st.pyplot(
            analysis.plot_event_study(
                tickers=[selected_ticker],
                horizons=(1, 3, 5, 10),
                lookback_days=lookback_days,
                aggregate=False,
                signals=["STRONG_ACCUMULATION", "ACCUMULATION", "NET_BUY", "AKUMULASI_KUAT", "AKUMULASI"],
            ),
            use_container_width=True,
        )

with raw_tab:
    st.subheader("Window-level broker-flow rows")
    flow_view = broker_window[["date", "bandar_signal", "bandar_signal_score", "foreign_net_broker", "local_net_broker", "total_value"]].rename(columns={
        "date": "Date", "bandar_signal": "Signal", "bandar_signal_score": "Score",
        "foreign_net_broker": "Foreign Net", "local_net_broker": "Local Net", "total_value": "Value",
    })
    flow_view["Signal"] = flow_view["Signal"].map(fmt_signal)
    st.dataframe(style_table(flow_view, money_cols=["Foreign Net", "Local Net", "Value"]), use_container_width=True, hide_index=True)

    st.subheader("Window-level broker activity")
    activity_view = activity_window[["date", "broker_code", "participant_type", "buy_value", "sell_value", "net_value", "frequency"]].rename(columns={
        "date": "Date", "broker_code": "Broker", "participant_type": "Type",
        "buy_value": "Buy", "sell_value": "Sell", "net_value": "Net", "frequency": "Freq",
    })
    activity_view["Type"] = activity_view["Type"].map(participant_label)
    st.dataframe(style_table(activity_view, money_cols=["Buy", "Sell", "Net"]), use_container_width=True, hide_index=True)

st.caption(f"Database: {storage.config.DB_PATH}")
