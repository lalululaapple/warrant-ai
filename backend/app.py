from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
import math
import traceback

from .crawler import crawl_warrants
from .filter import filter_warrants
from .score import score_dataframe
from .report import export_excel

app = FastAPI(title="Warrant AI v1.1")


class SearchRequest(BaseModel):
    symbol: str


HTML = """
<!doctype html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Warrant AI v1.1</title>

<style>
body{
    font-family:system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
    background:#f4f5f7;
    margin:0;
    color:#222
}

main{
    max-width:1000px;
    margin:auto;
    padding:20px
}

.card{
    background:#fff;
    border-radius:16px;
    padding:18px;
    margin:12px 0;
    box-shadow:0 2px 12px #0001
}

input{
    width:100%;
    box-sizing:border-box;
    padding:14px;
    border:1px solid #ddd;
    border-radius:10px;
    font-size:18px
}

button{
    width:100%;
    padding:14px;
    margin-top:10px;
    border:0;
    border-radius:10px;
    background:#111;
    color:white;
    font-size:17px;
    cursor:pointer
}

button:disabled{
    opacity:.5
}

.table-wrap{
    overflow-x:auto
}

table{
    width:100%;
    border-collapse:collapse;
    font-size:13px;
    min-width:850px
}

th,td{
    padding:9px;
    border-bottom:1px solid #eee;
    text-align:right;
    white-space:nowrap
}

th:first-child,
td:first-child,
th:nth-child(2),
td:nth-child(2){
    text-align:left
}

.rank{
    font-weight:bold
}

.error{
    color:#b00020
}

.success{
    color:#087f23
}
</style>
</head>

<body>

<main>

<div class="card">

<h1>Warrant AI v1.1</h1>

<p>
輸入股票代號，例如：3189（景碩）
</p>

<input
    id="symbol"
    placeholder="輸入股票代號，例如 3189"
    inputmode="numeric"
/>

<button id="searchBtn" onclick="search()">
開始搜尋
</button>

</div>

<div id="result"></div>

<script>

function f(v){
    if(v === null || v === undefined || v === ""){
        return "--";
    }
    return v;
}

function f2(v){
    if(v === null || v === undefined || v === ""){
        return "--";
    }

    const n = Number(v);
    return Number.isFinite(n) ? n.toFixed(2) : v;
}

async function search(){

    const symbol =
        document.getElementById("symbol").value.trim();

    if(!symbol){
        return;
    }

    const btn =
        document.getElementById("searchBtn");

    btn.disabled = true;
    btn.innerText = "搜尋中，請稍候...";

    document.getElementById("result").innerHTML =
        '<div class="card">正在抓取元大權證資料，請稍候...</div>';

    try{

        const r = await fetch(
            "/api/search",
            {
                method:"POST",
                headers:{
                    "Content-Type":"application/json"
                },
                body:JSON.stringify({
                    symbol:symbol
                })
            }
        );

        const d = await r.json();

        if(!r.ok){
            throw new Error(
                d.detail || "搜尋失敗"
            );
        }

        let h =
            '<div class="card">' +
            '<h2>' +
            symbol +
            ' 權證排行榜' +
            '</h2>';

        h +=
            '<p class="success">' +
            '共找到 ' +
            f(d.total) +
            ' 筆資料' +
            '</p>';

        h +=
            '<div class="table-wrap">' +
            '<table>' +

            '<tr>' +
            '<th>排名</th>' +
            '<th>權證名稱</th>' +
            '<th>權證代碼</th>' +
            '<th>價格</th>' +
            '<th>剩餘天數</th>' +
            '<th>價內外</th>' +
            '<th>實質槓桿</th>' +
            '<th>隱波%</th>' +
            '<th>Delta</th>' +
            '<th>標準Delta</th>' +
            '<th>Theta</th>' +
            '<th>Theta損耗%</th>' +
            '<th>買價隱波%</th>' +
            '<th>賣價隱波%</th>' +
            '<th>隱波差</th>' +
            '<th>價差比%</th>' +
            '<th>Delta分</th>' +
            '<th>Theta分</th>' +
            '<th>IV分</th>' +
            '<th>Score</th>' +
            '</tr>';

        for(const x of d.results){

            h +=
                '<tr>' +

                '<td class="rank">' +
                f(x.rank) +
                '</td>' +

                '<td>' +
                f(x.warrant_name) +
                '</td>' +

                '<td>' +
                f(x.warrant_code) +
                '</td>' +

                '<td>' +
                f(x.price) +
                '</td>' +

                '<td>' +
                f(x.days) +
                '</td>' +

                '<td>' +
                f(x.moneyness) +
                '</td>' +

                '<td>' +
                f(x.leverage) +
                '</td>' +

                '<td>' +
                f(x.iv) +
                '</td>' +

                '<td>' +
                f(x.delta) +
                '</td>' +

                '<td>' +
                f2(x.normalized_delta) +
                '</td>' +

                '<td>' +
                f(x.theta) +
                '</td>' +

                '<td>' +
                f2(x.theta_decay_pct) +
                '</td>' +

                '<td>' +
                f(x.bid_iv) +
                '</td>' +

                '<td>' +
                f(x.ask_iv) +
                '</td>' +

                '<td>' +
                f2(x.iv_gap) +
                '</td>' +

                '<td>' +
                f(x.spread) +
                '</td>' +

                '<td>' +
                f2(x.score_delta) +
                '</td>' +

                '<td>' +
                f2(x.score_theta) +
                '</td>' +

                '<td>' +
                f2(x.score_iv) +
                '</td>' +

                '<td><b>' +
                f(x.score) +
                '</b></td>' +

                '</tr>';
        }

        h +=
            '</table>' +
            '</div>' +
            '</div>';

        document.getElementById("result")
            .innerHTML = h;

    }catch(e){

        document.getElementById("result").innerHTML =
            '<div class="card error">' +
            '<h3>搜尋失敗</h3>' +
            '<p>' +
            e.message +
            '</p>' +
            '</div>';

    }finally{

        btn.disabled = false;
        btn.innerText = "開始搜尋";
    }
}

</script>

</main>

</body>
</html>
"""


