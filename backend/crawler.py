import asyncio
import math
import re
from io import StringIO

import pandas as pd
from playwright.async_api import async_playwright

from .config import YUANTA_SEARCH_URL, HEADLESS, SCREENSHOT_DIR
from .parser import normalize_dataframe


def find_warrant_table(html):
    """找出元大權證結果表。"""
    try:
        tables = pd.read_html(StringIO(html))
    except ValueError:
        return None

    for df in tables:
        cols = "".join("".join(str(c).split()) for c in df.columns)
        if "權證代碼" in cols and "權證名稱" in cols:
            return df

    return None


def _clean_result_df(df):
    if df is None or df.empty:
        return pd.DataFrame()

    df = normalize_dataframe(df)

    # 移除「沒有相關條件商品」等非資料列
    for col in df.columns:
        compact = "".join(str(col).split())
        if compact in {"權證名稱", "warrant_name"}:
            bad = (
                df[col]
                .astype(str)
                .str.contains("沒有相關條件商品", na=False)
            )
            df = df.loc[~bad].copy()
            break

    return df.reset_index(drop=True)


async def _select_underlying(page, symbol):
    """輸入股票代號並點選自動完成候選，例如 3189 景碩。"""
    target = page.locator(
        'input[placeholder="標的名稱/代碼"]'
    ).nth(0)

    if not await target.is_visible():
        raise RuntimeError("找不到可用的『標的名稱/代碼』輸入框。")

    await target.click()
    await target.fill(symbol)
    await page.wait_for_timeout(1200)

    # 候選文字格式例如：3189　景碩
    pattern = re.compile(rf"^{re.escape(symbol)}\s+.+$")
    candidates = page.get_by_text(pattern, exact=False)

    for i in range(await candidates.count()):
        item = candidates.nth(i)
        try:
            if await item.is_visible():
                text = (await item.inner_text()).strip()
                if text.startswith(symbol):
                    await item.click()
                    await page.wait_for_timeout(600)
                    selected_value = (await target.input_value()).strip()
                    if symbol in selected_value:
                        return selected_value
        except Exception:
            pass

    raise RuntimeError(
        f"輸入 {symbol} 後找不到或無法選取正確標的候選。"
    )


async def _click_search(page):
    buttons = page.get_by_text("查詢", exact=True)

    for i in range(await buttons.count()):
        btn = buttons.nth(i)
        try:
            if await btn.is_visible():
                await btn.click()
                await page.wait_for_timeout(2500)
                return
        except Exception:
            pass

    raise RuntimeError("找不到可點擊的『查詢』按鈕。")


async def _add_result_columns(page):
    """Add Greeks and bid/ask IV to the search result table."""
    await page.get_by_text("+增加項目", exact=True).click()
    popup = page.locator(".pop:visible")
    await popup.wait_for(state="visible")

    for column_name in ("DELTA", "THETA", "買價隱波", "賣價隱波"):
        available_item = popup.get_by_text(column_name, exact=True).first
        if await available_item.is_visible():
            await available_item.click()
            await popup.get_by_text("新增", exact=True).first.click()

    # 元大頁面的關閉控制有時被 Playwright 判定為不可見，
    # 直接觸發其原生 click，仍會執行 Angular 的 closePopup()。
    await popup.locator(".close").first.evaluate("element => element.click()")
    await page.wait_for_timeout(200)


async def _read_current_page(page):
    html = await page.content()
    return _clean_result_df(find_warrant_table(html))


def _page_signature(df):
    """Small fingerprint used to detect that pagination has finished."""
    if df is None or df.empty:
        return None
    for column in ("warrant_code", "權證 代碼"):
        if column in df.columns:
            return str(df.iloc[0][column])
    return str(df.iloc[0].to_dict())


async def _wait_for_page_change(page, previous_signature, timeout_ms=6000):
    """Return as soon as the result rows change; avoid a fixed 1.8s wait."""
    elapsed = 0
    interval_ms = 100
    while elapsed < timeout_ms:
        await page.wait_for_timeout(interval_ms)
        elapsed += interval_ms
        current = await _read_current_page(page)
        signature = _page_signature(current)
        if signature is not None and signature != previous_signature:
            return current
    raise RuntimeError("切換分頁逾時，結果資料沒有更新。")


async def _visible_exact_page_link(page, page_no):
    """回傳目前可見的分頁數字連結；找不到則回傳 None。"""
    links = page.locator("a").filter(
        has_text=re.compile(rf"^{page_no}$")
    )

    for i in range(await links.count()):
        link = links.nth(i)
        try:
            if await link.is_visible():
                txt = (await link.inner_text()).strip()
              …1376 tokens truncated…+", "", str(value)).lower()


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


def coarse_filter_mask(df: pd.DataFrame) -> pd.Series:
    """Return the verified first-stage hard-filter mask."""
    if df.empty:
        return pd.Series(False, index=df.index, dtype=bool)

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

    return (
        acceptable_moneyness
        & price.gt(0)
        & ratio.between(RATIO_MIN, RATIO_MAX)
        & days.ge(MIN_DAYS)
        & spread.le(MAX_SPREAD)
        & leverage.between(LEVERAGE_MIN, LEVERAGE_MAX)
    ).fillna(False)


def filter_warrants(df: pd.DataFrame) -> pd.DataFrame:
    """Apply the coarse filter and return a clean, independent DataFrame."""
    if df.empty:
        return df.copy()
    return df.loc[coarse_filter_mask(df)].copy().reset_index(drop=True)
