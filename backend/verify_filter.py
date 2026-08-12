"""Live health check without assuming a fixed market result count."""

from __future__ import annotations

import asyncio
import sys

from .crawler import crawl_warrants
from .filter import coarse_filter_mask, filter_warrants
from .score import score_dataframe


DEFAULT_SYMBOLS = ("1303", "2368", "3189")
REQUIRED_COLUMNS = {
    "warrant_code", "warrant_name", "price", "volume", "strike", "ratio",
    "days", "moneyness", "moneyness_pct", "spread", "leverage", "delta",
    "theta", "bid_iv", "ask_iv",
}
DERIVED_COLUMNS = {
    "normalized_delta", "theta_decay_pct", "iv_gap", "score_delta",
    "score_theta", "score_iv", "score", "rank",
}


async def verify_symbol(symbol: str) -> bool:
    warrants = await crawl_warrants(symbol, save_screenshot=False)
    problems = []

    if warrants.empty:
        problems.append("沒有抓到資料")

    missing = sorted(REQUIRED_COLUMNS.difference(warrants.columns))
    if missing:
        problems.append(f"缺少欄位：{', '.join(missing)}")

    filtered = filter_warrants(warrants) if not missing else warrants.iloc[0:0].copy()
    if not filtered.empty and not bool(coarse_filter_mask(filtered).all()):
        problems.append("粗篩結果包含不符合硬條件的資料")

    if not filtered.empty:
        scored = score_dataframe(filtered)
        missing_derived = sorted(DERIVED_COLUMNS.difference(scored.columns))
        if missing_derived:
            problems.append(f"缺少計算欄位：{', '.join(missing_derived)}")
        if scored["score"].isna().any():
            problems.append("Score 出現空值")

    status = "PASS" if not problems else "FAIL"
    print(f"[{status}] {symbol}: total={len(warrants)}, filtered={len(filtered)}")
    for problem in problems:
        print(f"  - {problem}")
    return not problems


async def main() -> None:
    symbols = tuple(sys.argv[1:]) or DEFAULT_SYMBOLS
    results = []
    for symbol in symbols:
        results.append(await verify_symbol(symbol))
    if not all(results):
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
