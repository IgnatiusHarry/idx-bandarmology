"""Pipeline orchestrator — scrape -> clean -> store, one call to run it all.

This is the only module you typically need to call directly:

    from idx_bandarmology import pipeline
    pipeline.run(["BBCA", "BBRI", "GOTO"])          # explicit list
    pipeline.run()                                    # uses config.WATCHLIST

Each run:
  1. Pulls daily OHLCV from yfinance for every ticker (price history).
  2. Pulls today's broker/bandar snapshot for every ticker
     (skipped automatically if BROKER_API_TOKEN isn't set — prices still load).
  3. Cleans/flattens both into tidy tables.
  4. Upserts into SQLite (data/db/bandarmology.sqlite).
  5. Logs the run so you can see history in the dashboard.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import pandas as pd

from . import broker_api, config, prices, storage


def _broker_flow_rows(watchlist_results: dict) -> pd.DataFrame:
    """Flatten broker_api.fetch_watchlist() output into one tidy DataFrame.

    Uses *today* as the snapshot date — the broker/bandar endpoints
    return the latest completed trading day's numbers, not a date range, so
    each pipeline run captures one row per ticker per run-day. Running the
    pipeline daily builds up a time series naturally.
    """
    today = datetime.now(timezone.utc).date().isoformat()
    fetched_at = datetime.now(timezone.utc).isoformat()
    rows = []
    for sym, r in watchlist_results.items():
        if not r.get("available"):
            continue
        broker = r.get("broker") or {}
        fd = r.get("foreignDomestic") or {}
        rows.append({
            "date": broker.get("date") or fd.get("date") or today,
            "ticker": sym,
            "bandar_signal": broker.get("signal"),
            "bandar_signal_score": broker.get("signalScore"),
            "foreign_net_broker": broker.get("foreignNet"),
            "local_net_broker": broker.get("localNet"),
            "gov_net_broker": broker.get("govNet"),
            "foreign_net_flow": fd.get("netForeign"),
            "domestic_net_flow": fd.get("netDomestic"),
            "total_value": fd.get("totalValue"),
            "foreign_signal": fd.get("signal"),
            "conclusion_broker": broker.get("conclusion"),
            "conclusion_flow": fd.get("conclusion"),
            "fetched_at": fetched_at,
        })
    return pd.DataFrame(rows)


def run(
    tickers: list[str] | None = None,
    price_period: str = "1y",
    fetch_broker_data: bool = True,
) -> dict:
    """Run the full pipeline once. Returns a small summary dict.

    Parameters
    ----------
    tickers : list of plain tickers (e.g. ["BBCA", "BBRI"]). Defaults to
        ``config.WATCHLIST`` — edit that (or set the WATCHLIST env var) to
        change what gets scanned everywhere in the repo.
    price_period : yfinance period string, e.g. "1y", "6mo", "5y", "max".
    fetch_broker_data : set False to skip the broker API (e.g. no token configured,
        or you just want to refresh prices).
    """
    syms = [t.upper() for t in (tickers or config.WATCHLIST)]
    storage.init_db()

    print(f"[pipeline] watchlist: {syms}")

    # 1) prices
    print("[pipeline] fetching prices from yfinance...")
    price_df = prices.fetch_history_many(syms, period=price_period)
    n_prices = storage.upsert_prices(price_df)
    print(f"[pipeline]   -> {n_prices} price rows upserted")

    # 2) broker / bandar flow
    n_broker = 0
    n_activity = 0
    broker_results: dict = {}
    if fetch_broker_data and broker_api.is_available():
        print("[pipeline] fetching broker/bandar data...")
        broker_results = broker_api.fetch_watchlist(syms)
        broker_df = _broker_flow_rows(broker_results)
        n_broker = storage.upsert_broker_flow(broker_df)
        print(f"[pipeline]   -> {n_broker} broker_flow rows upserted")
        if not broker_df.empty:
            start = broker_df["date"].min()
            end = broker_df["date"].max()
            print("[pipeline] fetching per-broker distribution rows...")
            _, activity_df = broker_api.fetch_historical_broker_data(syms, start, end)
            n_activity = storage.upsert_broker_activity(activity_df)
            print(f"[pipeline]   -> {n_activity} broker_activity rows upserted")
    elif fetch_broker_data:
        print("[pipeline]   BROKER_API_TOKEN not set — skipping broker/bandar data "
              "(prices-only run). See .env.example.")

    notes = "ok" if (n_prices or n_broker) else "no data fetched"
    storage.log_run(syms, n_prices, n_broker, notes=notes)

    return {
        "tickers": syms,
        "n_prices": n_prices,
        "n_broker": n_broker,
        "n_activity": n_activity,
        "broker_results": broker_results,  # raw per-ticker dicts, handy in a notebook
    }


def backfill_broker_history(
    tickers: list[str] | None = None,
    start_date: str | date | datetime | None = None,
    end_date: str | date | datetime | None = None,
    price_period: str = "1y",
    refresh_prices: bool = True,
) -> dict:
    """Backfill historical broker/bandar rows for event-study analysis.

    Stockbit's marketdetectors endpoint accepts ``from`` and ``to`` dates, so
    this writes one broker_flow row per ticker/trading day in the selected range.
    Price history is refreshed first by default so post-signal outcomes can be
    calculated immediately in the dashboard.
    """
    if not broker_api.is_available():
        raise RuntimeError("BROKER_API_TOKEN/STOCKBIT_TOKEN is not configured.")
    if start_date is None or end_date is None:
        raise ValueError("start_date and end_date are required.")

    syms = [t.upper() for t in (tickers or config.WATCHLIST)]
    storage.init_db()

    n_prices = 0
    if refresh_prices:
        print("[pipeline] refreshing prices from yfinance...")
        price_df = prices.fetch_history_many(syms, period=price_period)
        n_prices = storage.upsert_prices(price_df)

    print(f"[pipeline] backfilling broker/bandar history for {syms} from {start_date} to {end_date}...")
    broker_df, activity_df = broker_api.fetch_historical_broker_data(syms, start_date, end_date)
    n_broker = storage.upsert_broker_flow(broker_df)
    n_activity = storage.upsert_broker_activity(activity_df)
    print(f"[pipeline]   -> {n_broker} historical broker_flow rows upserted")
    print(f"[pipeline]   -> {n_activity} historical broker_activity rows upserted")

    storage.log_run(
        syms,
        n_prices,
        n_broker,
        notes=f"historical broker backfill {start_date} to {end_date}; activity rows={n_activity}",
    )
    return {
        "tickers": syms,
        "start_date": str(start_date),
        "end_date": str(end_date),
        "n_prices": n_prices,
        "n_broker": n_broker,
        "n_activity": n_activity,
    }
