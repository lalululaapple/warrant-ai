import pandas as pd
from .config import WEIGHTS


def _missing(value):
    return value is None or pd.isna(value)


def _safe_divide(numerator, denominator):
    numerator = pd.to_numeric(numerator, errors="coerce")
    denominator = pd.to_numeric(denominator, errors="coerce")
    return numerator.div(denominator.where(denominator.ne(0)))


def _relative_lower_is_better(series):
    """Map the lowest valid value to 1.0 and the highest to 0.2."""
    values = pd.to_numeric(series, errors="coerce")
    valid = values.notna()
    result = pd.Series(0.0, index=values.index, dtype=float)
    count = int(valid.sum())
    if count == 0:
        return result
    if count == 1:
        result.loc[valid] = 1.0
        return result
    percentile = values.loc[valid].rank(method="average", pct=True, ascending=True)
    result.loc[valid] = 1.0 - 0.8 * ((percentile - 1.0 / count) / (1.0 - 1.0 / count))
    return result

def s_delta(v):
    if _missing(v): return 0
    v = abs(v)
    if 0.30 <= v <= 0.60: return 1
    if 0.20 <= v < 0.30 or 0.60 < v <= 0.70: return .75
    if 0.10 <= v < 0.20 or 0.70 < v <= 0.80: return .45
    return .20

def s_theta(v):
    if _missing(v): return 0
    v = abs(v)
    if v <= .02: return 1
    if v <= .04: return .75
    if v <= .07: return .45
    return .20

def s_iv(v):
    if _missing(v): return 0
    if v <= 30: return 1
    if v <= 40: return .80
    if v <= 50: return .55
    if v <= 70: return .35
    return .15

def s_leverage(v):
    if _missing(v): return 0
    if 2 <= v <= 4: return 1
    if 1.5 <= v < 2 or 4 < v <= 5: return .75
    if 1 <= v < 1.5 or 5 < v <= 7: return .45
    return .20

def s_days(v):
    if _missing(v): return 0
    if v >= 120: return 1
    if v >= 90: return .85
    if v >= 60: return .60
    if v >= 30: return .35
    return .15

def s_spread(v):
    if _missing(v): return 0
    if v <= 1: return 1
    if v <= 2: return .80
    if v <= 4: return .55
    if v <= 7: return .30
    return .10

def s_volume(v):
    if _missing(v): return 0
    if v >= 5000: return 1
    if v >= 1000: return .80
    if v >= 300: return .60
    if v >= 50: return .35
    return .15

def s_moneyness(v):
    if _missing(v): return 0
    v = abs(v)
    if v <= 5: return 1
    if v <= 10: return .85
    if v <= 15: return .60
    if v <= 25: return .35
    return .15

def score_row(row):
    parts = {
        "delta": s_delta(row.get("normalized_delta")),
        "theta": row.get("theta_quality", 0),
        "iv": row.get("iv_quality", 0),
        "leverage": s_leverage(row.get("leverage")),
        "days": s_days(row.get("days")),
        "spread": s_spread(row.get("spread")),
        "volume": s_volume(row.get("volume")),
        "moneyness": s_moneyness(row.get("moneyness_pct")),
    }
    return parts

def score_dataframe(df):
    if df.empty:
        return df.copy()
    out = df.copy()
    # 元大 DELTA 是每單位權證對標的價格變動的敏感度，已包含行使比例。
    # 除以行使比例後，才能公平比較不同行使比例的權證。
    out["normalized_delta"] = _safe_divide(out.get("delta"), out.get("ratio"))

    # Theta 是每天減少的權證價格；除以權證價格後才可比較不同價位。
    out["theta_decay_pct"] = (
        _safe_divide(pd.to_numeric(out.get("theta"), errors="coerce").abs(), out.get("price"))
        * 100
    )
    out["theta_quality"] = _relative_lower_is_better(out["theta_decay_pct"])

    # 僅作資料品質與後續規則設計用；目前不加入評分，避免擅改規則。
    if "bid_iv" in out.columns and "ask_iv" in out.columns:
        out["iv_gap"] = (
            pd.to_numeric(out["ask_iv"], errors="coerce")
            - pd.to_numeric(out["bid_iv"], errors="coerce")
        )
        ask_iv_quality = _relative_lower_is_better(out["ask_iv"])
        iv_gap_quality = _relative_lower_is_better(out["iv_gap"].abs())
        # 買進成本（賣價隱波）與報價一致性（隱波差）各占一半。
        out["iv_quality"] = 0.5 * ask_iv_quality + 0.5 * iv_gap_quality
    else:
        out["iv_gap"] = float("nan")
        out["iv_quality"] = 0.0

    parts = out.apply(score_row, axis=1, result_type="expand")
    for key in WEIGHTS:
        out[f"score_{key}"] = (parts[key] * WEIGHTS[key]).round(2)
    out["score"] = out[[f"score_{key}" for key in WEIGHTS]].sum(axis=1).round(2)
    out = out.sort_values("score", ascending=False).reset_index(drop=True)
    out["rank"] = range(1, len(out) + 1)
    return out
