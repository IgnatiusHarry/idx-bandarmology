"""idx_bandarmology — simple end-to-end bandarmology pipeline for IDX stocks.

Modules
-------
config        : .env loading, watchlist, paths
stockbit      : Stockbit (exodus.stockbit.com) client — broker flow & bandar detector
prices        : yfinance client — OHLCV history for IDX tickers
storage       : SQLite read/write helpers (the "pipeline" landing zone)
pipeline      : orchestrates scrape -> clean -> store, for one run
features      : turns raw broker/price tables into a single tidy feature table
analysis      : descriptive stats, correlations, plots
modeling      : regression + simple ML to test the "smart money -> price" hypothesis
"""

from . import config  # noqa: F401
