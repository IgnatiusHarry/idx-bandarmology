"""Stockbit (exodus.stockbit.com) client — real per-stock broker & flow data.

Authenticated via the ``STOCKBIT_TOKEN`` secret (Bearer, put it in your .env).
Plain HTTPS GET — these endpoints are not behind Cloudflare, so no browser /
FlareSolverr dance is needed.

What this unlocks (the "bandarmology" part — hard to get on public IDX
sources without a broker login):
  * Real per-broker net buy/sell breakdown (local "Lokal" vs foreign "Asing" vs
    "Pemerintah") from Stockbit's broker_summary.
  * Bandar Detector accumulation/distribution signals (avg, 5-day, top1/3/5/10).
  * Foreign-vs-domestic transaction flow (value / volume).
  * Multi-timeframe price performance (1D...10Y), used as a quick cross-check.

Every numeric field is normalized to a float or ``None`` — nothing is
fabricated when a field is missing. Each section also carries a short
English ``conclusion`` string for quick reading in the dashboard.

Credit: this client started from a hands-on script written by the repo
owner against Stockbit's internal API; restructured here for reuse in the
pipeline (config-driven paths/token, caching, and section-level fallbacks).
"""

from __future__ import annotations

import time
from typing import Any, Callable, Optional

import requests

from . import config

_BASE = "https://exodus.stockbit.com"
_TIMEOUT = 15.0
_CACHE_TTL = 300.0  # 5 minutes — Stockbit data is end-of-day, refreshes slowly.
_cache: dict[str, tuple[float, Any]] = {}


# ── transport ────────────────────────────────────────────────────────────────

def is_available() -> bool:
    """True when a Stockbit token is configured in .env."""
    return bool(config.STOCKBIT_TOKEN)


