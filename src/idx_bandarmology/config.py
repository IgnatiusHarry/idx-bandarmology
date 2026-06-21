"""Central config: .env loading, filesystem paths, and the default watchlist.

Edit WATCHLIST below (or override via the WATCHLIST env var, comma-separated)
to change which tickers the pipeline scans. The pipeline is written so adding
or removing tickers here is the only change needed end-to-end.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# Load .env from the project root regardless of current working directory
# (so this works the same from a notebook in /notebooks or a script in /src).
_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(_ROOT / ".env")

# ── paths ─────────────────────────────────────────────────────────────────
DATA_DIR = _ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
PROCESSED_DIR = DATA_DIR / "processed"
DB_PATH = DATA_DIR / "db" / "bandarmology.sqlite"

for _d in (RAW_DIR, PROCESSED_DIR, DB_PATH.parent):
    _d.mkdir(parents=True, exist_ok=True)

# ── secrets ───────────────────────────────────────────────────────────────
STOCKBIT_TOKEN = os.environ.get("STOCKBIT_TOKEN", "").strip() or None

# ── watchlist ─────────────────────────────────────────────────────────────
# Start small on purpose — this is a starting point you search/curate by hand.
# The pipeline doesn't care how big this list is; grow it whenever you like.
_DEFAULT_WATCHLIST = [
    "BBCA", "BBRI", "BMRI", "BBNI",   # big banks
    "TLKM", "ASII", "UNVR",            # blue chips
    "GOTO", "BREN", "ANTM",            # high-flow / volatile names
]


def get_watchlist() -> list[str]:
    """Watchlist from env (WATCHLIST=BBCA,BBRI,...) or the default above."""
    env_val = os.environ.get("WATCHLIST", "").strip()
    if env_val:
        return [t.strip().upper() for t in env_val.split(",") if t.strip()]
    return list(_DEFAULT_WATCHLIST)


WATCHLIST = get_watchlist()
