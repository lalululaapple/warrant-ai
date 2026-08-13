import asyncio
import math
import re
from io import StringIO

import pandas as pd
from playwright.async_api import async_playwright

from .config import YUANTA_SEARCH_URL, HEADLESS, SCREENSHOT_DIR
from .parser import normalize_dataframe


def find_warrant_table(html):
    """æ‰¾å‡ºå…ƒå¤§æ¬Šè­‰çµæœè¡¨ã€‚"""
    try:
        tables = pd.read_html(StringIO(html))
    except ValueError:
        return None

    for df in tables:
        cols = "".join("".join(str(c).split()) for c in df.columns)
        if "æ¬Šè­‰ä»£ç¢¼" in cols and "æ¬Šè­‰åç¨±" in cols:
            return df

    return None


def _clean_result_df(df):
    if df is None or df.empty:
        return pd.DataFrame()

    df = normalize_dataframe(df)

    # ç§»é™¤ã€Œæ²’æœ‰ç›¸é—œæ¢ä»¶å•†å“ã€ç­‰éè³‡æ–™åˆ—
    for col in df.columns:
        compact = "".join(str(col).split())
        if compact in {"æ¬Šè­‰åç¨±", "warrant_name"}:
            bad = (
                df[col]
                .astype(str)
                .str.contains("æ²’æœ‰ç›¸é—œæ¢ä»¶å•†å“", na=False)
            )
            df = df.loc[~bad].copy()
            break

    return df.reset_index(drop=True)


async def _select_underlying(page, symbol):
    """è¼¸å…¥è‚¡ç¥¨ä»£è™Ÿä¸¦é»é¸è‡ªå‹•å®Œæˆå€™é¸ï¼Œä¾‹å¦‚ 3189 æ™¯ç¢©ã€‚"""
    target = page.locator(
        'input[placeholder="æ¨™çš„åç¨±/ä»£ç¢¼"]'
    ).nth(0)

    if not await target.is_visible():
        raise RuntimeError("æ‰¾ä¸åˆ°å¯ç”¨çš„ã€æ¨™çš„åç¨±/ä»£ç¢¼ã€è¼¸å…¥æ¡†ã€‚")

    await target.click()
    await target.fill(symbol)
    await page.wait_for_timeout(1200)

    # å€™é¸æ–‡å­—æ ¼å¼ä¾‹å¦‚ï¼š3189ã€€æ™¯ç¢©
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
        f"è¼¸å…¥ {symbol} å¾Œæ‰¾ä¸åˆ°æˆ–ç„¡æ³•é¸å–æ­£ç¢ºæ¨™çš„å€™é¸ã€‚"
    )


async def _click_search(page):
    buttons = page.get_by_text("æŸ¥è©¢", exact=True)

    for i in range(await buttons.count()):
        btn = buttons.nth(i)
        try:
            if await btn.is_visible():
                await btn.click()
                await page.wait_for_timeout(2500)
                return
        except Exception:
            pass

    raise RuntimeError("æ‰¾ä¸åˆ°å¯é»æ“Šçš„ã€æŸ¥è©¢ã€æŒ‰éˆ•ã€‚")


async def _add_result_columns(page):
    """Add Greeks and bid/ask IV to the search result table."""
    await page.get_by_text("+å¢åŠ é …ç›®", exact=True).click()
    popup = page.locator(".pop:visible")
    await popup.wait_for(state="visible")

    for column_name in ("DELTA", "THETA", "è²·åƒ¹éš±æ³¢", "è³£åƒ¹éš±æ³¢"):
        available_item = popup.get_by_text(column_name, exact=True).first
        if await available_item.is_visible():
            await available_item.click()
            await popup.get_by_text("æ–°å¢", exact=True).first.click()

    # å…ƒå¤§é é¢çš„é—œé–‰æ§åˆ¶æœ‰æ™‚è¢« Playwright åˆ¤å®šç‚ºä¸å¯è¦‹ï¼Œ
    # ç›´æ¥è§¸ç™¼å…¶åŸç”Ÿ clickï¼Œä»æœƒåŸ·è¡Œ Angular çš„ closePopup()ã€‚
    await popup.locator(".close").first.evaluate("element => element.click()")
    await page.wait_for_timeout(200)


