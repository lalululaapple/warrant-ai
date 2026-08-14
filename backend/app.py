from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
import math
import traceback
import asyncio
import time
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

from .crawler import crawl_warrants
from .filter import coarse_filter_counts, filter_warrants
from .score import score_dataframe
from .report import export_excel

app = FastAPI(title="Warrant AI v1.1")

# Small in-memory cache: repeated searches avoid launching Chromium again.
# It is intentionally short so market data does not remain stale for long.
SEARCH_CACHE_TTL = 300
_search_cache = {}
_search_locks = {}
_crawler_lock = asyncio.Lock()
_active_client_searches = {}
_search_jobs = {}
_client_jobs = {}


class SearchRequest(BaseModel):
    symbol: str
    client_id: str = ""


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

.meta{
    color:#666;
    font-size:14px
}

.filter-summary{
    background:#f7f8fa;
    border-radius:10px;
    padding:10px 14px;
    margin:10px 0 14px;
    font-size:14px;
    line-height:1.7
}

.column-toggle{
    width:auto;
    padding:8px 12px;
    margin:0 0 10px;
    background:#555;
    font-size:14px
}

@media (max-width:700px){
    main{padding:10px}
    table{min-width:680px}
    .results-table:not(.show-all) th:nth-child(n+8):not(:nth-child(20)),
    .results-table:not(.show-all) td:nth-child(n+8):not(:nth-child(20)){
        display:none
    }
}
</style>
</head>

<body>

<main>

<div class="card">

<h1>Warrant AI v1.1</h1>

<p>
輸入股票代號或中文名稱，例如：2330、台積電
</p>

<input
    id="symbol"
    placeholder="例如：2330 或 台積電"
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

function toggleColumns(button){
    const table = document.querySelector(".results-table");
    if(!table){ return; }
    const expanded = table.classList.toggle("show-all");
    button.innerText = expanded ? "顯示精簡欄位" : "顯示完整欄位";
}

async function cancelSearch(){
    const jobId = localStorage.getItem("warrantJobId");
    if(!jobId){ return; }
    await fetch("/api/search/jobs/" + jobId + "/cancel", {method:"POST"});
    localStorage.removeItem("warrantJobId");
    localStorage.removeItem("warrantJobSymbol");
    document.getElementById("result").innerHTML =
        '<div class="card">搜尋已取消，可以重新輸入股票。</div>';
    const btn = document.getElementById("searchBtn");
    btn.disabled = false;
    btn.innerText = "開始搜尋";
}

