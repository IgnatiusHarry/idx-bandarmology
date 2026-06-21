"""SQLite storage — the landing zone for the pipeline.

Why SQLite (not just CSV)? It's a single file (``data/db/bandarmology.sqlite``)
that:
  * Streamlit can query directly with plain SQL / pandas.read_sql.
  * Metabase (or DBeaver, or anything) can also open later with zero setup —
    just point it at the file.
  * Still lets you inspect/export to CSV any time via pandas.

Tables
------
prices          : daily OHLCV per ticker (from yfinance)
broker_flow     : daily broker-flow snapshot per ticker
broker_activity : daily per-broker buy/sell/net rows per ticker
runs            : log of each pipeline run, for traceability
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Iterator

import pandas as pd

from . import config

_SCHEMA = """
CREATE TABLE IF NOT EXISTS prices (
    date    TEXT NOT NULL,
    ticker  TEXT NOT NULL,
    open    REAL,
    high    REAL,
    low     REAL,
    close   REAL,
    volume  REAL,
    PRIMARY KEY (date, ticker)
);

CREATE TABLE IF NOT EXISTS broker_flow (
    date                TEXT NOT NULL,
    ticker              TEXT NOT NULL,
    bandar_signal       TEXT,
    bandar_signal_score REAL,
    foreign_net_broker  REAL,   -- from broker summary (foreign net)
    local_net_broker    REAL,
    gov_net_broker      REAL,
    foreign_net_flow    REAL,   -- from foreign-vs-domestic flow chart
    domestic_net_flow   REAL,
    total_value         REAL,
    foreign_signal      TEXT,
    conclusion_broker   TEXT,
    conclusion_flow     TEXT,
    fetched_at          TEXT,
    PRIMARY KEY (date, ticker)
);

CREATE TABLE IF NOT EXISTS broker_activity (
    date             TEXT NOT NULL,
    ticker           TEXT NOT NULL,
    broker_code      TEXT NOT NULL,
    participant_type TEXT,
    buy_value        REAL,
    sell_value       REAL,
    net_value        REAL,
    buy_lot          REAL,
    sell_lot         REAL,
    frequency        REAL,
    buy_avg_price    REAL,
    sell_avg_price   REAL,
    fetched_at       TEXT,
    PRIMARY KEY (date, ticker, broker_code)
);

CREATE TABLE IF NOT EXISTS runs (
    run_at      TEXT NOT NULL,
    tickers     TEXT,
    n_prices    INTEGER,
    n_broker    INTEGER,
    notes       TEXT
);
"""


@contextmanager
def get_conn() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(config.DB_PATH)
    try:
        yield conn
    finally:
        conn.close()


def init_db() -> None:
    """Create tables if they don't exist yet. Safe to call every run."""
    with get_conn() as conn:
        conn.executescript(_SCHEMA)
        conn.commit()


def upsert_prices(df: pd.DataFrame) -> int:
    """Insert/replace rows into ``prices``. Returns rows written."""
    if df.empty:
        return 0
    df = df.copy()
    df["date"] = df["date"].astype(str)
    with get_conn() as conn:
        rows = df[["date", "ticker", "open", "high", "low", "close", "volume"]].values.tolist()
        conn.executemany(
            """INSERT INTO prices (date, ticker, open, high, low, close, volume)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(date, ticker) DO UPDATE SET
                 open=excluded.open, high=excluded.high, low=excluded.low,
                 close=excluded.close, volume=excluded.volume""",
            rows,
        )
        conn.commit()
    return len(df)


