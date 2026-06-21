"""IDX Bandarmology dashboard — Streamlit.

Run with:
    streamlit run dashboard/app.py

Reads directly from the SQLite DB the pipeline writes to (data/db/bandarmology.sqlite).
No separate "load" step needed — just run the pipeline at least once first
(see notebooks/01_run_pipeline.ipynb or `python -m idx_bandarmology.pipeline`).
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
    "Smart money tracking untuk saham IDX — broker flow, bandar detector, dan uji hipotesis "
    "'apakah akumulasi bandar/asing memprediksi kenaikan harga'. Data: Stockbit (broker/bandar) + yfinance (harga)."
)

# ── sidebar: controls ────────────────────────────────────────────────────────
with st.sidebar:
    st.header("⚙️ Kontrol")

    all_tickers_in_db = sorted(storage.read_prices()["ticker"].unique().tolist()) if not storage.read_prices().empty else []
    default_watchlist = config.WATCHLIST

    watchlist_input = st.text_input(
        "Watchlist (pisahkan koma)",
        value=",".join(default_watchlist),
        help="Ticker IDX tanpa .JK, mis. BBCA,BBRI,GOTO",
    )
    watchlist = [t.strip().upper() for t in watchlist_input.split(",") if t.strip()]

    st.divider()
    run_clicked = st.button("🔄 Jalankan pipeline sekarang", use_container_width=True)
    if run_clicked:
        with st.spinner("Mengambil data dari yfinance + Stockbit..."):
            result = pipeline.run(watchlist)
        st.success(f"Selesai: {result['n_prices']} baris harga, {result['n_broker']} baris broker_flow.")
        st.rerun()

    st.divider()
    horizon = st.selectbox("Horizon forward return", [1, 3, 5, 10], index=2,
                            format_func=lambda d: f"{d} hari")
    target_col = f"fwd_return_{horizon}d"

    runs = storage.read_runs()
    if not runs.empty:
        st.caption(f"Pipeline terakhir dijalankan: {runs.iloc[0]['run_at']}")

    if not config.STOCKBIT_TOKEN:
        st.warning("STOCKBIT_TOKEN belum diset di .env — broker/bandar data tidak akan tersedia, "
                    "hanya data harga (yfinance).", icon="⚠️")

# ── load feature table ───────────────────────────────────────────────────────
feat = features.build_feature_table(watchlist)

if feat.empty:
    st.info("Belum ada data. Klik **Jalankan pipeline sekarang** di sidebar untuk mulai scraping.")
    st.stop()

tab_overview, tab_broker, tab_analysis, tab_model = st.tabs(
    ["📊 Overview", "🏦 Broker & Bandar", "🔍 Analisis Korelasi", "🤖 Modeling / Hipotesis"]
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
                emoji = "🟢" if "AKUMULASI" in str(signal) or signal == "NET_BUY" else \
                        "🔴" if "DISTRIBUSI" in str(signal) or signal == "NET_SELL" else "⚪"
                st.caption(f"{emoji} {signal}")

    st.subheader("Riwayat harga")
    pick = st.selectbox("Pilih ticker", watchlist)
    fig = analysis.plot_price_with_signal(feat, pick)
    st.pyplot(fig, use_container_width=True)

# ── tab: broker & bandar detail ────────────────────────────────────────────────
with tab_broker:
    st.subheader("Snapshot broker & bandar terbaru")
    broker_df = storage.read_broker_flow(watchlist)
    if broker_df.empty:
        st.info("Belum ada data broker/bandar. Pastikan STOCKBIT_TOKEN sudah diset, lalu jalankan pipeline.")
    else:
        latest_broker = broker_df.sort_values("date").groupby("ticker").tail(1)
        show_cols = ["ticker", "date", "bandar_signal", "foreign_net_broker", "local_net_broker",
                     "foreign_net_flow", "foreign_signal", "conclusion_broker"]
        st.dataframe(
            latest_broker[show_cols].rename(columns={
                "bandar_signal": "Sinyal Bandar", "foreign_net_broker": "Net Asing (broker)",
                "local_net_broker": "Net Lokal (broker)", "foreign_net_flow": "Net Asing (flow)",
                "foreign_signal": "Sinyal Asing", "conclusion_broker": "Kesimpulan",
            }),
            use_container_width=True, hide_index=True,
        )

        st.subheader("Detail per ticker")
        pick2 = st.selectbox("Pilih ticker", watchlist, key="broker_pick")
        row = latest_broker[latest_broker["ticker"] == pick2]
        if not row.empty:
            r = row.iloc[0]
            st.markdown(f"**{r['conclusion_broker'] or '-'}**")
            st.markdown(f"**{r['conclusion_flow'] or '-'}**")

# ── tab: correlation analysis ──────────────────────────────────────────────────
with tab_analysis:
    st.subheader(f"Korelasi sinyal smart money vs return {horizon} hari ke depan")
    corr = analysis.correlation_table(feat)
    if corr.empty:
        st.info("Belum cukup data untuk korelasi.")
    else:
        st.dataframe(corr.style.format("{:.3f}").background_gradient(cmap="RdYlGn", vmin=-0.5, vmax=0.5),
                     use_container_width=True)

    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**Distribusi return berdasarkan sinyal bandar**")
        fig2 = analysis.plot_signal_bucket_returns(feat, target_col=target_col)
        st.pyplot(fig2, use_container_width=True)
    with c2:
        st.markdown("**Scatter: skor sinyal vs forward return**")
        fig3 = analysis.plot_signal_vs_forward_return(feat, horizon=horizon)
        st.pyplot(fig3, use_container_width=True)

    st.subheader("Tabel ringkasan per sinyal")
    st.dataframe(analysis.summary_by_signal(feat, target_col=target_col), use_container_width=True, hide_index=True)

    st.subheader("Korelasi per ticker")
    st.dataframe(analysis.correlation_by_ticker(feat, target_col=target_col), use_container_width=True, hide_index=True)

# ── tab: modeling ────────────────────────────────────────────────────────────
with tab_model:
    st.subheader("Uji hipotesis: smart money -> kenaikan harga?")
    model_choice = st.radio("Model klasifikasi", ["logistic", "random_forest"], horizontal=True)

    reg = modeling.linear_regression(feat, target_col=target_col)
    clf = modeling.classify_up_down(feat, target_col=target_col, model_type=model_choice)

    st.markdown("#### 📐 Regresi linier (OLS)")
    if reg.coefficients.empty:
        st.warning(reg.summary_text)
    else:
        st.dataframe(
            reg.coefficients.style.format({"coef": "{:+.5f}", "std_err": "{:.5f}", "p_value": "{:.4f}"}),
            use_container_width=True, hide_index=True,
        )
        st.caption(f"n = {reg.n_obs}, R² = {reg.r_squared:.4f}")

    st.markdown("#### 🤖 Klasifikasi naik / tidak")
    if not pd.notna(clf.accuracy):
        st.warning(f"Belum cukup data untuk model klasifikasi (n={clf.n_obs}, butuh >=20).")
    else:
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Akurasi", f"{clf.accuracy:.1%}")
        m2.metric("Precision", f"{clf.precision:.1%}")
        m3.metric("Recall", f"{clf.recall:.1%}")
        m4.metric("ROC-AUC", f"{clf.roc_auc:.2f}" if clf.roc_auc else "-")
        st.dataframe(clf.feature_importance, use_container_width=True, hide_index=True)

    st.markdown("#### 📝 Kesimpulan")
    st.info(modeling.hypothesis_verdict(reg, clf))

    st.caption(
        "Catatan: dengan watchlist kecil dan histori pendek, hasil ini adalah titik awal eksplorasi, "
        "bukan sinyal trading siap pakai. Jalankan pipeline setiap hari bursa untuk menambah data historis "
        "dan hasil akan makin reliable."
    )