async def _read_current_page(page):
    html = await page.content()
    return _clean_result_df(find_warrant_table(html))


def _page_signature(df):
    """Small fingerprint used to detect that pagination has finished."""
    if df is None or df.empty:
        return None
    for column in ("warrant_code", "æ¬Šè­‰ ä»£ç¢¼"):
        if column in df.columns:
            return str(df.iloc[0][column])
    return str(df.iloc[0].to_dict())


async def _wait_for_page_change(page, previous_smwß‹h‘éì¶»§q«^ud;ï#9`g9«h¹b!ºh xà ˆ‚ˆ
Bˆœ™XZÂ‚ˆ™]š[İ\×ÜÚYÛ˜]\™HHÜYÙWÜÚYÛ˜]\™J[ÜYÙ\ÖËLWJBˆ]ØZ][šË˜ÛXÚÊ
Bˆ›ˆH]ØZ]İØZ]Ù›Ü—ÜYÙWØÚ[™ÙJYÙK™]š[İ\×ÜÚYÛ˜]\™JB‚ˆYˆØ]™WÜØÜ™Y[œÚİ‚ˆ]ØZ]YÙKœØÜ™Y[œÚİ
ˆ]\İŠˆĞÔ‘QS”ÒÕÑTˆÂˆˆÜŞ[X›ÛWÜYÙ^ÜYÙWÛ›ßKœ™È‚ˆ
Kˆ[ÜYÙOUYBˆ
B‚ˆš[
ˆˆ–ØÜ˜]Û\—H9ë+ÜYÙWÛ›ßH:h y¢¤ùb,Û[Š›Š_H9ëaˆ‚ˆ
B‚ˆYˆ›‹™[\N‚ˆœ™XZÂ‚ˆ[ÜYÙ\Ë˜\[™
›ŠB‚ˆYˆ›İ[ÜYÙ\Î‚ˆ˜Z\ÙH[[YQ\œ›ÜŠ¹§éz*h¹¢$9b§ûï#9/a¹¬¤¹§"y¢¤ùb,9.îù/ey«"º+bz,áù¥¦xà ˆŠB‚ˆ™\İ[H˜ÛÛ˜Ø]
ˆ[ÜYÙ\ËˆYÛ›Ü™WÚ[™^UYKˆÛÜQ˜[ÙBˆ
B‚ˆÈ9.éy«"º+by.èùè¯9c®úaãBˆÛÙWØÛÛH›Û™Bˆ›ÜˆÛÛ[ˆ™\İ[˜ÛÛ[[œÎ‚ˆÛÛ\XİHˆ‹š›Ú[ŠİŠÛÛ
KœÜ]

JBˆYˆÛÛ\Xİ[ˆÈ¹«"º+by.èùè¯‹Ø\œ˜[ØÛÙHŸN‚ˆÛÙWØÛÛHÛÛˆœ™XZÂ‚ˆYˆÛÙWØÛÛ\È›İ›Û™N‚ˆ™\İ[H™\İ[™›ÜÙ\XØ]\ÊˆİXœÙ]VØÛÙWØÛÛKˆÙY\H™š\œİ‚ˆ
B‚ˆ™\İ[H™\İ[œ™\Ù]Ú[™^
›ÜUYJB‚ˆš[
ˆˆ–ØÜ˜]Û\—H9d"9/myc®úaãyo£9alHÛ[Š™\İ[
_H9ëaˆ‚ˆ
B‚ˆ™]\›ˆ™\İ[‚ˆš[˜[N‚ˆ]ØZ]œ›İÜÙ\‹˜ÛÜÙJ
B‚‚™YˆÜ˜]Û
Ş[X›Û
N‚ˆ™]\›ˆ\Ş[˜Ú[Ëœ[ŠÜ˜]ÛİØ\œ˜[ÊŞ[X›Û
JB