"""Descriptive analysis — correlations and quick plots for the feature table.

Designed to be used from a notebook:

    from idx_bandarmology import features, analysis
    feat = features.build_feature_table()
    analysis.correlation_table(feat)
    analysis.plot_signal_vs_forward_return(feat, horizon=5)
"""

from __future__ import annotations

import matplotlib.pyplot as plt
import pandas as pd
import seaborn as sns

_SIGNAL_COLS = ["bandar_signal_score", "foreign_net_broker", "foreign_net_flow_pct"]
_FORWARD_COLS = ["fwd_return_1d", "fwd_return_3d", "fwd_return_5d", "fwd_return_10d"]


def correlation_table(feat: pd.DataFrame) -> pd.DataFrame:
    """Pearson correlation between smart-money signals and forward returns.

    Reads like: "if bandar_signal_score is high today, is fwd_return_5d
    (price return over the next 5 trading days) also higher, on average,
    across the whole watchlist & history we've collected?"
    """
    cols = [c for c in _SIGNAL_COLS + _FORWARD_COLS if c in feat.columns]
    sub = feat[cols].apply(pd.to_numeric, errors="coerce")
    corr = sub.corr(method="pearson")
    return corr.loc[
        [c for c in _SIGNAL_COLS if c in corr.index],
        [c for c in _FORWARD_COLS if c in corr.columns],
    ]


def correlation_by_ticker(feat: pd.DataFrame, signal_col: str = "bandar_signal_score",
                           target_col: str = "fwd_return_5d") -> pd.DataFrame:
    """Same correlation, but broken out per ticker — smart money's effect can
    differ a lot by stock (e.g. matters more for less-liquid names)."""
    empty = pd.DataFrame(columns=["ticker", "n_obs", "corr"])
    if signal_col not in feat.columns or target_col not in feat.columns:
        return empty
    rows = []
    for tk, g in feat.groupby("ticker"):
        sub = g[[signal_col, target_col]].apply(pd.to_numeric, errors="coerce").dropna()
        if len(sub) >= 5:
            rows.append({"ticker": tk, "n_obs": len(sub),
                         "corr": sub[signal_col].corr(sub[target_col])})
    if not rows:
        return empty
    return pd.DataFrame(rows).sort_values("corr", ascending=False).reset_index(drop=True)


def summary_by_signal(feat: pd.DataFrame, target_col: str = "fwd_return_5d") -> pd.DataFrame:
    """Mean/median forward return grouped by bandar_signal bucket — the most
    intuitive table for the hypothesis: does 'ACCUMULATION' really precede
    higher returns than 'DISTRIBUTION'?
    """
    if "bandar_signal" not in feat.columns or target_col not in feat.columns:
        return pd.DataFrame()
    g = feat.dropna(subset=[target_col]).groupby("bandar_signal")[target_col]
    out = g.agg(n="count", mean_return="mean", median_return="median", std="std").reset_index()
    order = ["STRONG_DISTRIBUTION", "DISTRIBUTION", "NET_SELL", "NEUTRAL", "NET_BUY", "ACCUMULATION", "STRONG_ACCUMULATION"]
    out["_order"] = out["bandar_signal"].apply(lambda s: order.index(s) if s in order else 99)
    return out.sort_values("_order").drop(columns="_order").reset_index(drop=True)


def plot_signal_vs_forward_return(feat: pd.DataFrame, horizon: int = 5,
                                    signal_col: str = "bandar_signal_score") -> plt.Figure:
    """Scatter + regression line: signal today vs forward return at `horizon` days."""
    target_col = f"fwd_return_{horizon}d"
    sub = feat[[signal_col, target_col, "ticker"]].dropna()
    fig, ax = plt.subplots(figsize=(7, 5))
    sns.regplot(data=sub, x=signal_col, y=target_col, ax=ax,
                scatter_kws={"alpha": 0.5, "s": 25}, line_kws={"color": "crimson"})
    ax.set_xlabel("Bandar signal score (today)")
    ax.set_ylabel(f"Forward return, {horizon}d ahead")
    ax.set_title(f"Smart money signal vs {horizon}-day forward return")
    ax.axhline(0, color="gray", linewidth=0.8, linestyle="--")
    fig.tight_layout()
    return fig


def plot_signal_bucket_returns(feat: pd.DataFrame, target_col: str = "fwd_return_5d") -> plt.Figure:
    """Boxplot of forward returns, one box per bandar_signal bucket."""
    order = ["STRONG_DISTRIBUTION", "DISTRIBUTION", "NET_SELL", "NEUTRAL", "NET_BUY", "ACCUMULATION", "STRONG_ACCUMULATION"]
    sub = feat.dropna(subset=[target_col, "bandar_signal"]).copy()
    present = [o for o in order if o in sub["bandar_signal"].unique()]
    fig, ax = plt.subplots(figsize=(8, 5))
    sns.boxplot(data=sub, x="bandar_signal", y=target_col, order=present, hue="bandar_signal",
                palette="RdYlGn", legend=False, ax=ax)
    ax.axhline(0, color="gray", linewidth=0.8, linestyle="--")
    ax.set_xlabel("Bandar signal")
    ax.set_ylabel(target_col)
    ax.set_title("Forward return distribution by bandar signal")
    plt.setp(ax.get_xticklabels(), rotation=20, ha="right")
    fig.tight_layout()
    return fig


def plot_price_with_signal(feat: pd.DataFrame, ticker: str) -> plt.Figure:
    """Price line for one ticker, with markers colored by that day's bandar signal."""
    sub = feat[feat["ticker"] == ticker].dropna(subset=["close"]).sort_values("date")
    color_map = {
        "STRONG_ACCUMULATION": "#1a9850", "ACCUMULATION": "#91cf60", "NET_BUY": "#91cf60",
        "NEUTRAL": "#999999",
        "DISTRIBUTION": "#fc8d59", "STRONG_DISTRIBUTION": "#d73027", "NET_SELL": "#fc8d59",
    }
    fig, ax = plt.subplots(figsize=(10, 5))
    ax.plot(sub["date"], sub["close"], color="#444", linewidth=1.2, label="Close")
    if "bandar_signal" in sub.columns:
        colors = sub["bandar_signal"].map(color_map).fillna("#cccccc")
        has_signal = sub["bandar_signal"].notna()
        ax.scatter(sub.loc[has_signal, "date"], sub.loc[has_signal, "close"],
                   c=colors[has_signal], s=40, zorder=3, label="Bandar signal")
    ax.set_title(f"{ticker} — price & bandar signal")
    ax.set_ylabel("Close price (Rp)")
    fig.autofmt_xdate()
    fig.tight_layout()
    return fig
