"""Modeling — regression + a simple ML classifier to test the hypothesis:

    "Does smart money flow (bandar/foreign accumulation) predict subsequent
    price increases in IDX stocks?"

Two angles, deliberately kept simple/interpretable rather than chasing
accuracy with a black box:

1. linear_regression()
   OLS: fwd_return ~ bandar_signal_score + foreign_net_flow_pct + volume_ratio
   -> gives you a coefficient + p-value per signal: is it statistically
      distinguishable from zero, given everything else in the model?

2. classify_up_down()
   Simple logistic regression / random forest classifying "price went up
   over the next N days" (1/0) from today's smart-money signals.
   -> gives you accuracy / precision / recall / a feature-importance view,
      i.e. "if I only trade when bandar shows AKUMULASI, how often does
      price actually go up afterwards?"

Both functions return plain dicts/DataFrames so they're easy to print or
feed into the Streamlit dashboard.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
import statsmodels.api as sm
from sklearn.ensemble import RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, precision_score, recall_score, roc_auc_score
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import StandardScaler

_FEATURE_COLS = ["bandar_signal_score", "foreign_net_flow_pct", "volume_ratio"]


@dataclass
class RegressionResult:
    target: str
    n_obs: int
    r_squared: float
    coefficients: pd.DataFrame   # columns: feature, coef, std_err, p_value, significant
    summary_text: str


def linear_regression(feat: pd.DataFrame, target_col: str = "fwd_return_5d",
                       feature_cols: list[str] | None = None) -> RegressionResult:
    """OLS regression of forward return on smart-money signals.

    Interpretation cheat sheet:
      * coef > 0 and p_value < 0.05 -> signal is associated with *higher*
        forward returns, and it's unlikely to be due to chance alone.
      * coef ~ 0 or p_value > 0.05 -> no statistically reliable relationship
        found in this sample — the hypothesis isn't supported (yet / here).
      * r_squared close to 0 -> smart-money signals explain very little of
        day-to-day return variance, which is *normal* for stock returns —
        even a small but significant coefficient can still be meaningful.
    """
    cols = feature_cols or _FEATURE_COLS
    cols = [c for c in cols if c in feat.columns]
    data = feat[cols + [target_col]].apply(pd.to_numeric, errors="coerce").dropna()

    if len(data) < 10 or not cols:
        return RegressionResult(
            target=target_col, n_obs=len(data), r_squared=float("nan"),
            coefficients=pd.DataFrame(), summary_text="Not enough data to fit a model "
            "(need at least 10 complete rows — run the pipeline for more days first).",
        )

    X = sm.add_constant(data[cols])
    y = data[target_col]
    model = sm.OLS(y, X).fit()

    coef_df = pd.DataFrame({
        "feature": X.columns,
        "coef": model.params.values,
        "std_err": model.bse.values,
        "p_value": model.pvalues.values,
    })
    coef_df["significant"] = coef_df["p_value"] < 0.05
    coef_df = coef_df[coef_df["feature"] != "const"].reset_index(drop=True)

    return RegressionResult(
        target=target_col, n_obs=int(model.nobs), r_squared=float(model.rsquared),
        coefficients=coef_df, summary_text=str(model.summary()),
    )


@dataclass
class ClassificationResult:
    target: str
    n_obs: int
    accuracy: float
    precision: float
    recall: float
    roc_auc: float | None
    feature_importance: pd.DataFrame
    model_name: str


def classify_up_down(feat: pd.DataFrame, target_col: str = "fwd_return_5d",
                      feature_cols: list[str] | None = None,
                      model_type: str = "logistic",
                      test_size: float = 0.25,
                      random_state: int = 42) -> ClassificationResult:
    """Binary classification: will price be up (1) or not (0) over the
    next N days, given today's smart-money signals?

    model_type: "logistic" (simple, interpretable coefficients) or
    "random_forest" (handles non-linearity, gives feature_importances_).

    Note: with a small watchlist + short history this is a *starting point*
    for the hypothesis test, not a production trading signal — check n_obs
    in the result before trusting the metrics.
    """
    cols = feature_cols or _FEATURE_COLS
    cols = [c for c in cols if c in feat.columns]
    data = feat[cols + [target_col]].apply(pd.to_numeric, errors="coerce").dropna()
    data["label"] = (data[target_col] > 0).astype(int)

    if len(data) < 20 or not cols or data["label"].nunique() < 2:
        return ClassificationResult(
            target=target_col, n_obs=len(data), accuracy=float("nan"),
            precision=float("nan"), recall=float("nan"), roc_auc=None,
            feature_importance=pd.DataFrame(), model_name=model_type,
        )

    X = data[cols]
    y = data["label"]
    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=test_size, random_state=random_state, stratify=y if y.nunique() > 1 else None,
    )

    scaler = StandardScaler()
    X_train_s = scaler.fit_transform(X_train)
    X_test_s = scaler.transform(X_test)

    if model_type == "random_forest":
        model = RandomForestClassifier(n_estimators=200, max_depth=4, random_state=random_state)
        model.fit(X_train, y_train)  # tree models don't need scaling
        preds = model.predict(X_test)
        proba = model.predict_proba(X_test)[:, 1]
        importances = pd.DataFrame({"feature": cols, "importance": model.feature_importances_})
        importances = importances.sort_values("importance", ascending=False).reset_index(drop=True)
    else:
        model = LogisticRegression(max_iter=1000)
        model.fit(X_train_s, y_train)
        preds = model.predict(X_test_s)
        proba = model.predict_proba(X_test_s)[:, 1]
        importances = pd.DataFrame({"feature": cols, "importance": model.coef_[0]})
        importances = importances.reindex(
            importances["importance"].abs().sort_values(ascending=False).index
        ).reset_index(drop=True)

    try:
        auc = roc_auc_score(y_test, proba) if y_test.nunique() > 1 else None
    except ValueError:
        auc = None

    return ClassificationResult(
        target=target_col, n_obs=len(data),
        accuracy=accuracy_score(y_test, preds),
        precision=precision_score(y_test, preds, zero_division=0),
        recall=recall_score(y_test, preds, zero_division=0),
        roc_auc=auc,
        feature_importance=importances,
        model_name=model_type,
    )


def hypothesis_verdict(reg: RegressionResult, clf: ClassificationResult) -> str:
    """Plain-language readout combining both models — meant to go straight
    into a notebook markdown cell or the dashboard."""
    lines = []
    if reg.coefficients.empty:
        lines.append("Belum cukup data untuk uji hipotesis (perlu lebih banyak hari pipeline berjalan).")
        return "\n".join(lines)

    sig_rows = reg.coefficients[reg.coefficients["significant"]]
    if sig_rows.empty:
        lines.append(
            f"Regresi OLS (n={reg.n_obs}, R²={reg.r_squared:.3f}) TIDAK menemukan hubungan "
            f"yang signifikan secara statistik (p<0.05) antara sinyal smart money dan "
            f"{reg.target}. Hipotesis belum terbukti pada data ini."
        )
    else:
        bits = [f"{r.feature} (coef={r.coef:+.4f}, p={r.p_value:.3f})" for r in sig_rows.itertuples()]
        lines.append(
            f"Regresi OLS (n={reg.n_obs}, R²={reg.r_squared:.3f}) menemukan hubungan signifikan: "
            + "; ".join(bits) + f" terhadap {reg.target}."
        )

    if not np.isnan(clf.accuracy):
        lines.append(
            f"Model klasifikasi ({clf.model_name}, n={clf.n_obs}): akurasi {clf.accuracy:.1%}, "
            f"precision {clf.precision:.1%}, recall {clf.recall:.1%}"
            + (f", ROC-AUC {clf.roc_auc:.2f}" if clf.roc_auc else "")
            + ". (Baseline acak ~50% untuk 2 kelas seimbang — bandingkan akurasi di atas dengan itu.)"
        )
    else:
        lines.append("Data belum cukup untuk model klasifikasi (perlu >=20 baris lengkap).")

    return "\n".join(lines)
