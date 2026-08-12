from pathlib import Path
import pandas as pd
from .config import OUTPUT_DIR

def export_excel(df, symbol):
    path = OUTPUT_DIR / f"{symbol}_權證排行榜.xlsx"

    wanted = [
        "rank", "warrant_code", "warrant_name", "price", "volume",
        "strike", "ratio", "days", "moneyness", "spread",
        "leverage", "iv", "bid_iv", "ask_iv", "outstanding",
        "delta", "normalized_delta", "theta", "theta_decay_pct",
        "iv_gap", "score_delta", "score_theta", "score_iv",
        "score_leverage", "score_days", "score_spread", "score_volume",
        "score_moneyness", "score"
    ]
    cols = [c for c in wanted if c in df.columns]

    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        df[cols].head(10).to_excel(writer, sheet_name="TOP10", index=False)
        df[cols].to_excel(writer, sheet_name="全部權證", index=False)

    return path
