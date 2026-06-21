"""idx_bandarmology — simple end-to-end bandarmology pipeline for IDX stocks.

Modules
-------
config        : .env loading, watchlist, paths
broker_api    : broker-flow client and bandar detector parser
prices        : yfinance client — OHLCV history for IDX tickers
storage       : SQLite read/write helpers (the "pipeline" landing zone)
pipeline      : orchestrates scrape -> clean -> store, for one run
features      : turns raw broker/price tables into a single tidy feature table
analysis      : descriptive stats, correlations, plots
modeling      : regression + simple ML to test the "smart money -> price" hypothesis
"""

from . import config  # noqa: F401