async function search(resumeJobId){

    let symbol = document.getElementById("symbol").value.trim();
    if(resumeJobId){
        symbol = localStorage.getItem("warrantJobSymbol") || symbol;
        document.getElementById("symbol").value = symbol;
    }

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

        let clientId = localStorage.getItem("warrantClientId");
        if(!clientId){
            clientId = (window.crypto && crypto.randomUUID)
                ? crypto.randomUUID()
                : String(Date.now()) + Math.random();
            localStorage.setItem("warrantClientId", clientId);
        }

        let jobId = resumeJobId || "";
        if(!jobId){
            const started = await fetch("/api/search/start", {
                method:"POST",
                headers:{"Content-Type":"application/json"},
                body:JSON.stringify({symbol:symbol, client_id:clientId})
            });
            const startData = await started.json();
            if(!started.ok){
                throw new Error(startData.detail || "無法開始搜尋");
            }
            jobId = startData.job_id;
            localStorage.setItem("warrantJobId", jobId);
            localStorage.setItem("warrantJobSymbol", symbol);
        }

        let d = null;
        while(true){
            let statusResponse;
            try{
                statusResponse = await fetch("/api/search/jobs/" + jobId);
            }catch(_networkError){
                // Mobile browsers pause networking while the screen is off.
                // Keep the job id and retry after the browser wakes up.
                await new Promise(resolve => setTimeout(resolve, 2000));
                continue;
            }
            const statusData = await statusResponse.json();
            if(!statusResponse.ok){
                throw new Error(statusData.detail || "找不到背景搜尋工作");
            }
            if(statusData.status === "done"){
                d = statusData.result;
                break;
            }
            if(statusData.status === "failed" || statusData.status === "cancelled"){
                throw new Error(statusData.error || "搜尋失敗");
            }
            const current = statusData.current_page || 0;
            const total = statusData.total_pages || 0;
            const name = statusData.underlying || symbol;
            const progressText = total
                ? '正在搜尋 ' + name + '：已抓取 ' + current + ' / ' + total + ' 頁'
                : '正在準備 ' + name + ' 的權證資料…';
            document.getElementById("result").innerHTML =
                '<div class="card"><p>' + progressText + '</p>' +
                '<button type="button" onclick="cancelSearch()">取消搜尋</button></div>';
            await new Promise(resolve => setTimeout(resolve, 1500));
        }

        localStorage.removeItem("warrantJobId");
        localStorage.removeItem("warrantJobSymbol");

        let h =
            '<div class="card">' +
            '<h2>' +
            f(d.symbol || symbol) +
            ' 權證排行榜' +
            '</h2>';

        h +=
            '<p class="success">' +
            '共找到 ' +
            f(d.total) +
            ' 筆資料' +
            '</p>';

        h += '<p class="meta">資料來源：元大權證｜更新時間：' +
            f(d.updated_at) + '｜搜尋耗時：' + f2(d.elapsed_seconds) + ' 秒</p>';

        h += '<div class="filter-summary"><b>固定篩選條件</b><br>' +
            '價內 0～10%／價外 0～15%、行使比例 0.01～0.05、' +
            '剩餘天數 ≥ 60、買賣價差比 ≤ 1.5%、' +
            '實質槓桿 2～4 倍、成交價 &gt; 0</div>';

        const stats = d.filter_stats || {};
        h += '<div class="filter-summary"><b>篩選淘汰統計（累積通過）</b><br>' +
            '原始資料：' + f(stats.raw) + ' 檔 → ' +
            '價內外：' + f(stats.moneyness) + ' 檔 → ' +
            '成交價：' + f(stats.price) + ' 檔 → ' +
            '行使比例：' + f(stats.ratio) + ' 檔 → ' +
            '剩餘天數：' + f(stats.days) + ' 檔 → ' +
            '價差比：' + f(stats.spread) + ' 檔 → ' +
            '實質槓桿：' + f(stats.leverage) + ' 檔 → ' +
            '<b>最後符合：' + f(stats.final) + ' 檔</b></div>';

        h += '<button class="column-toggle" type="button" ' +
            'onclick="toggleColumns(this)">顯示完整欄位</button>';

        h +=
            '<div class="table-wrap">' +
            '<table class="results-table">' +

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

window.addEventListener("DOMContentLoaded", () => {
    document.getElementById("symbol").addEventListener("keydown", event => {
        if(event.key === "Enter" && !event.isComposing){
            event.preventDefault();
            const button = document.getElementById("searchBtn");
            if(!button.disabled){
                search();
            }
        }
    });

    const jobId = localStorage.getItem("warrantJobId");
    if(jobId){
        search(jobId);
    }
});

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


async def _run_search_job(job_id, req):
    job = _search_jobs[job_id]
    try:
        job["result"] = await search(req)
        job["status"] = "done"
    except asyncio.CancelledError:
        job["status"] = "cancelled"
        job["error"] = "搜尋已取消"
    except HTTPException as exc:
        job["status"] = "failed"
        job["error"] = str(exc.detail)
    except Exception as exc:
        job["status"] = "failed"
        job["error"] = str(exc)


@app.post("/api/search/start")
async def start_search(req: SearchRequest):
    symbol = req.symbol.strip()
    client_id = req.client_id.strip()[:100]
    if not symbol:
        raise HTTPException(status_code=400, detail="請輸入股票代號")

    # Keep completed background results long enough for a sleeping phone to
    # reconnect, without growing memory forever.
    cutoff = time.monotonic() - 1800
    for old_id, old_job in list(_search_jobs.items()):
        if old_job["status"] != "running" and old_job["created"] < cutoff:
            _search_jobs.pop(old_id, None)

    previous_id = _client_jobs.get(client_id) if client_id else None
    previous = _search_jobs.get(previous_id) if previous_id else None
    if previous and previous["status"] == "running":
        previous["task"].cancel()

    job_id = uuid.uuid4().hex
    job = {
        "status": "running",
        "symbol": symbol,
        "created": time.monotonic(),
        "result": None,
        "error": None,
        "current_page": 0,
        "total_pages": 0,
        "underlying": symbol,
    }
    _search_jobs[job_id] = job
    if client_id:
        _client_jobs[client_id] = job_id
    job["task"] = asyncio.create_task(_run_search_job(job_id, req))
    return {"job_id": job_id, "status": "running"}


@app.get("/api/search/jobs/{job_id}")
async def search_job_status(job_id: str):
    job = _search_jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="背景搜尋已不存在，請重新搜尋")
    return {
        "status": job["status"],
        "symbol": job["symbol"],
        "result": job["result"],
        "error": job["error"],
        "current_page": job["current_page"],
        "total_pages": job["total_pages"],
        "underlying": job["underlying"],
    }


@app.post("/api/search/jobs/{job_id}/cancel")
async def cancel_search_job(job_id: str):
    job = _search_jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="背景搜尋已不存在")
    if job["status"] == "running":
        job["task"].cancel()
    return {"status": "cancelled"}


