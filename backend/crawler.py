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
    """輸入股票代號或中文名稱並選取元大的自動完成候選。"""
    target = page.locator(
        'input[placeholder="標的名稱/代碼"]'
    ).nth(0)

    if not await target.is_visible():
        raise RuntimeError("找不到可用的『標的名稱/代碼』輸入框。")

    await target.click()
    await target.fill(symbol)
    await page.wait_for_timeout(1200)

    # 候選文字格式例如：2330　台積電。中文查詢時代號在前。
    if symbol.isdigit():
        pattern = re.compile(rf"^{re.escape(symbol)}\s+.+$")
    else:
        pattern = re.compile(
            rf"^\d{{4,6}}\s+.*{re.escape(symbol)}.*$",
            re.IGNORECASE,
        )
    candidates = page.get_by_text(pattern, exact=False)

    visible = []
    for i in range(await candidates.count()):
        item = candidates.nth(i)
        try:
            if await item.is_visible():
                text = (await item.inner_text()).strip()
                match = re.match(r"^(\d{4,6})\s+(.+)$", text)
                if match:
                    visible.append((item, text, match.group(2).strip()))
        except Exception:
            pass

    if visible:
        selected = next(
            (entry for entry in visible if entry[2].casefold() == symbol.casefold()),
            visible[0],
        )
        await selected[0].click()
        await page.wait_for_timeout(600)
        return selected[1]

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
    # Parse only the result table instead of serializing and parsing the
    # entire Angular page on every pagination step.
    tables = page.locator("table")
    for index in range(await tables.count()):
        table = tables.nth(index)
        try:
            if not await table.is_visible():
                continue
            header = await table.locator("tr").first.inner_text()
            compact = "".join(header.split())
            if "權證代碼" in compact and "權證名稱" in compact:
                html = await table.evaluate("element => element.outerHTML")
                return _clean_result_df(find_warrant_table(html))
        except Exception:
            continue
    return pd.DataFrame()


def _page_signature(df):
    """Small fingerprint used to detect that pagination has finished."""
    if df is None or df.empty:
        return None
    for column in ("warrant_code", "權證 代碼"):
        if column in df.columns:
            return str(df.iloc[0][column])
    return str(df.iloc[0].to_dict())


async def _wait_for_page_change(page, previous_signature, timeout_ms=6000):
    """Wait cheaply for the first result row to change, then parse once."""
    elapsed = 0
    interval_ms = 150
    while elapsed < timeout_ms:
        await page.wait_for_timeout(interval_ms)
        elapsed += interval_ms
        tables = page.locator("table")
        for index in range(await tables.count()):
            table = tables.nth(index)
            try:
                if not await table.is_visible():
                    continue
                rows = table.locator("tr")
                if await rows.count() < 2:
                    continue
                header = "".join((await rows.first.inner_text()).split())
                if "權證代碼" not in header:
                    continue
                first_row = await rows.nth(1).inner_text()
                if previous_signature and previous_signature not in first_row:
                    return await _read_current_page(page)
            except Exception:
                continue
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
                if txt == str(page_no):
                    return link
        except Exception:
            pass

    return None


async def _extract_total_count(page):
    body = await page.locator("body").inner_text()
    # 元大頁面常見：總筆數：94
    m = re.search(r"總筆數\s*[:：]\s*(\d+)", body)
    if m:
        return int(m.group(1))
    return None


