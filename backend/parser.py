import re
from typing import Any
import pandas as pd

COLUMN_ALIASES = {
    "warrant_code": ["權證代碼"],
    "warrant_name": ["權證名稱"],
    "price": ["成交價"],
    "change": ["漲跌"],
    "change_pct": ["漲跌幅", "漲跌幅%"],
    "volume": ["成交量"],
    "strike": ["履約價"],
    "ratio": ["行使比例"],
    "days": ["剩餘天數", "剩餘日"],
    "moneyness": ["價內外程度", "價內外"],
    "spread": ["買賣價差比", "買賣價差比%"],
    "leverage": ["實質槓桿"],
    "iv": ["成交價隱波", "成交價隱波%"],
    "outstanding": ["流通在外比例", "流通在外比例%"],
    "delta": ["DELTA", "Delta"],
    "theta": ["THETA", "Theta"],
    "bid_iv": ["買價隱波", "買價隱波%"],
    "ask_iv": ["賣價隱波", "賣價隱波%"],
}

def clean_text(value: Any) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()

def compact_name(value: Any) -> str:
    """Normalize a column name while ignoring display-only whitespace."""
    return re.sub(r"\s+", "", str(value)).strip().lower()

def to_float(value: Any):
    text = clean_text(value).replace(",", "")
    if not text or text in {"--", "-", "—"}:
        return None
    text = text.replace("%", "")
    m = re.search(r"-?\d+(?:\.\d+)?", text)
    return float(m.group()) if m else None

def normalize_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df.copy()

    df = df.copy()
    df.columns = [clean_text(c) for c in df.columns]

    aliases_by_name = {
        compact_name(alias): target
        for target, aliases in COLUMN_ALIASES.items()
        for alias in aliases
    }
    rename = {}
    for col in df.columns:
        target = aliases_by_name.get(compact_name(col))
        if target is not None and col != target:
            rename[col] = target
    df.rename(columns=rename, inplace=True)

    numeric = [
        "price", "change", "change_pct", "volume", "strike", "ratio",
        "days", "spread", "leverage", "iv", "outstanding",
        "delta", "theta", "bid_iv", "ask_iv"
    ]
    for col in numeric:
        if col in df.columns:
            df[col] = df[col].map(to_float)

    if "moneyness" in df.columns:
        df["moneyness_pct"] = df["moneyness"].map(to_float)

    return df