@app.get("/", response_class=HTMLResponse)
def index():
    return HTML


def clean_value(value):
    """
    將 Pandas / NumPy 的 NaN、Infinity
    轉成 JSON 可以安全處理的 None。
    """

    if value is None:
        return None

    try:
        if isinstance(value, float) and not math.isfinite(value):
            return None
    except Exception:
        pass

    return value


@app.post("/api/search")
async def search(req: SearchRequest):

    symbol = req.symbol.strip()

    if not symbol:
        raise HTTPException(
            status_code=400,
            detail="請輸入股票代號"
        )

    try:

        # 1. 抓權證
        # 正式搜尋不存每一頁截圖，可大幅縮短多頁標的的等待時間。
        df = await crawl_warrants(symbol, save_screenshot=False)

        # 2. 第一層硬條件粗篩
        filtered = filter_warrants(df)

        # 3. 評分
        scored = score_dataframe(filtered)

        # 4. 匯出 Excel
        path = export_excel(scored, symbol)

        # 5. 顯示全部符合條件的權證，保留排名順序
        results = scored.copy()

        # 6. NaN -> None
        records = []

        for _, row in results.iterrows():

            record = {}

            for col, value in row.items():

                record[col] = clean_value(value)

            # 對應前端使用的欄位名稱
            record["warrant_code"] = clean_value(
                row.get("warrant_code")
            )

            record["warrant_name"] = clean_value(
                row.get("warrant_name")
            )

            record["price"] = clean_value(
                row.get("price")
            )

            record["days"] = clean_value(
                row.get("days")
            )

            record["moneyness"] = clean_value(
                row.get("moneyness")
            )

            record["leverage"] = clean_value(
                row.get("leverage")
            )

            record["iv"] = clean_value(
                row.get("iv")
            )

            record["delta"] = clean_value(
                row.get("delta")
            )

            record["normalized_delta"] = clean_value(
                row.get("normalized_delta")
            )

            record["theta"] = clean_value(
                row.get("theta")
            )

            record["theta_decay_pct"] = clean_value(
                row.get("theta_decay_pct")
            )

            record["bid_iv"] = clean_value(
                row.get("bid_iv")
            )

            record["ask_iv"] = clean_value(
                row.get("ask_iv")
            )

            record["iv_gap"] = clean_value(
                row.get("iv_gap")
            )

            record["spread"] = clean_value(
                row.get("spread")
            )

            record["score"] = clean_value(
                row.get("score")
            )

            record["rank"] = clean_value(
                row.get("rank")
            )

            records.append(record)

        return {
            "symbol": symbol,
            "total": int(len(scored)),
            "excel": str(path),
            "results": records
        }

    except Exception as exc:

        print("")
        print("=" * 70)
        print("WARRANT AI ERROR")
        print("=" * 70)

        traceback.print_exc()

        print("=" * 70)
        print("ERROR:", repr(exc))
        print("=" * 70)
        print("")

        raise HTTPException(
            status_code=500,
            detail=str(exc)
        )
