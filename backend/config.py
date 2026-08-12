from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
OUTPUT_DIR = BASE_DIR / "output"
SCREENSHOT_DIR = BASE_DIR / "screenshots"

YUANTA_SEARCH_URL = "https://www.warrantwin.com.tw/eyuanta/Warrant/Search.aspx"

HEADLESS = True
DEFAULT_TOP_N = 10

WEIGHTS = {
    "delta": 15,
    "theta": 10,
    "iv": 15,
    "leverage": 15,
    "days": 15,
    "spread": 15,
    "volume": 10,
    "moneyness": 5,
}

OUTPUT_DIR.mkdir(exist_ok=True)
SCREENSHOT_DIR.mkdir(exist_ok=True)