@app.post("/api/search")
async def search(req: SearchRequest):

    symbol = req.symbol.strip()
    client_id = req.client_id.strip()[:100]

    if not symbol:
        raise HTTPException(
            status_code=400,
            detail="請輸入股票代號"
        )

    search_started = time.perf_counter()

    try:

        # Reloading/closing a page aborts the browser's wait but does not
        # automatically stop Playwright on the server. A new request from the
        # same device therefore replaces its abandoned search.
        previous_task = _active_client_searches.get(client_id)
        if client_id and previous_task and not previous_task.done():
            print(f"[crawler] 取消同一裝置的上一筆搜尋：{symbol}")
            previous_task.cancel()
            try:
                await previous_task
            except asyncio.CancelledError:
                pass
            # Give the abandoned request time to leave its global lock after
            # Playwright has closed Chromium.
            for _ in range(40):
                if not _crawler_lock.locked():
                    break
                await asyncio.sleep(0.05)

        # 1. 抓權證
        # 正式搜尋不存每一頁截圖，可大幅縮短多頁標的的等待時間。
        now = time.monotonic()
        cached = _search_cache.get(symbol)
        if cached and now - cached[0] < SEARCH_CACHE_TTL:
            df = cached[1].copy()
            print(f"[cache] 使用 {symbol} 的近期搜尋結果")
        else:
            # Same-symbol requests share one crawl and then reuse its cache.
            lock = _search_locks.setdefault(symbol, asyncio.Lock())
            async with lock:
                cached = _search_cache.get(symbol)
                if cached and time.monotonic() - cached[0] < SEARCH_CACHE_TTL:
                    df = cached[1].copy()
                else:
                    # Render Free cannot safely hold two Chromium instances.
                    # Reject a different cold search instead of allowing both
                    # workers to be killed by the platform.
                    if _crawler_lock.locked():
                        raise HTTPException(
                            status_code=429,
                            detail=(
                                "目前有另一筆搜尋正在進行，"
                                "請等它完成後再搜尋。"
                            ),
                        )
                    async with _crawler_lock:
                        def filter_page_with_stats(page_df):
                            return (
                                filter_warrants(page_df),
                                coarse_filter_counts(page_df),
                            )

                        async def report_progress(current, total, underlying):
                            job_id = _client_jobs.get(client_id)
                            job = _search_jobs.get(job_id) if job_id else None
                            if job and job["status"] == "running":
                                job["current_page"] = current
                                job["total_pages"] = total
                                job["underlying"] = underlying

                        crawl_task = asyncio.create_task(
                            crawl_warrants(
                                symbol,
                                save_screenshot=False,
                                page_filter=filter_page_with_stats,
                                progress_callback=report_progress,
                            )
                        )
                        if client_id:
                            _active_client_searches[client_id] = crawl_task
                        try:
                            df = await crawl_task
                        finally:
                            if (
                                client_id
                                and _active_client_searches.get(client_id)
                                is crawl_task
                            ):
                                _active_client_searches.pop(client_id, None)
                    df.attrs["updated_at"] = datetime.now(
                        ZoneInfo("Asia/Taipei")
                    ).strftime("%Y/%m/%d %H:%M:%S")
                    _search_cache[symbol] = (time.monotonic(), df.copy())

        # 2. 第一層硬條件粗篩
        filtered = filter_warrants(df)
        resolved_symbol = str(df.attrs.get("symbol", symbol))

        # 3. 評分
        scored = score_dataframe(filtered)

        # 4. 匯出 Excel
        path = export_excel(scored, resolved_symbol)

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

        elapsed_seconds = round(time.perf_counter() - search_started, 2)
        updated_at = df.attrs.get("updated_at") or datetime.now(
            ZoneInfo("Asia/Taipei")
        ).strftime("%Y/%m/%d %H:%M:%S")
        filter_stats = df.attrs.get("filter_stats") or coarse_filter_counts(df)
        print(
            f"[search] symbol={resolved_symbol} results={len(scored)} "
            f"elapsed={elapsed_seconds}s status=success"
        )

        return {
            "symbol": resolved_symbol,
            "total": int(len(scored)),
            "excel": str(path),
            "updated_at": updated_at,
            "elapsed_seconds": elapsed_seconds,
            "filter_stats": filter_stats,
            "results": records
        }

    except HTTPException:
        raise

    except Exception as exc:

        elapsed_seconds = round(time.perf_counter() - search_started, 2)
        print(
            f"[search] symbol={symbol} elapsed={elapsed_seconds}s "
            f"status=failed error={exc!r}"
        )

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

