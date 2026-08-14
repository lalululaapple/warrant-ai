"""Hard filters applied before warrant scoring."""

from __future__ import annotations

import re
from collections.abc import Iterable

import pandas as pd


MONEYNESS_ITM_MAX = 10.0
MONEYNESS_OTM_MAX = 15.0
RATIO_MIN = 0.01
RATIO_MAX = 0.05
MIN_DAYS = 60.0
MAX_SPREAD = 1.5
LEVERAGE_MIN = 2.0
LEVERAGE_MAX = 4.0


def _compact(value: object) -> str:
    return re.sub(r"\s+", "", str(value)).lower()


def _find_column(df: pd.DataFrame, aliases: Iterable[str]) -> str:
    by_compact_name = {_compact(column): column for column in df.columns}
    for alias in aliases:
        column = by_compact_name.get(_compact(alias))
        if column is not None:
            return column
    raise ValueError(
        f"粗篩缺少必要欄位（可接受：{', '.join(aliases)}）；"
        f"目前欄位：{', '.join(map(str, df.columns))}"
    )


def _numeric(series: pd.Series) -> pd.Series:
    if pd.api.types.is_numeric_dtype(series):
        return pd.to_numeric(series, errors="coerce")
    cleaned = (
        series.astype("string")
        .str.replace(",", "", regex=False)
        .str.replace("%", "", regex=False)
        .str.extract(r"(-?\d+(?:\.\d+)?)", expand=False)
    )
    return pd.to_numeric(cleaned, errors="coerce")


def _coarse_filter_conditions(df: pd.DataFrame) -> dict[str, pd.Series]:
    """Return each hard-filter condition in the displayed evaluation order."""
    moneyness_col = _find_column(df, ("moneyness", "價內外程度", "價內外"))
    moneyness_pct_col = _find_column(df, ("moneyness_pct",))
    price_col = _find_column(df, ("price", "成交價"))
    ratio_col = _find_column(df, ("ratio", "行使比例"))
    days_col = _find_column(df, ("days", "剩餘天數", "剩餘日"))
    spread_col = _find_column(df, ("spread", "買賣價差比", "買賣價差比%"))
    leverage_col = _find_column(df, ("leverage", "實質槓桿"))

    moneyness = df[moneyness_col].astype("string")
    moneyness_pct = _numeric(df[moneyness_pct_col])
    price = _numeric(df[price_col])
    ratio = _numeric(df[ratio_col])
    days = _numeric(df[days_col])
    spread = _numeric(df[spread_col])
    leverage = _numeric(df[leverage_col])

    acceptable_moneyness = (
        (moneyness.str.contains("價內", na=False) & moneyness_pct.between(0, MONEYNESS_ITM_MAX))
        | (moneyness.str.contains("價外", na=False) & moneyness_pct.between(0, MONEYNESS_OTM_MAX))
    )

    return {
        "moneyness": acceptable_moneyness.fillna(False),
        "price": price.gt(0).fillna(False),
        "ratio": ratio.between(RATIO_MIN, RATIO_MAX).fillna(False),
        "days": days.ge(MIN_DAYS).fillna(False),
        "spread": spread.le(MAX_SPREAD).fillna(False),
        "leverage": leverage.between(LEVERAGE_MIN, LEVERAGE_MAX).fillna(False),
    }


def coarse_filter_mask(df: pd.DataFrame) -> pd.Series:
    """Return the verified first-stage hard-filter mask."""
    if df.empty:
        return pd.Series(False, index=df.index, dtype=bool)

    mask = pd.Series(True, index=df.index, dtype=bool)
    for condition in _coarse_filter_conditions(df).values():
        mask &= condition
    return mask.fillna(False)


def coarse_filter_counts(df: pd.DataFrame) -> dict[str, int]:
    """Return cumulative pass counts after each fixed hard-filter condition."""
    counts = {"raw": int(len(df))}
    if df.empty:
        for name in ("moneyness", "price", "ratio", "days", "spread", "leverage"):
            counts[name] = 0
        counts["final"] = 0
        return counts

    mask = pd.Series(True, index=df.index, dtype=bool)
    for name, condition in _coarse_filter_conditions(df).items():
        mask &= condition
        counts[name] = int(mask.sum())
    counts["final"] = int(mask.sum())
    return counts


def filter_warrants(df: pd.DataFrame) -> pd.DataFrame:
    """Apply the coarse filter and return a clean, independent DataFrame."""
    if df.empty:
        return df.copy()
    return df.loc[coarse_filter_mask(df)].copy().reset_index(drop=True)

