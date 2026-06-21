"""IDX Bandarmology dashboard.

Run with:
    streamlit run dashboard/app.py
"""

from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import streamlit as st

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "src"))

from idx_bandarmology import analysis, config, features, pipeline, storage  # noqa: E402


def format_signal(signal: str | None) -> str:
    if not signal or pd.isna(signal):
        return "-"
    return str(signal).replace("_", " ").title()


def format_rp(value: float | int | None) -> str:
    if value is None or pd.isna(value):
        return "-"
    value = float(value)
    sign = "-" if value < 0 else ""
    value = abs(value)
    if value >= 1e12:
        return f"{sign}Rp {value / 1e12:.2f} T"
    if value >= 1e9:
        return f"{sign}Rp {value / 1e9:.2f} B"
    if value >= 1e6:
        return f"{sign}Rp {value / 1e6:.2f} M"
    return f"{sign}Rp {value:,.0f}"


def format_pct(value: float | int | None) -> str:
    if value is None or pd.isna(value):
        return "-"
    return f"{float(value):+.2%}"


def clean_signal_table(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    out = df.copy()
    if "bandar_signal" in out:
        out["bandar_signal"] = out["bandar_signal"].map(format_signal)
    for col in ["foreign_net_broker", "local_net_broker", "total_value"]:
        if col in out:
            out[col] = out[col].map(format_rp)
    return out


def latest_price_moves(ticker_prices: pd.DataFrame) -> dict[str, float | None]:
    if ticker_prices.empty:
        return {"close": None, "return_1d": None, "return_5d": None, "return_10d": None}
    prices = ticker_prices.sort_values("date").copy()
    latest = prices.iloc[-1]
    out: dict[str, float | None] = {"close": latest["close"], "return_1d": None, "return_5d": None, "return_10d": None}
    for days in (1, 5, 10):
        if len(prices) > days:
            base = prices.iloc[-days - 1]["close"]
            out[f"return_{days}d"] = latest["close"] / base - 1 if base else None
    return out


def focused_ticker_diagnosis(
    ticker: str,
    ticker_prices: pd.DataFrame,
    ticker_flow: pd.DataFrame,
    ticker_activity: pd.DataFrame,
    scan_10d: pd.DataFrame,
    lookback: int,
) -> tuple[str, pd.DataFrame, pd.DataFrame]:
    """Build the plain-language answer shown at the top of the dashboard."""
    moves = latest_price_moves(ticker_prices)
    recent_cutoff = ticker_activity["date"].max() - pd.Timedelta(days=lookback) if not ticker_activity.empty else None
    recent_activity = ticker_activity[ticker_activity["date"] >= recent_cutoff].copy() if recent_cutoff is not None else pd.DataFrame()
    if recent_activity.empty:
        return (
            f"{ticker}: broker detail is not available for the selected lookback window.",
            pd.DataFrame(),
            pd.DataFrame(),
        )

    recent_net = (
        recent_activity.groupby(["broker_code", "participant_type"])
        .agg(
            days=("date", "nunique"),
            buy_value=("buy_value", "sum"),
            sell_value=("sell_value", "sum"),
            net_value=("net_value", "sum"),
        )
        .reset_index()
        .sort_values("net_value", ascending=False)
    )
    top_accumulators = recent_net[recent_net["net_value"] > 0].head(8).copy()

    model_rows = scan_10d[scan_10d["ticker"].eq(ticker)].copy() if "ticker" in scan_10d.columns else pd.DataFrame()
    significant = model_rows[model_rows["significant"].eq(True)].copy() if not model_rows.empty else pd.DataFrame()
    latest_flow = ticker_flow.sort_values("date").tail(1)
    latest_signal = format_signal(latest_flow.iloc[0]["bandar_signal"]) if not latest_flow.empty else "-"

    move_bits = []
    if moves["return_5d"] is not None:
        move_bits.append(f"5D {format_pct(moves['return_5d'])}")
    if moves["return_10d"] is not None:
        move_bits.append(f"10D {format_pct(moves['return_10d'])}")
    move_text = ", ".join(move_bits) if move_bits else "recent return unavailable"

    if significant.empty:
        verdict = (
            f"{ticker}: price move is {move_text}. Recent top accumulation exists, "
            f"but the model has not found a statistically significant broker-specific pattern yet. "
            f"Latest aggregate signal: {latest_signal}."
        )
    else:
        best = significant.sort_values(["p_value_one_sided", "mean_fwd_return"], ascending=[True, False]).iloc[0]
        verdict = (
            f"{ticker}: price move is {move_text}. The stronger evidence is broker-specific accumulation, "
            f"not only the aggregate bandar label. Best model row: broker {best['broker_code']} has "
            f"{int(best['n_events'])} events, mean 10D forward return {format_pct(best['mean_fwd_return'])}, "
            f"win rate {best['win_rate']:.0%}, p-value {best['p_value_one_sided']:.4f}. "
            f"Latest aggregate signal: {latest_signal}."
        )

    return verdict, top_accumulators, significant


st.set_page_config(page_title="IDX Bandarmology", layout="wide")

st.title("IDX Bandarmology")
st.caption(
    "Daily broker-flow evidence, event-study outcomes, and broker-combination screening "
    "from the local SQLite database."
)

with st.sidebar:
    st.header("Controls")

    watchlist_input = st.text_input(
        "Watchlist",
        value=",".join(config.WATCHLIST),
        help="Comma-separated IDX tickers without .JK, for example BBCA,BBRI,GOTO.",
    )
    watchlist = [ticker.strip().upper() for ticker in watchlist_input.split(",") if ticker.strip()]

    st.subheader("Data refresh")
    if st.button("Run latest pipeline", use_container_width=True):
        with st.spinner("Fetching latest prices and broker rows..."):
            result = pipeline.run(watchlist)
        st.success(
            f"Stored {result['n_prices']} price rows, {result['n_broker']} broker-flow rows, "
            f"and {result.get('n_activity', 0)} broker-activity rows."
        )
        st.rerun()

    default_end = date.today()
    default_start = default_end - timedelta(days=90)
    backfill_range = st.date_input(
        "Historical backfill range",
        value=(default_start, default_end),
        help="Loads historical market-detector rows for event studies and broker scans.",
    )
    if isinstance(backfill_range, tuple) and len(backfill_range) == 2:
        backfill_start, backfill_end = backfill_range
    else:
        backfill_start, backfill_end = default_start, default_end

    if st.button("Backfill historical data", use_container_width=True):
        if not config.get_broker_api_token():
            st.error("BROKER_API_TOKEN or STOCKBIT_TOKEN is not configured.")
        else:
            with st.spinner("Fetching historical broker-flow and per-broker rows..."):
                result = pipeline.backfill_broker_history(
                    watchlist,
                    start_date=backfill_start,
                    end_date=backfill_end,
                    price_period="1y",
                    refresh_prices=True,
                )
            st.success(
                f"Stored {result['n_broker']} broker-flow rows, "
                f"{result.get('n_activity', 0)} broker-activity rows, "
                f"and {result['n_prices']} price rows."
            )
            st.rerun()

    st.subheader("View settings")
    selected_ticker = st.selectbox("Focused ticker", watchlist)
    lookback_days = st.selectbox("Lookback window", [30, 60, 90, 180, 365], index=2)
    horizon = st.selectbox("Forward return horizon", [1, 3, 5, 10], index=2)
    min_events = st.number_input("Minimum broker events", min_value=2, max_value=30, value=5, step=1)
    min_net_buy_b = st.number_input("Minimum broker net buy, Rp B", min_value=0.0, value=0.0, step=0.5)

    runs = storage.read_runs()
    if not runs.empty:
        st.caption(f"Last run: {runs.iloc[0]['run_at']}")


price_df = storage.read_prices(watchlist)
broker_df = storage.read_broker_flow(watchlist)
activity_df = storage.read_broker_activity(watchlist)
feat = features.build_feature_table(watchlist)

if price_df.empty:
    st.info("No price data yet. Run the latest pipeline from the sidebar.")
    st.stop()

latest_price_date = price_df["date"].max()
latest_broker_date = broker_df["date"].max() if not broker_df.empty else None
latest_activity_date = activity_df["date"].max() if not activity_df.empty else None
latest_signals = broker_df[broker_df["date"] == latest_broker_date].copy() if latest_broker_date is not None else pd.DataFrame()
accumulation_count = 0
if not latest_signals.empty:
    accumulation_count = latest_signals["bandar_signal"].fillna("").str.contains("ACCUMULATION|AKUMULASI|NET_BUY").sum()

metric_cols = st.columns(5)
metric_cols[0].metric("Price rows", f"{len(price_df):,}")
metric_cols[1].metric("Broker-flow rows", f"{len(broker_df):,}")
metric_cols[2].metric("Broker-activity rows", f"{len(activity_df):,}")
metric_cols[3].metric("Latest broker date", latest_broker_date.strftime("%Y-%m-%d") if latest_broker_date is not None else "-")
metric_cols[4].metric("Accumulation names", f"{accumulation_count}")

if broker_df.empty:
    st.warning("Broker-flow data is empty. Use historical backfill to populate signals and broker details.")
    st.stop()

st.divider()

st.subheader("Focused ticker diagnosis")
focused_price = price_df[price_df["ticker"] == selected_ticker].copy()
focused_flow = broker_df[broker_df["ticker"] == selected_ticker].copy()
focused_activity = activity_df[activity_df["ticker"] == selected_ticker].copy()
focused_scan_10d = analysis.broker_alpha_scan(
    [selected_ticker],
    horizon=10,
    min_events=5,
    min_net_value=0,
    group_by=("ticker", "broker_code"),
)
diagnosis_text, top_accumulators, significant_brokers = focused_ticker_diagnosis(
    selected_ticker,
    focused_price,
    focused_flow,
    focused_activity,
    focused_scan_10d,
    lookback_days,
)

st.info(diagnosis_text)

diag_cols = st.columns([1, 1])
with diag_cols[0]:
    st.markdown("**Top recent accumulators**")
    if top_accumulators.empty:
        st.caption("No recent net-buy broker rows.")
    else:
        acc_view = top_accumulators.head(10).copy()
        for col in ["buy_value", "sell_value", "net_value"]:
            acc_view[col] = acc_view[col].map(format_rp)
        st.dataframe(
            acc_view.rename(columns={
                "broker_code": "Broker",
                "participant_type": "Type",
                "days": "Days",
                "buy_value": "Buy",
                "sell_value": "Sell",
                "net_value": "Net Buy",
            }),
            use_container_width=True,
            hide_index=True,
        )

with diag_cols[1]:
    st.markdown("**Model-supported brokers, 10D**")
    if significant_brokers.empty:
        st.caption("No broker passed the 10D significance filter.")
    else:
        sig_view = significant_brokers[[
            "broker_code", "n_events", "mean_fwd_return", "win_rate",
            "avg_net_value", "total_net_value", "p_value_one_sided",
        ]].head(10).copy()
        st.dataframe(
            sig_view.rename(columns={
                "broker_code": "Broker",
                "n_events": "Events",
                "mean_fwd_return": "Mean 10D",
                "win_rate": "Win Rate",
                "avg_net_value": "Avg Net Buy",
                "total_net_value": "Total Net Buy",
                "p_value_one_sided": "P Value",
            }).style.format({
                "Mean 10D": "{:+.2%}",
                "Win Rate": "{:.0%}",
                "Avg Net Buy": "Rp {:,.0f}",
                "Total Net Buy": "Rp {:,.0f}",
                "P Value": "{:.4f}",
            }),
            use_container_width=True,
            hide_index=True,
        )

st.divider()

st.subheader("Market snapshot")
snapshot_cols = [
    "ticker", "date", "bandar_signal", "bandar_signal_score",
    "foreign_net_broker", "local_net_broker", "total_value",
]
snapshot = latest_signals[[col for col in snapshot_cols if col in latest_signals.columns]]
st.dataframe(
    clean_signal_table(snapshot).rename(columns={
        "ticker": "Ticker",
        "date": "Date",
        "bandar_signal": "Signal",
        "bandar_signal_score": "Score",
        "foreign_net_broker": "Foreign Net",
        "local_net_broker": "Local Net",
        "total_value": "Value",
    }),
    use_container_width=True,
    hide_index=True,
)

st.divider()

st.subheader("Event study")
event_signal_filter = [
    "STRONG_ACCUMULATION", "ACCUMULATION", "NET_BUY",
    "AKUMULASI_KUAT", "AKUMULASI",
]
event_scope = st.radio(
    "Event scope",
    ["Focused ticker", "All watchlist"],
    horizontal=True,
)
event_tickers = [selected_ticker] if event_scope == "Focused ticker" else watchlist
event_table = analysis.event_study_table(
    tickers=event_tickers,
    horizons=(1, 3, 5, 10),
    lookback_days=lookback_days,
    signals=event_signal_filter,
)
st.pyplot(
    analysis.plot_event_study(
        tickers=event_tickers,
        horizons=(1, 3, 5, 10),
        lookback_days=lookback_days,
        aggregate=False,
        signals=event_signal_filter,
    ),
    use_container_width=True,
)

if event_table.empty:
    st.info("No accumulation events match this scope. Extend the backfill range or use all watchlist events.")
else:
    outcome_cols = [col for col in event_table.columns if col.startswith("t_plus_") and col != "t_plus_0d"]
    event_view = event_table.copy()
    event_view["bandar_signal"] = event_view["bandar_signal"].map(format_signal)
    for col in outcome_cols:
        event_view[col] = event_view[col].map(lambda value: value - 100 if pd.notna(value) else value)
    st.dataframe(
        event_view.rename(columns={
            "ticker": "Ticker",
            "signal_date": "Signal Date",
            "bandar_signal": "Signal",
            "bandar_signal_score": "Score",
            "t_plus_1d": "Return 1D",
            "t_plus_3d": "Return 3D",
            "t_plus_5d": "Return 5D",
            "t_plus_10d": "Return 10D",
        }).style.format({
            "Return 1D": "{:+.2f}%",
            "Return 3D": "{:+.2f}%",
            "Return 5D": "{:+.2f}%",
            "Return 10D": "{:+.2f}%",
        }, na_rep="-"),
        use_container_width=True,
        hide_index=True,
    )

st.divider()

st.subheader("Broker detail")
left, right = st.columns([1.2, 1])

with left:
    st.markdown(f"**{selected_ticker} price and signal history**")
    st.pyplot(analysis.plot_price_signal_panel(selected_ticker), use_container_width=True)

with right:
    st.markdown("**Broker distribution**")
    ticker_activity = activity_df[activity_df["ticker"] == selected_ticker].copy()
    if ticker_activity.empty:
        st.info("No per-broker rows for this ticker. Run historical backfill.")
    else:
        available_dates = sorted(ticker_activity["date"].dropna().dt.date.unique().tolist())
        selected_date = st.selectbox("Distribution date", available_dates, index=len(available_dates) - 1)
        st.pyplot(
            analysis.plot_broker_distribution(selected_ticker, trade_date=str(selected_date), top_n=12),
            use_container_width=True,
        )

        dist_table = analysis.broker_distribution_table(selected_ticker, trade_date=str(selected_date), top_n=15)
        if not dist_table.empty:
            dist_view = dist_table[[
                "broker_code", "participant_type", "buy_value", "sell_value", "net_value", "frequency",
            ]].copy()
            for col in ["buy_value", "sell_value", "net_value"]:
                dist_view[col] = dist_view[col].map(format_rp)
            st.dataframe(
                dist_view.rename(columns={
                    "broker_code": "Broker",
                    "participant_type": "Type",
                    "buy_value": "Buy",
                    "sell_value": "Sell",
                    "net_value": "Net",
                    "frequency": "Freq",
                }),
                use_container_width=True,
                hide_index=True,
            )

st.markdown("**Broker flow over time**")
if activity_df.empty:
    st.info("No broker-activity data available.")
else:
    ticker_activity = activity_df[activity_df["ticker"] == selected_ticker].copy()
    broker_options = sorted(ticker_activity["broker_code"].dropna().unique().tolist())
    default_brokers = (
        ticker_activity.assign(abs_net=lambda df: df["net_value"].abs())
        .groupby("broker_code")["abs_net"]
        .sum()
        .sort_values(ascending=False)
        .head(5)
        .index
        .tolist()
    )
    selected_brokers = st.multiselect("Broker codes", broker_options, default=default_brokers)
    st.pyplot(
        analysis.plot_broker_flow(selected_ticker, broker_codes=selected_brokers, lookback_days=lookback_days),
        use_container_width=True,
    )

st.divider()

st.subheader("Broker accumulation scanner")
st.caption(
    "This ranks cases where a broker repeatedly net-bought a ticker, then measures forward returns. "
    "The significant flag requires at least five events, positive average return, and one-sided p-value below 0.05."
)

group_choice = st.radio(
    "Scan mode",
    ["Ticker plus broker", "Broker across watchlist"],
    horizontal=True,
)
group_by = ("ticker", "broker_code") if group_choice == "Ticker plus broker" else ("broker_code",)
scan = analysis.broker_alpha_scan(
    watchlist,
    horizon=horizon,
    min_events=int(min_events),
    min_net_value=float(min_net_buy_b) * 1e9,
    group_by=group_by,
)

if scan.empty:
    st.info("No broker combinations have enough completed forward-return events under these settings.")
else:
    visible_cols = [
        col for col in [
            "ticker", "broker_code", "n_events", "mean_fwd_return", "median_fwd_return",
            "win_rate", "avg_net_value", "total_net_value", "t_stat", "p_value_one_sided",
            "significant", "status",
        ] if col in scan.columns
    ]
    scan_view = scan[visible_cols].copy()
    st.dataframe(
        scan_view.rename(columns={
            "ticker": "Ticker",
            "broker_code": "Broker",
            "n_events": "Events",
            "mean_fwd_return": "Mean Return",
            "median_fwd_return": "Median Return",
            "win_rate": "Win Rate",
            "avg_net_value": "Avg Net Buy",
            "total_net_value": "Total Net Buy",
            "t_stat": "T Stat",
            "p_value_one_sided": "P Value",
            "significant": "Significant",
            "status": "Status",
        }).style.format({
            "Mean Return": "{:+.2%}",
            "Median Return": "{:+.2%}",
            "Win Rate": "{:.0%}",
            "Avg Net Buy": "Rp {:,.0f}",
            "Total Net Buy": "Rp {:,.0f}",
            "T Stat": "{:.2f}",
            "P Value": "{:.4f}",
        }),
        use_container_width=True,
        hide_index=True,
    )

    significant_count = int(scan["significant"].sum()) if "significant" in scan.columns else 0
    if significant_count:
        st.success(f"{significant_count} broker combination(s) meet the current statistical filter.")
    else:
        st.warning("No combination meets the significance filter yet. Use a longer backfill range or broader watchlist.")

st.caption(
    f"Database: {storage.config.DB_PATH} | Latest price date: {latest_price_date:%Y-%m-%d} | "
    f"Latest broker-activity date: {latest_activity_date:%Y-%m-%d}" if latest_activity_date is not None
    else f"Database: {storage.config.DB_PATH} | Latest price date: {latest_price_date:%Y-%m-%d}"
)
