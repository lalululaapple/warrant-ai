# Warrant AI v1.2

手機友善的元大權證查詢／評分 MVP。

## V1.2
- 輸入股票代號或名稱
- Playwright 開啟元大權證搜尋頁
- 查詢權證列表
- 解析公開欄位
- 粗篩與細篩兩階段顯示
- 權證評分排行榜
- Excel 匯出

不需要元大帳號密碼。

來源：
https://www.warrantwin.com.tw/eyuanta/Warrant/Search.aspx

## 安裝（Windows）
```bash
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r backend/requirements.txt
playwright install chromium
```

## 啟動
```bash
python -m backend.run
```

電腦：
http://127.0.0.1:8000

手機與電腦同 Wi-Fi 時：
http://你的電腦區網IP:8000

第一次測試請先輸入 3189。