async def crawl_warrants(
    symbol: str,
    save_screenshot=False,
    page_filter=None,
    progress_callback=None,
):
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=HEADLESS,
            args=[
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--no-sandbox",
            ],
        )
        page = await browser.new_page(
            viewport={"width": 1440, "height": 1000}
        )

        # The search page does not need images, media or web fonts. Blocking
        # them lowers Chromium memory usage on small Render instances.
        async def block_heavy_resources(route):
            if route.request.resource_type in {"image", "media", "font"}:
                await route.abort()
            else:
                await route.continue_()

        await page.route("**/*", block_heavy_resources)

        try:
            await page.goto(
                YUANTA_SEARCH_URL,
                wait_until="domcontentloaded",
                timeout=60000
            )
            await page.wait_for_timeout(1500)

            selected = await _select_underlying(page, symbol)
            print(f"[crawler] 已選取標的：{selected}")
            selected_match = re.match(r"^(\d{4,6})\s+(.+)$", selected)
            resolved_symbol = selected_match.group(1) if selected_match else symbol

            # 元大搜尋頁預設發行人是「元大證券」
            # 改成「全部」，才能取得所有券商發行的權證
            issuer = page.locator("select").nth(1)

            await issuer.select_option(label="全部")
            await page.wait_for_timeout(500)

            issuer_text = (
                await issuer.locator("option:checked").inner_text()
            ).strip()

            print(f"[crawler] 發行人：{issuer_text}")

            await _add_result_columns(page)
            await _click_search(page)

            if save_screenshot:
                await page.screenshot(
                    path=str(SCREENSHOT_DIR / f"{symbol}_page1.png"),
                    full_page=True
                )

            total_count = await _extract_total_count(page)
            if total_count is not None:
                print(f"[crawler] 元大顯示總筆數：{total_count}")

            kept_pages = []

            def keep_page(df):
                if page_filter is None or df.empty:
                    return df
                # Apply the exact same application hard filter immediately,
                # so rejected rows do not stay resident for all 58+ pages.
                return page_filter(df)

            # 第 1 頁
            df1 = await _read_current_page(page)
            print(f"[crawler] 第 1 頁抓到 {len(df1)} 筆")
            empty_result = df1.iloc[0:0].copy()
            previous_signature = _page_signature(df1)
            kept = keep_page(df1)
            if not kept.empty:
                kept_pages.append(kept)
            del df1, kept

            # 若可讀到總筆數，元大目前約 20 筆/頁；
            # 否則最多嘗試 20 頁，遇到沒有下一個頁碼就停止。
            if total_count:
                max_pages = max(1, math.ceil(total_count / 20))
            else:
                max_pages = 20

            if progress_callback:
                await progress_callback(1, max_pages, selected)

            for page_no in range(2, max_pages + 1):
                link = await _visible_exact_page_link(page, page_no)

                if link is None:
                    # 若總筆數未知，找不到下一頁就停止。
                    if total_count is None:
                        break

                    # 有總筆數但目前看不到頁碼：再稍等一次。
                    await page.wait_for_timeout(500)
                    link = await _visible_exact_page_link(page, page_no)
                    if link is None:
                        print(
                            f"[crawler] 找不到第 {page_no} 頁連結，停止分頁。"
                        )
                        break

                await link.click()
                dfn = await _wait_for_page_change(page, previous_signature)

                if save_screenshot:
                    await page.screenshot(
                        path=str(
                            SCREENSHOT_DIR /
                            f"{symbol}_page{page_no}.png"
                        ),
                        full_page=True
                    )

                print(
                    f"[crawler] 第 {page_no} 頁抓到 {len(dfn)} 筆"
                )

                if progress_callback:
                    await progress_callback(page_no, max_pages, selected)

                if dfn.empty:
                    break

                previous_signature = _page_signature(dfn)
                kept = keep_page(dfn)
                if not kept.empty:
                    kept_pages.append(kept)
                del dfn, kept

            if not kept_pages:
                # A valid search can legitimately have zero hard-filter hits.
                if page_filter is not None:
                    empty_result.attrs["symbol"] = resolved_symbol
                    empty_result.attrs["underlying"] = selected
                    return empty_result
                raise RuntimeError("查詢成功，但沒有抓到任何權證資料。")

            result = pd.concat(
                kept_pages,
                ignore_index=True,
                sort=False
            )

            # 以權證代碼去重
            code_col = None
            for col in result.columns:
                compact = "".join(str(col).split())
                if compact in {"權證代碼", "warrant_code"}:
                    code_col = col
                    break

            if code_col is not None:
                result = result.drop_duplicates(
                    subset=[code_col],
                    keep="first"
                )

            result = result.reset_index(drop=True)
            result.attrs["symbol"] = resolved_symbol
            result.attrs["underlying"] = selected

            print(
                f"[crawler] 合併去重後共 {len(result)} 筆"
            )

            return result

        finally:
            await browser.close()


def crawl(symbol):
    return asyncio.run(crawl_warrants(symbol))

