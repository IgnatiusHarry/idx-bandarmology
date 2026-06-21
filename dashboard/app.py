"""IDX Bandarmology dashboard — Streamlit.

Run with:
    streamlit run dashboard/app.py

Reads directly from the SQLite DB the pipeline writes to (data/db/bandarmology.sqlite).
No separate "load" step needed — just run the pipeline at least once first
(see notebooks/01_bandarmology_end_to_end.ipynb or `python -m idx_bandarmology.pipeline`).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import streamlit as st

# Make `src/` importable when run as `streamlit run dashboard/app.py` from repo root.
_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "src"))

from idx_bandarmology import analysis, config, features, modeling, pipeline, storage  # noqa: E402

st.set_page_config(page_title="IDX Bandarmology", page_icon="📈", layout="wide")

st.title("📈 IDX Bandarmology Dashboard")
st.caption(
    "Smart-money tracking for IDX stocks — broker flow, bandar detector, and the hypothesis test "
    "'does bandar/foreign accumulation predict a price increase?'. Data: Stockbit (broker/bandar) + yfinance (prices)."
)

# ── sidebar: controls ────────────────────────────────────────────────────────
with st.sidebar:
    st.header("⚙️ Controls")

    all_tickers_in_db = sorted(storage.read_prices()["ticker"].unique().tolist()) if not storage.read_prices().empty else []
    default_watchlist = config.WATCHLIST

    watchlist_input = st.text_input(
        "Watchlist (comma-separated)",
        value=",".join(default_watchlist),
        help="IDX tickers without .JK, e.g. BBCA,BBRI,GOTO",
    )
    watchlist = [t.strip().upper() for t in watchlist_input.split(",") if t.strip()]

    st.divider()
    run_clicked = st.button("🔄 Run pipeline now", use_container_width=True)
    if run_clicked:
        with st.spinner("Fetching data from yfinance + Stockbit..."):
            result = pipeline.run(watchlist)
        st.success(f"Done: {result['n_prices']} price rows, {result['n_broker']} broker_flow rows.")
        st.rerun()

    st.divider()
    horizon = st.selectbox("Forward return horizon", [1, 3, 5, 10], index=2,
                            format_func=lambda d: f"{d} days")
    target_col = f"fwd_return_{horizon}d"

    runs = storage.read_runs()
    if not runs.empty:
        st.caption(f"Pipeline last run: {runs.iloc[0]['run_at']}")

    if not config.STOCKBIT_TOKEN:
        st.warning("STOCKBIT_TOKEN is not set in .env — broker/bandar data will be unavailable, "
                    "only price data (yfinance).", icon="⚠️")

# ── load feature table ───────────────────────────────────────────────────────
feat = features.build_feature_table(watchlist)

if feat.empty:
    st.info("No data yet. Click **Run pipeline now** in the sidebar to start scraping.")
    st.stop()

tab_overview, tab_broker, tab_analysis, tab_model = st.tabs(
    ["📊 Overview", "🏦 Broker & Bandar", "🔍 Correlation Analysis", "🤖 Modeling / Hypothesis"]
)

# ── tab: overview ─────────────────────────────────────────────────────────────
with tab_overview:
    latest = feat.sort_values("date").groupby("ticker").tail(1)
    cols = st.columns(len(watchlist) if len(watchlist) <= 6 else 6)
    for i, row in enumerate(latest.itertuples()):
        with cols[i % len(cols)]:
            close = getattr(row, "close", None)
            ret1d = getattr(row, "return_1d", None)
            signal = getattr(row, "bandar_signal", None)
            st.metric(
                label=row.ticker,
                value=f"Rp {close:,.0f}" if pd.notna(close) else "-",
                delta=f"{ret1d*100:+.2f}%" if pd.notna(ret1d) else None,
            )
            if pd.notna(signal):
                emoji = "🟢" if "ACCUMULATION" in str(signal) or signal == "NET_BUY" else \
                        "🔴" if "DISTRIBUTION" in str(signal) or signal == "NET_SELL" else "⚪"
                st.caption(f"{emoji} {signal}")

    st.subheader("Price history")
    pick = st.selectbox("Select ticker", watchlist)
    fig = analysis.plot_price_with_signal(feat, pick)
    st.pyplot(fig, use_container_width=True)

# ── tab: broker & bandar detail ────────────────────────────────────────────────
with tab_broker:
    st.subheader("Latest broker & bandar snapshot")
    broker_df = storage.read_broker_flow(watchlist)
    if broker_df.empty:
        st.info("No broker/bandar data yet. Make sure STOCKBIT_TOKEN is set, then run the pipeline.")
    else:
        latest_broker = broker_df.sort_values("date").groupby("ticker").tail(1)
        show_cols = ["ticker", "date", "bandar_signal", "foreign_net_broker", "local_net_broker",
                     "foreign_net_flow", "foreign_signal", "conclusion_broker"]
        st.dataframe(
            latest_broker[show_cols].rename(columns={
                "bandar_signal": "Bandar Signal", "foreign_net_broker": "Foreign Net (broker)",
                "local_net_broker": "Local Net (broker)", "foreign_net_flow": "Foreign Net (flow)",
                "foreign_signal": "Foreign Signal", "conclusion_broker": "Conclusion",
            }),
            use_container_width=True, hide_index=True,
        )

        st.subheader("Per-ticker detail")
        pick2 = st.selectbox("Select ticker", watchlist, key="broker_pick")
        row = latest_broker[latest_broker["ticker"] == pick2]
        if not row.empty:
            r = row.iloc[0]
            st.markdown(f"**{r['conclusion_broker'] or '-'}**")
            st.markdown(f"**{r['conclusion_flow'] or '-'}**")

# ── tab: correlation analysis ──────────────────────────────────────────────────
with tab_analysis:
    st.subheader(f"Correlation: smart-money signals vs {horizon}-day forward return")
    corr = analysis.correlation_table(feat)
    if corr.empty:
        st.info("Not enough data for correlation.")
    else:
        st.dataframe(corr.style.format("{:.3f}").background_gradient(cmap="RdYlGn", vmin=-0.5, vmax=0.5),
                     use_container_width=True)

    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**Return distribution by bandar signal**")
        fig2 = analysis.plot_signal_bucket_returns(feat, target_col=target_col)
        st.pyplot(fig2, use_container_width=True)
    with c2:
        st.markdown("**Scatter: signal score vs forward return**")
        fig3 = analysis.plot_signal_vs_forward_return(feat, horizon=horizon)
        st.pyplot(fig3, use_container_width=True)

    st.subheader("Summary table by signal")
    st.dataframe(analysis.summary_by_signal(feat, target_col=target_col), use_container_width=True, hide_index=True)

    st.subheader("Correlation by ticker")
    st.dataframe(analysis.correlation_by_ticker(feat, target_col=target_col), use_container_width=True, hide_index=True)

# ── tab: modeling ────────────────────────────────────────────────────────────
with tab_model:
    st.subheader("Hypothesis test: does smart money -> price increase?")
    model_choice = st.radio("Classification model", ["logistic", "random_forest"], horizontal=True)

    reg = modeling.linear_regression(feat, target_col=target_col)
    clf = modeling.classify_up_down(feat, target_col=target_col, model_type=model_choice)

    st.markdown("#### 📐 Linear regression (OLS)")
    if reg.coefficients.empty:
        st.warning(reg.summary_text)
    else:
        st.dataframe(
            reg.coefficients.style.format({"coef": "{:+.5f}", "std_err": "{:.5f}", "p_value": "{:.4f}"}),
            use_container_width=True, hide_index=True,
        )
        st.caption(f"n = {reg.n_obs}, R² = {reg.r_squared:.4f}")

    st.markdown("#### 🤖 Up / not-up classification")
    if not pd.notna(clf.accuracy):
        st.warning(f"Not enough data for the classification model (n={clf.n_obs}, need >=20).")
    else:
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Accuracy", f"{clf.accuracy:.1%}")
        m2.metric("Precision", f"{clf.precision:.1%}")
        m3.metric("Recall", f"{clf.recall:.1%}")
        m4.metric("ROC-AUC", f"{clf.roc_auc:.2f}" if clf.roc_auc else "-")
        st.dataframe(clf.feature_importance, use_container_width=True, hide_index=True)

    st.markdown("#### 📝 Verdict")
    st.info(modeling.hypothesis_verdict(reg, clf))

    st.caption(
        "Note: with a small watchlist and short history, this output is a starting point for "
        "exploration, not a ready-to-trade signal. Run the pipeline every trading day to accumulate "
        "more historical data and the results will become more reliable."
    )