def _get(path: str) -> Any:
    if not config.STOCKBIT_TOKEN:
        raise RuntimeError(
            "STOCKBIT_TOKEN not configured — add it to your .env "
            "(see .env.example)"
        )
    resp = requests.get(
        _BASE + path,
        headers={
            "Authorization": f"Bearer {config.STOCKBIT_TOKEN}",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            "Accept": "application/json",
        },
        timeout=_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()


def _cached(key: str, fn: Callable[[], Any]) -> Any:
    now = time.time()
    hit = _cache.get(key)
    if hit and now - hit[0] < _CACHE_TTL:
        return hit[1]
    val = fn()
    _cache[key] = (now, val)
    return val


# ── parsing helpers ──────────────────────────────────────────────────────────

def _f(v: Any) -> Optional[float]:
    """Coerce to float; Stockbit often returns numbers as strings."""
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _raw(o: Any) -> Optional[float]:
    """Extract ``.value.raw`` / ``.raw`` from Stockbit's nested value dicts."""
    if isinstance(o, dict):
        if "raw" in o:
            return _f(o.get("raw"))
        if "value" in o:
            return _raw(o.get("value"))
    return _f(o)


def _rp(v: Optional[float]) -> str:
    """Human-readable Rupiah (T = trillion, B = billion, M = million)."""
    if v is None:
        return "-"
    a = abs(v)
    sign = "-" if v < 0 else ""
    if a >= 1e12:
        return f"{sign}Rp {a / 1e12:.2f}T"
    if a >= 1e9:
        return f"{sign}Rp {a / 1e9:.2f}B"
    if a >= 1e6:
        return f"{sign}Rp {a / 1e6:.2f}M"
    return f"{sign}Rp {a:.0f}"


def _sym(ticker: str) -> str:
    return ticker.upper().replace(".JK", "").strip()


# accdist label -> (signal code, score, readable text)
_ACC_MAP: dict[str, tuple[str, int, str]] = {
    "Big Acc": ("STRONG_ACCUMULATION", 2, "strong accumulation"),
    "Small Acc": ("ACCUMULATION", 1, "accumulation"),
    "Neutral": ("NEUTRAL", 0, "neutral"),
    "Small Dist": ("DISTRIBUTION", -1, "distribution"),
    "Big Dist": ("STRONG_DISTRIBUTION", -2, "strong distribution"),
}


def _accdist(label: Optional[str]) -> tuple[str, int, str]:
    return _ACC_MAP.get(label or "", ("NEUTRAL", 0, "neutral"))


# ── section: broker summary + bandar detector ────────────────────────────────

def _broker_section(sym: str) -> dict[str, Any]:
    md = _get(f"/marketdetectors/{sym}").get("data", {}) or {}
    bandar = md.get("bandar_detector") or {}
    bs = md.get("broker_summary") or {}

    def mk_buy(b: dict) -> dict:
        return {
            "code": b.get("netbs_broker_code"),
            "type": b.get("type"),
            "value": _f(b.get("bval")),
            "lot": _f(b.get("blot")),
            "freq": _f(b.get("freq")),
            "avgPrice": _f(b.get("netbs_buy_avg_price")),
        }

    def mk_sell(b: dict) -> dict:
        return {
            "code": b.get("netbs_broker_code"),
            "type": b.get("type"),
            "value": _f(b.get("sval")),
            "lot": _f(b.get("slot")),
            "freq": _f(b.get("freq")),
            "avgPrice": _f(b.get("netbs_sell_avg_price")),
        }

    buyers = [mk_buy(b) for b in (bs.get("brokers_buy") or [])]
    sellers = [mk_sell(b) for b in (bs.get("brokers_sell") or [])]
    buyers.sort(key=lambda x: x["value"] or 0, reverse=True)
    sellers.sort(key=lambda x: x["value"] or 0)  # most negative first

    def net_by_type(t: str) -> float:
        s = 0.0
        for b in buyers + sellers:
            if b["type"] == t and b["value"]:
                s += b["value"]
        return s

    foreign_net = net_by_type("Asing")
    local_net = net_by_type("Lokal")
    gov_net = net_by_type("Pemerintah")

    def acc(o: Any) -> Optional[dict]:
        if not isinstance(o, dict):
            return None
        return {
            "accdist": o.get("accdist"),
            "amount": _f(o.get("amount")),
            "percent": _f(o.get("percent")),
            "vol": _f(o.get("vol")),
        }

    bandar_out = {
        "brokerAccdist": bandar.get("broker_accdist"),
        "avg": acc(bandar.get("avg")),
        "avg5": acc(bandar.get("avg5")),
        "top1": acc(bandar.get("top1")),
        "top3": acc(bandar.get("top3")),
        "top5": acc(bandar.get("top5")),
        "top10": acc(bandar.get("top10")),
        "totalBuyer": bandar.get("total_buyer"),
        "totalSeller": bandar.get("total_seller"),
        "numberBrokerBuysell": bandar.get("number_broker_buysell"),
        "value": _f(bandar.get("value")),
        "volume": _f(bandar.get("volume")),
        "averagePrice": _f(bandar.get("average")),
    }

    # Overall bandar signal: blend the 5-day pattern (avg5) with the big-player
    # gauge (top5). Whichever is stronger in magnitude wins.
    sig5, score5, read5 = _accdist((bandar.get("avg5") or {}).get("accdist"))
    sigt, scoret, readt = _accdist((bandar.get("top5") or {}).get("accdist"))
    if abs(scoret) >= abs(score5):
        signal, readable, score = sigt, readt, scoret
    else:
        signal, readable, score = sig5, read5, score5

    top5 = bandar_out["top5"] or {}
    fnet_word = "net buy" if foreign_net > 0 else "net sell" if foreign_net < 0 else "balanced"
    conclusion = (
        f"Big players (top 5 brokers) show signs of {readable} "
        f"({_rp(top5.get('amount'))}, {(top5.get('percent') or 0):.1f}% of value). "
        f"Foreign {fnet_word} {_rp(abs(foreign_net))} today; local {_rp(local_net)}. "
        f"{bandar_out['totalBuyer'] or 0} brokers buying vs {bandar_out['totalSeller'] or 0} brokers selling."
    )

    return {
        "available": True,
        "date": md.get("to") or md.get("from"),
        "signal": signal,
        "signalScore": score,
        "buyers": buyers[:12],
        "sellers": sellers[:12],
        "foreignNet": foreign_net,
        "localNet": local_net,
        "govNet": gov_net,
        "bandar": bandar_out,
        "conclusion": conclusion,
    }


# ── section: foreign vs domestic flow ────────────────────────────────────────

def _foreign_domestic_section(sym: str) -> dict[str, Any]:
    fd = _get(f"/findata-view/foreign-domestic/v1/chart-data/{sym}").get("data", {}) or {}
    val = fd.get("value", {}) or {}
    summary = fd.get("summary", {}) or {}

    def vp(node: Any) -> dict:
        node = node or {}
        return {"value": _raw(node.get("value")), "pct": _raw(node.get("percentage"))}

    net_foreign = _raw((summary.get("net_foreign") or {}).get("value"))
    net_domestic = _raw((summary.get("net_domestic") or {}).get("value"))
    total = _raw(val.get("total"))

    out = {
        "available": True,
        "date": fd.get("last_updated") or fd.get("to"),
        "totalValue": total,
        "foreignBuy": vp(val.get("foreign_buy")),
        "foreignSell": vp(val.get("foreign_sell")),
        "domesticBuy": vp(val.get("domestic_buy")),
        "domesticSell": vp(val.get("domestic_sell")),
        "foreignTotalPct": _raw((val.get("foreign_total") or {}).get("percentage")),
        "domesticTotalPct": _raw((val.get("domestic_total") or {}).get("percentage")),
        "netForeign": net_foreign,
        "netDomestic": net_domestic,
    }

    netpct = (net_foreign / total * 100) if (net_foreign is not None and total) else None
    if net_foreign is None:
        word, signal = "not available", "NEUTRAL"
    elif net_foreign > 0:
        word = "net buy (foreign accumulation)"
        signal = "ACCUMULATION" if (netpct or 0) >= 5 else "NET_BUY"
    elif net_foreign < 0:
        word = "net sell (foreign distribution)"
        signal = "DISTRIBUTION" if (netpct or 0) <= -5 else "NET_SELL"
    else:
        word, signal = "balanced", "NEUTRAL"
    out["signal"] = signal
    out["conclusion"] = (
        f"Foreign {word} {_rp(abs(net_foreign) if net_foreign is not None else None)}"
        + (f" ({netpct:+.1f}% of transaction value {_rp(total)})" if netpct is not None else "")
        + f"; net domestic {_rp(net_domestic)}."
    )
    return out


# ── section: price performance (quick cross-check; daily OHLC comes from yfinance) ──

def _price_performance_section(sym: str) -> dict[str, Any]:
    prices = _get(f"/company-price-feed/price-performance/{sym}").get("data", {}).get("prices", []) or []
    rows = []
    by_tf: dict[str, Optional[float]] = {}
    for p in prices:
        tf = p.get("timeframe")
        pct = _raw(p.get("percentage"))
        rows.append({
            "timeframe": tf,
            "close": _raw(p.get("close")),
            "high": _raw(p.get("high")),
            "low": _raw(p.get("low")),
            "pct": pct,
        })
        if tf:
            by_tf[tf] = pct

    m1, m3, y1 = by_tf.get("1M"), by_tf.get("3M"), by_tf.get("1Y")
    parts = []
    for label, v in (("1 month", m1), ("3 months", m3), ("1 year", y1)):
        if v is not None:
            parts.append(f"{label} {v:+.1f}%")
    mom = sum(1 for v in (m1, m3, y1) if v is not None and v > 0)
    neg = sum(1 for v in (m1, m3, y1) if v is not None and v < 0)
    if mom > neg:
        trend = "positive price momentum"
    elif neg > mom:
        trend = "negative price momentum"
    else:
        trend = "mixed price momentum"
    conclusion = f"{trend.capitalize()}" + (f" ({', '.join(parts)})." if parts else ".")

    return {"available": True, "prices": rows, "conclusion": conclusion}


# ── public API ───────────────────────────────────────────────────────────────

def fetch_analysis(ticker: str) -> dict[str, Any]:
    """Full Stockbit per-stock analysis with per-section graceful degradation.

    Returns a dict with ``broker``, ``foreignDomestic`` and ``pricePerformance``
    sections, each independently marked ``available`` so one broken endpoint
    doesn't take down the whole pipeline run.
    """
    sym = _sym(ticker)

    def _build() -> dict[str, Any]:
        result: dict[str, Any] = {"ticker": sym, "available": True}

        def safe(name: str, fn: Callable[[], dict]) -> None:
            try:
                result[name] = fn()
            except Exception as exc:  # noqa: BLE001
                result[name] = {"available": False, "reason": str(exc)[:160]}

        safe("broker", lambda: _broker_section(sym))
        safe("foreignDomestic", lambda: _foreign_domestic_section(sym))
        safe("pricePerformance", lambda: _price_performance_section(sym))

        sections = [result.get("broker"), result.get("foreignDomestic"), result.get("pricePerformance")]
        if not any((s or {}).get("available") for s in sections):
            result["available"] = False

        result["summary"] = _overall_summary(sym, result)
        return result

    return _cached(f"analysis:{sym}", _build)


def _overall_summary(sym: str, r: dict[str, Any]) -> str:
    broker = r.get("broker") or {}
    fd = r.get("foreignDomestic") or {}
    pp = r.get("pricePerformance") or {}
    bits = []
    if broker.get("available"):
        sig = broker.get("signal", "NEUTRAL").replace("_", " ").lower()
        bits.append(f"bandar signal {sig}")
        fn = broker.get("foreignNet")
        if fn is not None:
            bits.append(f"foreign net broker {_rp(fn)}")
    if fd.get("available") and fd.get("netForeign") is not None:
        bits.append(f"foreign flow {_rp(fd.get('netForeign'))}")
    if pp.get("available"):
        for row in pp.get("prices", []):
            if row.get("timeframe") == "1M" and row.get("pct") is not None:
                bits.append(f"price 1 month {row['pct']:+.1f}%")
                break
    if not bits:
        return f"Stockbit data for {sym} is not available for the latest trading day."
    return f"{sym}: " + "; ".join(bits) + "."


def fetch_watchlist(symbols: list[str]) -> dict[str, dict[str, Any]]:
    """Run fetch_analysis for each symbol in the watchlist. Never raises."""
    out: dict[str, dict[str, Any]] = {}
    for s in symbols:
        try:
            out[_sym(s)] = fetch_analysis(s)
        except Exception as exc:  # noqa: BLE001
            out[_sym(s)] = {"ticker": _sym(s), "available": False, "reason": str(exc)[:160]}
    return out