def upsert_broker_flow(df: pd.DataFrame) -> int:
    """Insert/replace rows into ``broker_flow``. Returns rows written."""
    if df.empty:
        return 0
    df = df.copy()
    df["date"] = df["date"].astype(str)
    cols = [
        "date", "ticker", "bandar_signal", "bandar_signal_score",
        "foreign_net_broker", "local_net_broker", "gov_net_broker",
        "foreign_net_flow", "domestic_net_flow", "total_value",
        "foreign_signal", "conclusion_broker", "conclusion_flow", "fetched_at",
    ]
    for c in cols:
        if c not in df.columns:
            df[c] = None
    with get_conn() as conn:
        rows = df[cols].values.tolist()
        placeholders = ", ".join("?" * len(cols))
        updates = ", ".join(f"{c}=excluded.{c}" for c in cols if c not in ("date", "ticker"))
        conn.executemany(
            f"""INSERT INTO broker_flow ({', '.join(cols)}) VALUES ({placeholders})
                ON CONFLICT(date, ticker) DO UPDATE SET {updates}""",
            rows,
        )
        conn.commit()
    return len(df)


def upsert_broker_activity(df: pd.DataFrame) -> int:
    """Insert/replace per-broker activity rows. Returns rows written."""
    if df.empty:
        return 0
    df = df.copy()
    df["date"] = df["date"].astype(str)
    cols = [
        "date", "ticker", "broker_code", "participant_type",
        "buy_value", "sell_value", "net_value",
        "buy_lot", "sell_lot", "frequency",
        "buy_avg_price", "sell_avg_price", "fetched_at",
    ]
    for c in cols:
        if c not in df.columns:
            df[c] = None
    with get_conn() as conn:
        rows = df[cols].values.tolist()
        placeholders = ", ".join("?" * len(cols))
        updates = ", ".join(f"{c}=excluded.{c}" for c in cols if c not in ("date", "ticker", "broker_code"))
        conn.executemany(
            f"""INSERT INTO broker_activity ({', '.join(cols)}) VALUES ({placeholders})
                ON CONFLICT(date, ticker, broker_code) DO UPDATE SET {updates}""",
            rows,
        )
        conn.commit()
    return len(df)


def log_run(tickers: list[str], n_prices: int, n_broker: int, notes: str = "") -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO runs (run_at, tickers, n_prices, n_broker, notes) VALUES (?, ?, ?, ?, ?)",
            (datetime.now(timezone.utc).isoformat(), ",".join(tickers), n_prices, n_broker, notes),
        )
        conn.commit()


def read_prices(tickers: list[str] | None = None) -> pd.DataFrame:
    init_db()
    q = "SELECT * FROM prices"
    params: tuple = ()
    if tickers:
        q += f" WHERE ticker IN ({','.join('?' * len(tickers))})"
        params = tuple(t.upper() for t in tickers)
    with get_conn() as conn:
        df = pd.read_sql(q, conn, params=params, parse_dates=["date"])
    return df.sort_values(["ticker", "date"]).reset_index(drop=True)


def read_broker_flow(tickers: list[str] | None = None) -> pd.DataFrame:
    init_db()
    q = "SELECT * FROM broker_flow"
    params: tuple = ()
    if tickers:
        q += f" WHERE ticker IN ({','.join('?' * len(tickers))})"
        params = tuple(t.upper() for t in tickers)
    with get_conn() as conn:
        df = pd.read_sql(q, conn, params=params, parse_dates=["date"])
    return df.sort_values(["ticker", "date"]).reset_index(drop=True)


def read_broker_activity(tickers: list[str] | None = None) -> pd.DataFrame:
    init_db()
    q = "SELECT * FROM broker_activity"
    params: tuple = ()
    if tickers:
        q += f" WHERE ticker IN ({','.join('?' * len(tickers))})"
        params = tuple(t.upper() for t in tickers)
    with get_conn() as conn:
        df = pd.read_sql(q, conn, params=params, parse_dates=["date"])
    return df.sort_values(["ticker", "date", "net_value"], ascending=[True, True, False]).reset_index(drop=True)


def read_runs() -> pd.DataFrame:
    init_db()
    with get_conn() as conn:
        return pd.read_sql("SELECT * FROM runs ORDER BY run_at DESC", conn)
