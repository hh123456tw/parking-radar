# Parking Hell Radar Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建立一套可在本機完整展示、可部署到 GCP 1 vCPU／1 GB VM 的臺北市即時停車難度、歷史分析、地址搜尋與 Gemini 限定對話系統。

**Architecture:** 使用單一 Flask 應用協調查詢，`collector.py` 將臺北市官方靜態與動態資料以官方停車場 ID 合併後寫入同機 MySQL；所有分數、距離、排名與歷史統計都由可測試的 Python 固定函式計算。Gemini 只把自然語言轉成三種結構化意圖，Nominatim 只負責地址轉座標並先查快取；前端維持單頁，以原生 JavaScript、Leaflet 與 Chart.js 顯示結果。

**Tech Stack:** Python 3.11+、Flask 3、PyMySQL、Pandas、Google Gen AI SDK、Pydantic 2、Requests、MySQL 8、HTML/CSS/JavaScript、Leaflet、Chart.js、pytest、Gunicorn、Nginx、Linux cron。

## Global Constraints

- 只處理臺北市官方路外停車場資料；不得加入新北市、路邊車格或個別民營業者爬蟲。
- 官方靜態資料 URL 固定為 `https://tcgbusfs.blob.core.windows.net/blobtcmsv/TCMSV_alldesc.json`。
- 官方動態資料 URL 固定為 `https://tcgbusfs.blob.core.windows.net/blobtcmsv/TCMSV_allavailable.json`。
- 實測資料結構皆為 `data.UPDATETIME` 與 `data.park`；靜態與動態資料使用 `id` 精確 JOIN，不做名稱或地址模糊配對。
- `EntranceCoord.EntrancecoordInfo[0].Xcod` 當作緯度、`Ycod` 當作經度；第一版不轉換 TWD97，缺少有效 WGS84 座標者不可參與地址附近推薦。
- 每 30 分鐘蒐集一次；即時推薦只採用 45 分鐘內、`total_spaces > 0` 且 `0 <= available_spaces <= total_spaces` 的資料。
- 資料庫時間一律存 UTC，顯示與日別判斷一律使用 `Asia/Taipei`。
- 地址搜尋半徑固定 1,500 公尺；距離使用 Haversine 直線距離，不做導航或道路距離。
- 有至少 3 筆同日別、同小時歷史樣本時使用 50／30／20；不足時使用 60／40，不稱為 AI 預測。
- Gemini 模型由環境變數設定，預設 `gemini-3.5-flash`；Gemini 只能輸出 `recommend`、`history`、`compare` 三種結構化意圖，不得產生 SQL 或停車數字。
- Nominatim 每秒最多 1 次、必須帶可辨識 User-Agent、禁止輸入即時自動完成，且必須先查 `geocode_cache`。
- 使用者介面只有一個頁面、一張 Leaflet 地圖與一張 Chart.js 歷史折線圖；不得增加登入、管理後台或第二個儀表板。
- 不使用 Docker、Cloudflare Workers、前端框架、ORM、Redis、背景工作佇列或額外架構層。
- 正式程式與測試含繁體中文註解目標 1,500～2,000 行，硬上限 2,500 行；每個程式檔盡量不超過 250 行、每個函式盡量不超過 30 行。
- 每個檔案開頭、每個函式及關鍵清洗／公式／SQL／例外分支都要有有意義的繁體中文註解；不得逐行重複程式本身已表達的內容。
- 自動測試不得呼叫真實臺北市 API、Gemini 或 Nominatim；一律使用 fixture 或 mock。

---

## Locked File Map and Line Budget

以下為鎖定的正式檔案。除 fixture、部署設定與文件外，不新增 production Python 模組；若單檔超出預算，先刪除非必要動畫或文字效果，不另建抽象層。

| File | Responsibility | Target lines |
|---|---|---:|
| `app.py` | Flask 路由、session、查詢流程與 JSON 輸出 | 190 |
| `config.py` | 環境變數與常數 | 60 |
| `collector.py` | 官方資料下載、解析、清洗、交易式保存 | 180 |
| `database.py` | PyMySQL 連線與固定參數化 SQL | 200 |
| `analysis.py` | 分數、距離、排行與歷史統計 | 190 |
| `ai_service.py` | Gemini 三種結構化意圖 | 120 |
| `geocoder.py` | Nominatim、節流與快取 | 100 |
| `schema.sql` | 三張表、索引與唯一限制 | 60 |
| `templates/index.html` | 唯一主頁 | 130 |
| `static/style.css` | 深色、橘紅警示、響應式排版 | 180 |
| `static/app.js` | 查詢、結果卡、地圖與折線圖 | 220 |
| `tests/test_core.py` | 核心、collector、外部服務與路由測試 | 220 |

輔助檔案：`requirements.txt`、`.env.example`、`README.md`、`tests/fixtures/taipei_static.json`、`tests/fixtures/taipei_dynamic.json`、`deploy/parking-radar.service`、`deploy/nginx-parking-radar.conf`。輔助檔案不建立新的應用分層。

## Shared Data Contracts

各模組傳遞普通 `dict`，避免為小型專題增加 models 檔。欄位名稱固定如下，不得在後續任務改名：

```python
# collector.py 解析後的停車場
lot = {
    "lot_id": "TPE0001",
    "lot_name": "台灣聯通長春停車場",
    "district": "中山區",
    "address": "長春路17號地下1層",
    "operator_type": "民營停車場",
    "total_spaces": 17,
    "fee_info": "計時：100元/時",
    "service_time": "00:00:00-23:59:59",
    "latitude": 25.0552,
    "longitude": 121.5242,
    "supports_realtime": True,
    "source_updated_at": datetime(..., tzinfo=timezone.utc),
}

# collector.py 解析後的有效快照
snapshot = {
    "lot_id": "TPE0001",
    "available_spaces": 8,
    "source_updated_at": datetime(..., tzinfo=timezone.utc),
    "captured_at": datetime(..., tzinfo=timezone.utc),
}

# analysis.py 排名後的候選停車場
candidate = {
    **lot,
    "available_spaces": 8,
    "captured_at": datetime(..., tzinfo=timezone.utc),
    "distance_m": 420.0,
    "hell_score": 52.94,
    "hell_label": "輕鬆停",
    "historical_hell_score": 65.0,
    "history_sample_count": 5,
    "recommendation_score": 63.53,
}
```

---

### Task 1: Project Foundation, Configuration, and Health Route

**Files:**
- Create: `requirements.txt`
- Create: `.env.example`
- Create: `config.py`
- Create: `app.py`
- Test: `tests/test_core.py`

**Interfaces:**
- Consumes: environment variables only.
- Produces: `Config`, `create_app(test_config: dict | None = None) -> Flask`, `GET /health`.

- [ ] **Step 1: Add dependency and environment contracts**

Create `requirements.txt` with only these runtime and test dependencies:

```text
Flask>=3.1,<4
PyMySQL>=1.1,<2
pandas>=2.2,<4
requests>=2.32,<3
google-genai>=1.0,<2
pydantic>=2.8,<3
python-dotenv>=1.0,<2
gunicorn>=23,<24
pytest>=8,<10
```

Create `.env.example`:

```dotenv
FLASK_SECRET_KEY=replace-with-a-long-random-string
MYSQL_HOST=127.0.0.1
MYSQL_PORT=3306
MYSQL_USER=parking_app
MYSQL_PASSWORD=replace-me
MYSQL_DATABASE=parking_hell
GEMINI_API_KEY=
GEMINI_MODEL=gemini-3.5-flash
NOMINATIM_USER_AGENT=parking-hell-radar-student-project/1.0 contact@example.com
```

- [ ] **Step 2: Write the failing configuration and health tests**

Create `tests/test_core.py` with the module docstring and first tests:

```python
"""停車地獄雷達核心測試；所有外部服務都使用固定資料或 mock。"""

from app import create_app
from config import Config


def test_config_has_locked_analysis_constants():
    """搜尋半徑與新鮮度必須符合設計規格，不受本機環境影響。"""
    assert Config.SEARCH_RADIUS_M == 1500
    assert Config.FRESHNESS_MINUTES == 45


def test_health_route_returns_ok():
    """健康檢查不依賴資料庫或外部 API。"""
    app = create_app({"TESTING": True, "SECRET_KEY": "test"})
    response = app.test_client().get("/health")
    assert response.status_code == 200
    assert response.get_json() == {"status": "ok"}
```

- [ ] **Step 3: Run tests and verify the expected failure**

Run: `python -m pytest tests/test_core.py -v`

Expected: collection fails with `ModuleNotFoundError: No module named 'app'` or `config`.

- [ ] **Step 4: Implement minimal config and app factory**

Create `config.py`:

```python
"""集中讀取環境變數與專題固定規則，避免設定散落在各模組。"""

import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    """提供 Flask、MySQL、Gemini、地址搜尋與分析所需設定。"""

    SECRET_KEY = os.getenv("FLASK_SECRET_KEY", "dev-only-change-me")
    MYSQL_HOST = os.getenv("MYSQL_HOST", "127.0.0.1")
    MYSQL_PORT = int(os.getenv("MYSQL_PORT", "3306"))
    MYSQL_USER = os.getenv("MYSQL_USER", "parking_app")
    MYSQL_PASSWORD = os.getenv("MYSQL_PASSWORD", "")
    MYSQL_DATABASE = os.getenv("MYSQL_DATABASE", "parking_hell")
    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
    GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash")
    NOMINATIM_USER_AGENT = os.getenv(
        "NOMINATIM_USER_AGENT", "parking-hell-radar-student-project/1.0"
    )
    SEARCH_RADIUS_M = 1500
    FRESHNESS_MINUTES = 45
    MIN_HISTORY_SAMPLES = 3
    HISTORY_LOOKBACK_DAYS = 30
```

Create the initial `app.py`:

```python
"""Flask 入口；集中協調查詢流程，不在路由內重寫分析公式。"""

from flask import Flask, jsonify
from config import Config


def create_app(test_config=None):
    """建立 Flask 應用，允許測試覆寫設定並回傳 app。"""
    app = Flask(__name__)
    app.config.from_object(Config)
    if test_config:
        app.config.update(test_config)

    @app.get("/health")
    def health():
        """回傳不依賴外部服務的程序健康狀態。"""
        return jsonify(status="ok")

    return app


app = create_app()
```

- [ ] **Step 5: Run tests and commit the foundation**

Run: `python -m pytest tests/test_core.py -v`

Expected: `2 passed`.

```bash
git add requirements.txt .env.example config.py app.py tests/test_core.py
git commit -m "chore: scaffold parking radar application"
```

---

### Task 2: Pure Parking Scores, Distance, and Recommendation Rules

**Files:**
- Create: `analysis.py`
- Modify: `tests/test_core.py`

**Interfaces:**
- Consumes: dictionaries containing `total_spaces`, `available_spaces`, optional coordinates and history values.
- Produces: `clean_available(total_spaces, available_spaces) -> int | None`, `hell_score(total_spaces, available_spaces) -> float | None`, `hell_label(score) -> str`, `district_hell_score(rows) -> float | None`, `haversine_m(...) -> float`, `distance_ease(distance_m, radius_m=1500) -> float`, `recommendation_score(current_hell, distance_m, historical_hell=None, radius_m=1500) -> float`, `rank_candidates(rows, destination_lat, destination_lon, radius_m=1500) -> list[dict]`.

- [ ] **Step 1: Add failing tests for cleaning and hell scores**

Append to `tests/test_core.py`:

```python
from analysis import clean_available, district_hell_score, hell_label, hell_score


def test_clean_available_rejects_special_and_impossible_values():
    """官方負數、缺值與剩餘數超過總數都不能進入計算。"""
    assert clean_available(100, -9) is None
    assert clean_available(100, None) is None
    assert clean_available(100, 101) is None
    assert clean_available(0, 0) is None
    assert clean_available(100, 20) == 20


def test_hell_score_and_label_use_fixed_thresholds():
    assert hell_score(200, 10) == 95.0
    assert hell_label(95.0) == "停車地獄"
    assert hell_label(80.0) == "很難停"
    assert hell_label(60.0) == "開始擠"
    assert hell_label(59.9) == "輕鬆停"


def test_district_score_is_weighted_by_spaces():
    rows = [
        {"total_spaces": 100, "available_spaces": 50},
        {"total_spaces": 300, "available_spaces": 30},
        {"total_spaces": 50, "available_spaces": -9},
    ]
    assert district_hell_score(rows) == 80.0
```

- [ ] **Step 2: Run the new tests and verify failure**

Run: `python -m pytest tests/test_core.py -k "clean_available or hell_score or district_score" -v`

Expected: collection fails because `analysis.py` does not exist.

- [ ] **Step 3: Implement cleaning and score functions**

Create `analysis.py` with these first functions:

```python
"""純分析函式：負責清洗、地獄指數、距離、推薦與歷史統計。"""

from math import asin, cos, radians, sin, sqrt


def clean_available(total_spaces, available_spaces):
    """驗證總格數與剩餘格數；有效時回傳 int，否則回傳 None。"""
    if total_spaces is None or available_spaces is None:
        return None
    total = int(total_spaces)
    available = int(available_spaces)
    if total <= 0 or available < 0 or available > total:
        return None
    return available


def hell_score(total_spaces, available_spaces):
    """依有效格數計算 0 到 100 的使用率分數，無效資料回傳 None。"""
    available = clean_available(total_spaces, available_spaces)
    if available is None:
        return None
    return round((int(total_spaces) - available) / int(total_spaces) * 100, 2)


def hell_label(score):
    """把地獄指數轉成人類可讀的四級標籤。"""
    if score >= 95:
        return "停車地獄"
    if score >= 80:
        return "很難停"
    if score >= 60:
        return "開始擠"
    return "輕鬆停"


def district_hell_score(rows):
    """以有效總車位加權計算行政區地獄指數，而非平均場站分數。"""
    valid = [
        row for row in rows
        if clean_available(row.get("total_spaces"), row.get("available_spaces"))
        is not None
    ]
    total = sum(int(row["total_spaces"]) for row in valid)
    if total == 0:
        return None
    available = sum(int(row["available_spaces"]) for row in valid)
    return round((total - available) / total * 100, 2)
```

- [ ] **Step 4: Add failing tests for distance and both recommendation formulas**

Append:

```python
from analysis import distance_ease, haversine_m, rank_candidates, recommendation_score


def test_haversine_and_radius_filter():
    distance = haversine_m(25.0330, 121.5654, 25.0330, 121.5704)
    assert 490 < distance < 520
    rows = [
        {"lot_id": "near", "latitude": 25.0330, "longitude": 121.5704,
         "total_spaces": 100, "available_spaces": 20},
        {"lot_id": "far", "latitude": 25.0330, "longitude": 121.5904,
         "total_spaces": 100, "available_spaces": 50},
    ]
    ranked = rank_candidates(rows, 25.0330, 121.5654)
    assert [row["lot_id"] for row in ranked] == ["near"]


def test_recommendation_weights_with_and_without_history():
    # 即時地獄 80 → 容易度 20；距離 750m → 容易度 50；歷史地獄 60 → 40。
    assert recommendation_score(80, 750, 60) == 33.0
    assert recommendation_score(80, 750, None) == 32.0
    assert distance_ease(1500) == 0.0
```

- [ ] **Step 5: Implement distance, ranking, and recommendation functions**

Add to `analysis.py`:

```python
def haversine_m(lat1, lon1, lat2, lon2):
    """用 Haversine 公式回傳兩組 WGS84 座標的直線公尺數。"""
    lat1, lon1, lat2, lon2 = map(float, (lat1, lon1, lat2, lon2))
    earth_radius_m = 6_371_000
    lat1_r, lat2_r = radians(lat1), radians(lat2)
    delta_lat = radians(lat2 - lat1)
    delta_lon = radians(lon2 - lon1)
    value = sin(delta_lat / 2) ** 2 + cos(lat1_r) * cos(lat2_r) * sin(delta_lon / 2) ** 2
    return earth_radius_m * 2 * asin(sqrt(value))


def distance_ease(distance_m, radius_m=1500):
    """把 0 到搜尋半徑的距離線性轉成 100 到 0 的容易度。"""
    return round(max(0.0, min(100.0, 100 * (1 - distance_m / radius_m))), 2)


def recommendation_score(current_hell, distance_m, historical_hell=None, radius_m=1500):
    """依歷史樣本是否存在，套用鎖定的 50/30/20 或 60/40 權重。"""
    current_ease = 100 - current_hell
    nearby_ease = distance_ease(distance_m, radius_m)
    if historical_hell is None:
        score = current_ease * 0.6 + nearby_ease * 0.4
    else:
        score = current_ease * 0.5 + nearby_ease * 0.3 + (100 - historical_hell) * 0.2
    return round(score, 2)


def rank_candidates(rows, destination_lat, destination_lon, radius_m=1500):
    """排除無效或超出半徑的場站，加入分析欄位並依推薦分數排序。"""
    candidates = []
    for row in rows:
        if clean_available(row.get("total_spaces"), row.get("available_spaces")) is None:
            continue
        if row.get("latitude") is None or row.get("longitude") is None:
            continue
        distance = haversine_m(destination_lat, destination_lon, row["latitude"], row["longitude"])
        if distance > radius_m:
            continue
        item = dict(row)
        item["distance_m"] = round(distance, 1)
        item["hell_score"] = hell_score(row["total_spaces"], row["available_spaces"])
        item["hell_label"] = hell_label(item["hell_score"])
        item["recommendation_score"] = recommendation_score(
            item["hell_score"], distance, row.get("historical_hell_score"), radius_m
        )
        candidates.append(item)
    return sorted(candidates, key=lambda item: item["recommendation_score"], reverse=True)
```

- [ ] **Step 6: Run all tests and commit the pure analysis rules**

Run: `python -m pytest tests/test_core.py -v`

Expected: all tests pass.

```bash
git add analysis.py tests/test_core.py
git commit -m "feat: add parking difficulty and ranking rules"
```

---

### Task 3: MySQL Schema and Parameterized Database Access

**Files:**
- Create: `schema.sql`
- Create: `database.py`
- Modify: `tests/test_core.py`

**Interfaces:**
- Consumes: `Config`, collector `lot` and `snapshot` dictionaries, normalized addresses.
- Produces: `get_connection()`, `upsert_parking_lots(connection, lots) -> int`, `insert_snapshots(connection, snapshots) -> int`, `fetch_current_lots(connection, district=None) -> list[dict]`, `fetch_history(connection, lot_id, start_utc, end_utc) -> list[dict]`, `get_cached_geocode(connection, normalized_address) -> dict | None`, `save_cached_geocode(connection, result) -> None`.

- [ ] **Step 1: Create the exact three-table schema**

Create `schema.sql`:

```sql
-- 停車場基本資料會隨官方靜態檔更新，不保存可重新計算的分數。
CREATE TABLE IF NOT EXISTS parking_lots (
    lot_id VARCHAR(32) PRIMARY KEY,
    lot_name VARCHAR(120) NOT NULL,
    district VARCHAR(20) NOT NULL,
    address VARCHAR(255) NOT NULL,
    operator_type VARCHAR(40) NOT NULL,
    total_spaces INT NOT NULL,
    fee_info TEXT,
    service_time VARCHAR(80),
    latitude DECIMAL(10, 7),
    longitude DECIMAL(10, 7),
    supports_realtime BOOLEAN NOT NULL DEFAULT FALSE,
    source_updated_at DATETIME NULL,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_lots_district (district)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 每筆快照只保存官方格數與時間；負數特殊狀態不寫入此表。
CREATE TABLE IF NOT EXISTS parking_snapshots (
    snapshot_id BIGINT AUTO_INCREMENT PRIMARY KEY,
    lot_id VARCHAR(32) NOT NULL,
    available_spaces INT NOT NULL,
    source_updated_at DATETIME NOT NULL,
    captured_at DATETIME NOT NULL,
    CONSTRAINT fk_snapshots_lot FOREIGN KEY (lot_id)
        REFERENCES parking_lots(lot_id),
    CONSTRAINT uq_lot_source_time UNIQUE (lot_id, source_updated_at),
    INDEX idx_snapshots_lot_captured (lot_id, captured_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 相同正規化地址只向 Nominatim 查詢一次。
CREATE TABLE IF NOT EXISTS geocode_cache (
    normalized_address VARCHAR(255) PRIMARY KEY,
    display_address VARCHAR(255) NOT NULL,
    latitude DECIMAL(10, 7) NOT NULL,
    longitude DECIMAL(10, 7) NOT NULL,
    cached_at DATETIME NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
```

- [ ] **Step 2: Add failing database tests with a fake cursor**

Append a small `FakeConnection` and these assertions to `tests/test_core.py`:

```python
from database import fetch_current_lots, insert_snapshots


class FakeCursor:
    """記錄 SQL 與參數，讓測試不需要真的啟動 MySQL。"""
    def __init__(self, rows=None):
        self.rows = rows or []
        self.calls = []
        self.rowcount = 0

    def execute(self, sql, params=None):
        self.calls.append((sql, params))

    def executemany(self, sql, params):
        values = list(params)
        self.calls.append((sql, values))
        self.rowcount = len(values)

    def fetchall(self):
        return self.rows

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class FakeConnection:
    def __init__(self, rows=None):
        self.fake_cursor = FakeCursor(rows)

    def cursor(self):
        return self.fake_cursor


def test_insert_snapshots_uses_bulk_parameterized_sql():
    connection = FakeConnection()
    count = insert_snapshots(connection, [{
        "lot_id": "TPE0001", "available_spaces": 8,
        "source_updated_at": "2026-08-03 10:00:00",
        "captured_at": "2026-08-03 10:01:00",
    }])
    sql, params = connection.fake_cursor.calls[0]
    assert "%s" in sql and "ON DUPLICATE KEY" in sql
    assert params[0][0] == "TPE0001"
    assert count == 1


def test_fetch_current_lots_passes_freshness_and_district_as_parameters():
    connection = FakeConnection([{"lot_id": "TPE0001"}])
    rows = fetch_current_lots(connection, "信義區", freshness_minutes=45)
    sql, params = connection.fake_cursor.calls[0]
    assert "ROW_NUMBER()" in sql
    assert params == (45, "信義區")
    assert rows == [{"lot_id": "TPE0001"}]
```

- [ ] **Step 3: Run database tests and verify failure**

Run: `python -m pytest tests/test_core.py -k "snapshots or current_lots" -v`

Expected: collection fails because `database.py` does not exist.

- [ ] **Step 4: Implement connection and fixed SQL functions**

Create `database.py`. Use `pymysql.cursors.DictCursor`, `autocommit=False`, and these exact SQL patterns:

```python
"""集中管理 PyMySQL 連線與固定 SQL；所有外部值都以參數傳入。"""

import pymysql
from config import Config


def get_connection():
    """建立 UTF-8、DictCursor、手動交易的 MySQL 連線。"""
    return pymysql.connect(
        host=Config.MYSQL_HOST, port=Config.MYSQL_PORT,
        user=Config.MYSQL_USER, password=Config.MYSQL_PASSWORD,
        database=Config.MYSQL_DATABASE, charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor, autocommit=False,
    )


def insert_snapshots(connection, snapshots):
    """批次新增快照；同場站與官方時間重複時不重複累積。"""
    sql = """
        INSERT INTO parking_snapshots
            (lot_id, available_spaces, source_updated_at, captured_at)
        VALUES (%s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE lot_id = VALUES(lot_id)
    """
    values = [(
        row["lot_id"], row["available_spaces"],
        row["source_updated_at"], row["captured_at"],
    ) for row in snapshots]
    with connection.cursor() as cursor:
        cursor.executemany(sql, values)
        return cursor.rowcount


def fetch_current_lots(connection, district=None, freshness_minutes=45):
    """取得每個停車場 45 分鐘內最新有效快照，可選擇行政區。"""
    sql = """
        SELECT * FROM (
            SELECT l.*, s.available_spaces, s.source_updated_at,
                   s.captured_at,
                   ROW_NUMBER() OVER (
                       PARTITION BY l.lot_id ORDER BY s.captured_at DESC
                   ) AS row_num
            FROM parking_lots l
            JOIN parking_snapshots s ON s.lot_id = l.lot_id
            WHERE s.captured_at >= UTC_TIMESTAMP() - INTERVAL %s MINUTE
              AND l.supports_realtime = TRUE
        ) latest
        WHERE row_num = 1
    """
    params = [freshness_minutes]
    if district:
        sql += " AND district = %s"
        params.append(district)
    with connection.cursor() as cursor:
        cursor.execute(sql, tuple(params))
        return list(cursor.fetchall())
```

In the same file, implement:

```python
def upsert_parking_lots(connection, lots):
    """以官方 lot_id 批次新增或更新基本資料，回傳受影響列數。"""
    sql = """
        INSERT INTO parking_lots
            (lot_id, lot_name, district, address, operator_type,
             total_spaces, fee_info, service_time, latitude, longitude,
             supports_realtime, source_updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            lot_name=VALUES(lot_name), district=VALUES(district),
            address=VALUES(address), operator_type=VALUES(operator_type),
            total_spaces=VALUES(total_spaces), fee_info=VALUES(fee_info),
            service_time=VALUES(service_time), latitude=VALUES(latitude),
            longitude=VALUES(longitude),
            supports_realtime=VALUES(supports_realtime),
            source_updated_at=VALUES(source_updated_at)
    """
    keys = ("lot_id", "lot_name", "district", "address", "operator_type",
            "total_spaces", "fee_info", "service_time", "latitude", "longitude",
            "supports_realtime", "source_updated_at")
    values = [tuple(row.get(key) for key in keys) for row in lots]
    with connection.cursor() as cursor:
        cursor.executemany(sql, values)
        return cursor.rowcount


def fetch_history(connection, lot_id, start_utc, end_utc):
    """取得單一停車場指定 UTC 區間的格數及總格數。"""
    sql = """
        SELECT s.lot_id, s.available_spaces, s.captured_at, l.total_spaces
        FROM parking_snapshots s
        JOIN parking_lots l ON l.lot_id = s.lot_id
        WHERE s.lot_id = %s AND s.captured_at BETWEEN %s AND %s
        ORDER BY s.captured_at
    """
    with connection.cursor() as cursor:
        cursor.execute(sql, (lot_id, start_utc, end_utc))
        return list(cursor.fetchall())


def get_cached_geocode(connection, normalized_address):
    """依主鍵查詢地址快取；不存在時回傳 None。"""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT * FROM geocode_cache WHERE normalized_address = %s",
            (normalized_address,),
        )
        rows = list(cursor.fetchall())
        return rows[0] if rows else None


def save_cached_geocode(connection, result):
    """新增或更新地址快取，呼叫端負責 commit 或 rollback。"""
    sql = """
        INSERT INTO geocode_cache
            (normalized_address, display_address, latitude, longitude, cached_at)
        VALUES (%s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE display_address=VALUES(display_address),
            latitude=VALUES(latitude), longitude=VALUES(longitude),
            cached_at=VALUES(cached_at)
    """
    with connection.cursor() as cursor:
        cursor.execute(sql, (
            result["normalized_address"], result["display_address"],
            result["latitude"], result["longitude"], result["cached_at"],
        ))
```

- [ ] **Step 5: Run tests, inspect schema, and commit**

Run:

```bash
python -m pytest tests/test_core.py -v
python -m compileall app.py config.py analysis.py database.py
```

Expected: tests pass and compileall reports no syntax errors.

```bash
git add schema.sql database.py tests/test_core.py
git commit -m "feat: add parking data schema and queries"
```

---

### Task 4: Taipei Official Data Collector with Atomic Writes

**Files:**
- Create: `collector.py`
- Create: `tests/fixtures/taipei_static.json`
- Create: `tests/fixtures/taipei_dynamic.json`
- Modify: `tests/test_core.py`

**Interfaces:**
- Consumes: official JSON `data.UPDATETIME` and `data.park`, plus `database.upsert_parking_lots` and `database.insert_snapshots`.
- Produces: `parse_source_time(value) -> datetime`, `parse_static(payload, realtime_ids) -> list[dict]`, `parse_dynamic(payload, captured_at) -> list[dict]`, `fetch_json(url, timeout=15) -> dict`, `collect_once() -> dict[str, int]`.

- [ ] **Step 1: Add representative fixed API fixtures**

Create `tests/fixtures/taipei_static.json`:

```json
{"data":{"UPDATETIME":"2026-08-03T18:00:00+08:00","park":[
  {"id":"TPE0001","area":"中山區","name":"測試民營停車場","type2":"民營停車場","address":"長春路17號地下1層","payex":"100元/時","serviceTime":"00:00:00-23:59:59","totalcar":17,"EntranceCoord":{"EntrancecoordInfo":[{"Xcod":"25.0552","Ycod":"121.5242","Address":"入口"}]}},
  {"id":"139","area":"士林區","name":"無即時資料停車場","address":"福國路70號","payex":"","totalcar":30,"EntranceCoord":{}}
]}}
```

Create `tests/fixtures/taipei_dynamic.json`:

```json
{"data":{"UPDATETIME":"2026-08-03T18:00:00+08:00","park":[
  {"id":"TPE0001","availablecar":8},
  {"id":"TPE0002","availablecar":-9},
  {"id":"TPE0003","availablecar":999}
]}}
```

- [ ] **Step 2: Add failing parser tests**

Append:

```python
import json
from datetime import datetime, timezone
from pathlib import Path
from collector import parse_dynamic, parse_static

FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(name):
    """讀取固定官方格式，讓測試不依賴網路內容。"""
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_static_parser_uses_exact_id_and_wgs84_entrance():
    payload = load_fixture("taipei_static.json")
    lots = parse_static(payload, {"TPE0001"})
    assert lots[0]["lot_id"] == "TPE0001"
    assert lots[0]["operator_type"] == "民營停車場"
    assert lots[0]["latitude"] == 25.0552
    assert lots[0]["longitude"] == 121.5242
    assert lots[0]["supports_realtime"] is True
    assert lots[1]["supports_realtime"] is False


def test_dynamic_parser_keeps_only_nonnegative_values():
    payload = load_fixture("taipei_dynamic.json")
    captured = datetime(2026, 8, 3, 10, 1, tzinfo=timezone.utc)
    snapshots = parse_dynamic(payload, captured)
    assert snapshots == [{
        "lot_id": "TPE0001", "available_spaces": 8,
        "source_updated_at": datetime(2026, 8, 3, 10, 0, tzinfo=timezone.utc),
        "captured_at": captured,
    }, {
        "lot_id": "TPE0003", "available_spaces": 999,
        "source_updated_at": datetime(2026, 8, 3, 10, 0, tzinfo=timezone.utc),
        "captured_at": captured,
    }]
```

- [ ] **Step 3: Run parser tests and verify failure**

Run: `python -m pytest tests/test_core.py -k "static_parser or dynamic_parser" -v`

Expected: collection fails because `collector.py` does not exist.

- [ ] **Step 4: Implement strict parsing without TWD97 conversion**

Create `collector.py` with constants and parsers:

```python
"""下載臺北市官方停車資料，清洗後以單一交易保存。"""

import argparse
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import requests
from analysis import clean_available
from database import get_connection, insert_snapshots, upsert_parking_lots

STATIC_URL = "https://tcgbusfs.blob.core.windows.net/blobtcmsv/TCMSV_alldesc.json"
DYNAMIC_URL = "https://tcgbusfs.blob.core.windows.net/blobtcmsv/TCMSV_allavailable.json"
TAIPEI_TZ = ZoneInfo("Asia/Taipei")


def parse_source_time(value):
    """將官方 ISO 時間轉成帶 UTC 時區的 datetime。"""
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=TAIPEI_TZ)
    return parsed.astimezone(timezone.utc)


def _entrance_coordinates(raw):
    """讀取第一個入口 WGS84 座標；格式不完整時回傳兩個 None。"""
    items = raw.get("EntranceCoord", {}).get("EntrancecoordInfo", [])
    if not items:
        return None, None
    try:
        latitude = float(items[0]["Xcod"])
        longitude = float(items[0]["Ycod"])
    except (KeyError, TypeError, ValueError):
        return None, None
    if not (24.8 <= latitude <= 25.3 and 121.3 <= longitude <= 121.8):
        return None, None
    return latitude, longitude


def parse_static(payload, realtime_ids):
    """把官方靜態欄位統一成 parking_lots 所需格式。"""
    updated_at = parse_source_time(payload["data"]["UPDATETIME"])
    lots = []
    for raw in payload["data"]["park"]:
        latitude, longitude = _entrance_coordinates(raw)
        lots.append({
            "lot_id": str(raw["id"]), "lot_name": raw.get("name", "未命名停車場"),
            "district": raw.get("area", "未知"), "address": raw.get("address", ""),
            "operator_type": raw.get("type2", "未標示"),
            "total_spaces": int(raw.get("totalcar") or 0),
            "fee_info": raw.get("payex", ""), "service_time": raw.get("serviceTime", ""),
            "latitude": latitude, "longitude": longitude,
            "supports_realtime": str(raw["id"]) in realtime_ids,
            "source_updated_at": updated_at,
        })
    return lots


def parse_dynamic(payload, captured_at):
    """保留非負汽車剩餘格數；總格數合理性在兩份資料合併後再次檢查。"""
    source_time = parse_source_time(payload["data"]["UPDATETIME"])
    return [{
        "lot_id": str(raw["id"]),
        "available_spaces": int(raw["availablecar"]),
        "source_updated_at": source_time,
        "captured_at": captured_at,
    } for raw in payload["data"]["park"]
      if raw.get("availablecar") is not None and int(raw["availablecar"]) >= 0]


def fetch_json(url, timeout=15):
    """下載 JSON 並在 HTTP 或 JSON 錯誤時直接拋出例外。"""
    response = requests.get(url, timeout=timeout)
    response.raise_for_status()
    return response.json()
```

- [ ] **Step 5: Add a failing atomic collection test**

Append a test that monkeypatches downloads and database functions:

```python
import collector


def test_collect_once_filters_available_over_total_and_commits(monkeypatch):
    static = load_fixture("taipei_static.json")
    dynamic = load_fixture("taipei_dynamic.json")
    connection = type("Connection", (), {
        "committed": False, "rolled_back": False,
        "commit": lambda self: setattr(self, "committed", True),
        "rollback": lambda self: setattr(self, "rolled_back", True),
        "close": lambda self: None,
    })()
    monkeypatch.setattr(collector, "fetch_json", lambda url, timeout=15: static if "alldesc" in url else dynamic)
    monkeypatch.setattr(collector, "get_connection", lambda: connection)
    monkeypatch.setattr(collector, "upsert_parking_lots", lambda conn, rows: len(rows))
    saved = []
    monkeypatch.setattr(collector, "insert_snapshots", lambda conn, rows: saved.extend(rows) or len(rows))
    result = collector.collect_once()
    assert [row["lot_id"] for row in saved] == ["TPE0001"]
    assert result == {"lots": 2, "snapshots": 1}
    assert connection.committed is True
```

- [ ] **Step 6: Implement the one-shot transaction and CLI**

Add:

```python
def collect_once():
    """先完整下載與清洗兩份資料，再以單一交易寫入，避免半套快照。"""
    static_payload = fetch_json(STATIC_URL)
    dynamic_payload = fetch_json(DYNAMIC_URL)
    captured_at = datetime.now(timezone.utc)
    raw_snapshots = parse_dynamic(dynamic_payload, captured_at)
    # 即使官方回傳 -9 等狀態，該場站仍屬於支援即時資料，只是不參與本次計算。
    realtime_ids = {str(row["id"]) for row in dynamic_payload["data"]["park"]}
    lots = parse_static(static_payload, realtime_ids)
    totals = {row["lot_id"]: row["total_spaces"] for row in lots}
    snapshots = [row for row in raw_snapshots
                 if clean_available(totals.get(row["lot_id"]), row["available_spaces"]) is not None]
    connection = get_connection()
    try:
        upsert_parking_lots(connection, lots)
        inserted = insert_snapshots(connection, snapshots)
        connection.commit()
        return {"lots": len(lots), "snapshots": inserted}
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="蒐集一次臺北市停車資料")
    parser.add_argument("--once", action="store_true", help="執行一次後結束")
    args = parser.parse_args()
    if args.once:
        print(collect_once())
```

- [ ] **Step 7: Run tests and commit the collector**

Run:

```bash
python -m pytest tests/test_core.py -v
python -m compileall collector.py
```

Expected: all tests pass; no test makes an HTTP request.

```bash
git add collector.py tests/test_core.py tests/fixtures
git commit -m "feat: collect and clean taipei parking data"
```

---

### Task 5: Historical Hour Analysis and Seven-Day Series

**Files:**
- Modify: `analysis.py`
- Modify: `tests/test_core.py`

**Interfaces:**
- Consumes: `fetch_history` rows with UTC `captured_at`, `total_spaces`, and `available_spaces`; a target arrival datetime.
- Produces: `day_type(local_dt) -> str`, `summarize_matching_history(rows, arrival_time, min_samples=3) -> dict`, `summarize_hour_comparison(rows, hour, min_samples=3) -> dict`, `build_history_series(rows) -> list[dict]`.

- [ ] **Step 1: Add failing history tests**

Append:

```python
from datetime import timedelta
from analysis import (build_history_series, summarize_hour_comparison,
                      summarize_matching_history)


def history_row(local_day, hour, available):
    """建立臺北時間樣本，再轉成資料庫使用的 UTC。"""
    local = datetime(2026, 8, local_day, hour, tzinfo=ZoneInfo("Asia/Taipei"))
    return {"captured_at": local.astimezone(timezone.utc),
            "total_spaces": 100, "available_spaces": available}


def test_history_requires_three_same_day_type_and_hour_samples():
    arrival = datetime(2026, 8, 8, 18, tzinfo=ZoneInfo("Asia/Taipei"))
    insufficient = [history_row(1, 18, 10), history_row(2, 18, 20)]
    assert summarize_matching_history(insufficient, arrival)["hell_score"] is None
    enough = insufficient + [history_row(8, 18, 30)]
    summary = summarize_matching_history(enough, arrival)
    assert summary == {"hell_score": 80.0, "sample_count": 3, "day_type": "weekend", "hour": 18}


def test_history_series_has_iso_time_and_available_spaces():
    rows = [history_row(1, 18, 10)]
    point = build_history_series(rows)[0]
    assert point["available_spaces"] == 10
    assert point["captured_at"].endswith("+08:00")


def test_weekday_weekend_comparison_reports_both_groups():
    rows = [history_row(day, 18, 50) for day in (3, 4, 5)]
    rows += [history_row(day, 18, 20) for day in (1, 2, 8)]
    comparison = summarize_hour_comparison(rows, 18)
    assert comparison["weekday"] == {"hell_score": 50.0, "sample_count": 3}
    assert comparison["weekend"] == {"hell_score": 80.0, "sample_count": 3}
```

Add imports at the top of the test file:

```python
from zoneinfo import ZoneInfo
```

- [ ] **Step 2: Run the focused tests and verify failure**

Run: `python -m pytest tests/test_core.py -k "history_requires or history_series or weekday_weekend" -v`

Expected: import error for the new functions.

- [ ] **Step 3: Implement same-hour weekday/weekend statistics**

Add to `analysis.py`. This is the one deliberate Pandas use in the project: it demonstrates tabular filtering and aggregation without spreading DataFrames through the whole application.

```python
import pandas as pd
from datetime import datetime
from zoneinfo import ZoneInfo

TAIPEI_TZ = ZoneInfo("Asia/Taipei")


def day_type(local_dt):
    """星期一至五回傳 weekday，星期六日回傳 weekend。"""
    return "weekday" if local_dt.weekday() < 5 else "weekend"


def summarize_matching_history(rows, arrival_time, min_samples=3):
    """計算與抵達時間同日別、同整點小時的平均地獄指數。"""
    local_arrival = arrival_time.astimezone(TAIPEI_TZ)
    target_type = day_type(local_arrival)
    target_hour = local_arrival.hour
    frame = pd.DataFrame(rows)
    if frame.empty:
        return {"hell_score": None, "sample_count": 0,
                "day_type": target_type, "hour": target_hour}
    frame["local_time"] = pd.to_datetime(frame["captured_at"], utc=True).dt.tz_convert("Asia/Taipei")
    frame["day_type"] = frame["local_time"].dt.weekday.map(
        lambda value: "weekday" if value < 5 else "weekend")
    frame["hour"] = frame["local_time"].dt.hour
    frame["hell_score"] = frame.apply(
        lambda row: hell_score(row["total_spaces"], row["available_spaces"]), axis=1)
    matched = frame[(frame["day_type"] == target_type) &
                    (frame["hour"] == target_hour)]["hell_score"].dropna()
    result = {"hell_score": None, "sample_count": int(len(matched)),
              "day_type": target_type, "hour": target_hour}
    if len(matched) >= min_samples:
        result["hell_score"] = round(float(matched.mean()), 2)
    return result


def summarize_hour_comparison(rows, hour, min_samples=3):
    """比較指定整點的平日與週末平均分數；各組都獨立套用三筆門檻。"""
    results = {kind: {"hell_score": None, "sample_count": 0}
               for kind in ("weekday", "weekend")}
    for kind, target in (
        ("weekday", datetime(2024, 1, 1, hour, tzinfo=TAIPEI_TZ)),
        ("weekend", datetime(2024, 1, 6, hour, tzinfo=TAIPEI_TZ)),
    ):
        summary = summarize_matching_history(rows, target, min_samples)
        results[kind] = {"hell_score": summary["hell_score"],
                         "sample_count": summary["sample_count"]}
    return results


def build_history_series(rows):
    """將最近七天快照轉成 Chart.js 可直接使用的臺北時間序列。"""
    return [{
        "captured_at": row["captured_at"].astimezone(TAIPEI_TZ).isoformat(),
        "available_spaces": int(row["available_spaces"]),
    } for row in rows if clean_available(row["total_spaces"], row["available_spaces"]) is not None]
```

- [ ] **Step 4: Run all tests and commit**

Run: `python -m pytest tests/test_core.py -v`

Expected: all tests pass, including the exact three-sample boundary.

```bash
git add analysis.py tests/test_core.py
git commit -m "feat: add historical parking analysis"
```

---

### Task 6: Cached Nominatim Address Geocoding

**Files:**
- Create: `geocoder.py`
- Modify: `tests/test_core.py`

**Interfaces:**
- Consumes: raw address, database connection, `database.get_cached_geocode`, `database.save_cached_geocode`, Nominatim JSON.
- Produces: `normalize_address(address) -> str`, `geocode_address(address, connection, http_get=requests.get) -> dict | None`.

- [ ] **Step 1: Add failing cache-hit and HTTP parsing tests**

Append:

```python
from geocoder import geocode_address, normalize_address


def test_normalize_address_adds_taipei_and_removes_spaces():
    assert normalize_address(" 信義區 忠孝東路五段 7 號 ") == "臺北市信義區忠孝東路五段7號"
    assert normalize_address("台北市中山區長春路17號") == "臺北市中山區長春路17號"


def test_geocoder_returns_cache_without_http(monkeypatch):
    cached = {"normalized_address": "臺北市信義區市府路1號", "display_address": "臺北市政府",
              "latitude": 25.0375, "longitude": 121.5637}
    monkeypatch.setattr("geocoder.get_cached_geocode", lambda conn, key: cached)
    result = geocode_address("市府路1號", object(), http_get=lambda *_a, **_k: (_ for _ in ()).throw(AssertionError()))
    assert result == cached


def test_geocoder_parses_first_taipei_result_and_saves(monkeypatch):
    monkeypatch.setattr("geocoder.get_cached_geocode", lambda conn, key: None)
    saved = []
    monkeypatch.setattr("geocoder.save_cached_geocode", lambda conn, row: saved.append(row))
    response = type("Response", (), {
        "raise_for_status": lambda self: None,
        "json": lambda self: [{"display_name": "臺北市政府, 信義區, 臺北市", "lat": "25.0375", "lon": "121.5637"}],
    })()
    fake_connection = type("Connection", (), {"commit": lambda self: None})()
    result = geocode_address("市府路1號", fake_connection, http_get=lambda *_a, **_k: response)
    assert result["latitude"] == 25.0375
    assert saved[0]["normalized_address"] == "臺北市市府路1號"
```

- [ ] **Step 2: Run focused tests and verify failure**

Run: `python -m pytest tests/test_core.py -k "normalize_address or geocoder" -v`

Expected: collection fails because `geocoder.py` does not exist.

- [ ] **Step 3: Implement cache-first, one-request geocoding**

Create `geocoder.py`:

```python
"""免費地址轉座標：先查 MySQL 快取，再以受限制的 Nominatim 請求補齊。"""

import re
import time
from datetime import datetime, timezone
import requests
from config import Config
from database import get_cached_geocode, save_cached_geocode

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
_last_request_at = 0.0


def normalize_address(address):
    """統一台／臺、移除空白，並在缺少城市時補上臺北市。"""
    normalized = re.sub(r"\s+", "", address.strip()).replace("台北市", "臺北市")
    if not normalized.startswith("臺北市"):
        normalized = "臺北市" + normalized
    return normalized


def _respect_rate_limit():
    """確保同一程序兩次公共 Nominatim 請求至少間隔一秒。"""
    global _last_request_at
    wait_seconds = 1.0 - (time.monotonic() - _last_request_at)
    if wait_seconds > 0:
        time.sleep(wait_seconds)
    _last_request_at = time.monotonic()


def geocode_address(address, connection, http_get=requests.get):
    """回傳快取或第一筆臺北市座標；查無結果時回傳 None。"""
    key = normalize_address(address)
    cached = get_cached_geocode(connection, key)
    if cached:
        return cached
    _respect_rate_limit()
    response = http_get(
        NOMINATIM_URL,
        params={"q": key, "format": "jsonv2", "limit": 1, "countrycodes": "tw"},
        headers={"User-Agent": Config.NOMINATIM_USER_AGENT}, timeout=8,
    )
    response.raise_for_status()
    items = response.json()
    if not items or "臺北市" not in items[0].get("display_name", ""):
        return None
    result = {
        "normalized_address": key, "display_address": items[0]["display_name"],
        "latitude": float(items[0]["lat"]), "longitude": float(items[0]["lon"]),
        "cached_at": datetime.now(timezone.utc),
    }
    save_cached_geocode(connection, result)
    connection.commit()
    return result
```

- [ ] **Step 4: Run tests and commit**

Run: `python -m pytest tests/test_core.py -v`

Expected: all tests pass without a real Nominatim request.

```bash
git add geocoder.py tests/test_core.py
git commit -m "feat: add cached address geocoding"
```

---

### Task 7: Gemini Structured Intent Parsing with Manual Fallback

**Files:**
- Create: `ai_service.py`
- Modify: `tests/test_core.py`

**Interfaces:**
- Consumes: message text and optional session context `{destination, district, lot_id, arrival_time}`.
- Produces: `ParkingIntent(BaseModel)`, `parse_parking_query(message, context=None, client=None) -> ParkingIntent`; raises `IntentServiceError` on missing key, timeout, invalid schema, unsupported district or service error.

- [ ] **Step 1: Add failing schema and mocked Gemini tests**

Append:

```python
import pytest
from ai_service import IntentServiceError, ParkingIntent, parse_parking_query


def test_intent_schema_rejects_non_taipei_district():
    with pytest.raises(ValueError):
        ParkingIntent(intent="recommend", original_destination="板橋車站",
                      address=None, district="板橋區", arrival_time=None,
                      missing_fields=[])


def test_parse_query_uses_structured_output_and_context(monkeypatch):
    output = """{"intent":"compare","original_destination":"臺北市政府",
      "address":"臺北市信義區市府路1號","district":"信義區",
      "arrival_time":"2026-08-08T18:00:00+08:00","missing_fields":[]}"""
    interaction = type("Interaction", (), {"output_text": output})()
    interactions = type("Interactions", (), {"create": lambda self, **kwargs: interaction})()
    fake_client = type("Client", (), {"interactions": interactions})()
    result = parse_parking_query("那週末呢？", {"destination": "臺北市政府"}, fake_client)
    assert result.intent == "compare"
    assert result.district == "信義區"
```

- [ ] **Step 2: Run focused tests and verify failure**

Run: `python -m pytest tests/test_core.py -k "intent_schema or parse_query" -v`

Expected: collection fails because `ai_service.py` does not exist.

- [ ] **Step 3: Implement the restricted Pydantic contract and prompt**

Create `ai_service.py`:

```python
"""Gemini 僅把停車問題轉成固定結構，不產生 SQL、格數或推薦。"""

import json
from datetime import datetime
from typing import Literal
from zoneinfo import ZoneInfo
from google import genai
from pydantic import BaseModel, field_validator
from config import Config

TAIPEI_DISTRICTS = {
    "松山區", "信義區", "大安區", "中山區", "中正區", "大同區",
    "萬華區", "文山區", "南港區", "內湖區", "士林區", "北投區",
}


class IntentServiceError(RuntimeError):
    """表示 Gemini 未設定、逾時、回應無效或服務失敗。"""


class ParkingIntent(BaseModel):
    """限定 Gemini 能交給後端的欄位與三種停車意圖。"""
    intent: Literal["recommend", "history", "compare"]
    original_destination: str | None
    address: str | None
    district: str | None
    arrival_time: datetime | None
    missing_fields: list[str]

    @field_validator("district")
    @classmethod
    def validate_district(cls, value):
        if value is not None and value not in TAIPEI_DISTRICTS:
            raise ValueError("只支援臺北市十二行政區")
        return value


def _prompt(message, context):
    """建立窄範圍指令，明確禁止模型虛構停車資料。"""
    now = datetime.now(ZoneInfo("Asia/Taipei")).isoformat()
    return f"""你是停車查詢欄位解析器，只能判斷 recommend、history、compare。
目前臺北時間：{now}。只接受臺北市地址與十二行政區。
不得提供停車場、空位、距離、分數、SQL 或一般聊天答案。
必要資訊不足時列入 missing_fields，不得猜測。
上一輪狀態：{json.dumps(context or {}, ensure_ascii=False, default=str)}
使用者：{message}"""


def parse_parking_query(message, context=None, client=None):
    """呼叫 Gemini 結構化輸出並驗證為 ParkingIntent。"""
    if client is None and not Config.GEMINI_API_KEY:
        raise IntentServiceError("Gemini 尚未設定")
    try:
        client = client or genai.Client(api_key=Config.GEMINI_API_KEY)
        interaction = client.interactions.create(
            model=Config.GEMINI_MODEL,
            input=_prompt(message, context),
            response_format={"type": "text", "mime_type": "application/json",
                             "schema": ParkingIntent.model_json_schema()},
        )
        return ParkingIntent.model_validate_json(interaction.output_text)
    except Exception as exc:
        raise IntentServiceError("目前無法理解問題，請改用手動查詢") from exc
```

- [ ] **Step 4: Run tests and commit**

Run: `python -m pytest tests/test_core.py -v`

Expected: all tests pass; Gemini is only reached through the fake client.

```bash
git add ai_service.py tests/test_core.py
git commit -m "feat: parse constrained parking intents with gemini"
```

---

### Task 8: Flask Query Orchestration, Session Follow-up, and History API

**Files:**
- Modify: `app.py`
- Modify: `database.py`
- Modify: `analysis.py`
- Modify: `tests/test_core.py`

**Interfaces:**
- Consumes: manual JSON `{mode, address, district, arrival_time}` or chat JSON `{mode: "chat", message}`; functions from Tasks 2–7.
- Produces: `POST /api/query`, `GET /api/parking/<lot_id>/history`, deterministic response object with `destination`, `current`, `history`, `recommendations`, `nearest`, `warning`, `avoid`, and `updated_at`.

- [ ] **Step 1: Add a database helper for attaching per-lot history**

Add to `database.py`:

```python
def fetch_matching_history(connection, lot_ids, start_utc, end_utc):
    """一次取得候選場站歷史，避免每個場站各查一次造成 N+1。"""
    if not lot_ids:
        return []
    placeholders = ",".join(["%s"] * len(lot_ids))
    sql = f"""
        SELECT s.lot_id, s.available_spaces, s.captured_at, l.total_spaces
        FROM parking_snapshots s
        JOIN parking_lots l ON l.lot_id = s.lot_id
        WHERE s.lot_id IN ({placeholders})
          AND s.captured_at BETWEEN %s AND %s
        ORDER BY s.lot_id, s.captured_at
    """
    params = tuple(lot_ids) + (start_utc, end_utc)
    with connection.cursor() as cursor:
        cursor.execute(sql, params)
        return list(cursor.fetchall())
```

- [ ] **Step 2: Add a focused response builder to analysis.py**

Add:

```python
def split_recommendation_groups(ranked):
    """產生前三名、最近、小心與避雷群組，讓前端只負責呈現。"""
    with_distance = [row for row in ranked if row.get("distance_m") is not None]
    nearest = sorted(with_distance, key=lambda item: item["distance_m"])[:3]
    warning = [row for row in ranked if 85 <= row["hell_score"] < 95]
    avoid = [row for row in ranked if row["available_spaces"] <= 3 or row["hell_score"] >= 95]
    return {
        "recommendations": ranked[:3], "nearest": nearest,
        "warning": warning[:3], "avoid": avoid[:3],
    }
```

- [ ] **Step 3: Add failing manual-query and fallback route tests**

Append tests that monkeypatch the imported app functions, not the external network:

```python
import app as app_module


def test_manual_query_returns_deterministic_groups(monkeypatch):
    fake_connection = type("Connection", (), {"close": lambda self: None})()
    monkeypatch.setattr(app_module, "get_connection", lambda: fake_connection)
    monkeypatch.setattr(app_module, "geocode_address", lambda address, conn: {
        "display_address": "臺北市政府", "latitude": 25.0375, "longitude": 121.5637})
    monkeypatch.setattr(app_module, "fetch_current_lots", lambda conn, district=None, freshness_minutes=45: [{
        "lot_id": "TPE1", "lot_name": "A場", "district": "信義區", "address": "市府路",
        "operator_type": "民營停車場", "total_spaces": 100, "available_spaces": 20,
        "latitude": 25.0376, "longitude": 121.5638,
        "captured_at": datetime(2026, 8, 3, 10, tzinfo=timezone.utc)}])
    monkeypatch.setattr(app_module, "attach_history", lambda conn, rows, arrival: rows)
    client = create_app({"TESTING": True, "SECRET_KEY": "test"}).test_client()
    response = client.post("/api/query", json={
        "mode": "manual", "address": "市府路1號", "district": "信義區",
        "arrival_time": "2026-08-03T18:00:00+08:00"})
    body = response.get_json()
    assert response.status_code == 200
    assert body["recommendations"][0]["lot_id"] == "TPE1"
    assert body["destination"]["display_address"] == "臺北市政府"


def test_chat_failure_returns_manual_fallback(monkeypatch):
    monkeypatch.setattr(app_module, "parse_parking_query",
                        lambda *_a, **_k: (_ for _ in ()).throw(IntentServiceError("失敗")))
    client = create_app({"TESTING": True, "SECRET_KEY": "test"}).test_client()
    response = client.post("/api/query", json={"mode": "chat", "message": "我要去市政府"})
    assert response.status_code == 503
    assert response.get_json()["fallback"] == "manual"
```

- [ ] **Step 4: Run route tests and verify failure**

Run: `python -m pytest tests/test_core.py -k "manual_query or chat_failure" -v`

Expected: import or route failure because orchestration imports and `/api/query` are absent.

- [ ] **Step 5: Implement history attachment and request validation**

In `app.py`, import the required functions and add helpers. Keep each helper under 30 lines:

```python
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from flask import render_template, request, session
from ai_service import IntentServiceError, TAIPEI_DISTRICTS, parse_parking_query
from analysis import (district_hell_score, rank_candidates, rank_district_candidates,
                      split_recommendation_groups, summarize_matching_history,
                      summarize_hour_comparison, build_history_series)
from database import (fetch_current_lots, fetch_history,
                      fetch_matching_history, get_connection)
from geocoder import geocode_address


def parse_manual_payload(payload):
    """驗證手動表單並回傳與 Gemini 相同概念的普通字典。"""
    district = (payload.get("district") or "").strip()
    address = (payload.get("address") or "").strip()
    if not district and not address:
        raise ValueError("請輸入地址或選擇行政區")
    if district and district not in TAIPEI_DISTRICTS:
        raise ValueError("只支援臺北市十二行政區")
    arrival = datetime.fromisoformat(payload["arrival_time"])
    if arrival.tzinfo is None:
        raise ValueError("抵達時間必須包含時區")
    return {"intent": "recommend", "address": address or None,
            "district": district or None, "arrival_time": arrival}


def validate_parsed_query(parsed):
    """統一驗證 Gemini 與手動結果，避免缺少條件時進入資料庫分析。"""
    if parsed.get("missing_fields"):
        names = "、".join(parsed["missing_fields"])
        raise ValueError(f"還需要：{names}")
    if not parsed.get("address") and not parsed.get("district"):
        raise ValueError("請提供臺北市地址或行政區")
    if parsed.get("arrival_time") is None:
        raise ValueError("請提供預計抵達時間")
    if isinstance(parsed["arrival_time"], str):
        parsed["arrival_time"] = datetime.fromisoformat(parsed["arrival_time"])
    return parsed


def attach_history(connection, rows, arrival_time):
    """一次查詢 30 天歷史，再把相同日別與小時摘要放回各候選場站。"""
    end_utc = datetime.now(timezone.utc)
    start_utc = end_utc - timedelta(days=Config.HISTORY_LOOKBACK_DAYS)
    history_rows = fetch_matching_history(
        connection, [row["lot_id"] for row in rows], start_utc, end_utc)
    grouped = defaultdict(list)
    for row in history_rows:
        grouped[row["lot_id"]].append(row)
    for row in rows:
        summary = summarize_matching_history(grouped[row["lot_id"]], arrival_time)
        row["historical_hell_score"] = summary["hell_score"]
        row["history_sample_count"] = summary["sample_count"]
        row["history_comparison"] = summarize_hour_comparison(
            grouped[row["lot_id"]], arrival_time.astimezone(ZoneInfo("Asia/Taipei")).hour)
    return rows


def public_candidate(row):
    """只輸出頁面需要的安全欄位，並把 Decimal 與 datetime 轉成 JSON 型別。"""
    keys = ("lot_id", "lot_name", "district", "address", "operator_type",
            "total_spaces", "available_spaces", "fee_info", "service_time",
            "hell_label", "history_sample_count")
    result = {key: row.get(key) for key in keys}
    for key in ("latitude", "longitude", "distance_m", "hell_score",
                "historical_hell_score", "recommendation_score"):
        result[key] = float(row[key]) if row.get(key) is not None else None
    return result
```

- [ ] **Step 6: Implement deterministic `/api/query` and session state**

Inside `create_app`, add:

```python
    @app.get("/")
    def index():
        """顯示唯一主頁，資料由前端呼叫 JSON API 載入。"""
        return render_template("index.html")

    @app.post("/api/query")
    def query_parking():
        """解析手動或聊天輸入，交由固定函式產生可驗證的停車結果。"""
        payload = request.get_json(silent=True) or {}
        try:
            if payload.get("mode") == "chat":
                parsed = parse_parking_query(payload.get("message", ""), dict(session)).model_dump()
            else:
                parsed = parse_manual_payload(payload)
            parsed = validate_parsed_query(parsed)
        except IntentServiceError as exc:
            return jsonify(error=str(exc), fallback="manual"), 503
        except (KeyError, TypeError, ValueError) as exc:
            return jsonify(error=str(exc)), 400

        connection = get_connection()
        try:
            destination = geocode_address(parsed.get("address"), connection) if parsed.get("address") else None
            if parsed.get("address") and destination is None:
                return jsonify(error="找不到地址，請修正或改選行政區", fallback="district"), 422
            rows = fetch_current_lots(connection, parsed.get("district"), Config.FRESHNESS_MINUTES)
            if destination:
                # 先用即時資料與距離縮到 1.5 公里，再查這批候選的歷史，避免讀取全市歷史。
                nearby = rank_candidates(rows, destination["latitude"], destination["longitude"])
                nearby = attach_history(connection, nearby, parsed["arrival_time"])
                ranked = rank_candidates(nearby, destination["latitude"], destination["longitude"])
                score_rows = ranked
            else:
                rows = attach_history(connection, rows, parsed["arrival_time"])
                ranked = rank_district_candidates(rows)
                score_rows = rows
            raw_groups = split_recommendation_groups(ranked)
            groups = {name: [public_candidate(row) for row in group]
                      for name, group in raw_groups.items()}
            destination_json = None if destination is None else {
                "display_address": destination["display_address"],
                "latitude": float(destination["latitude"]),
                "longitude": float(destination["longitude"]),
            }
            first = ranked[0] if ranked else None
            session.update(destination=parsed.get("address"), district=parsed.get("district"),
                           arrival_time=parsed["arrival_time"].isoformat(),
                           lot_id=ranked[0]["lot_id"] if ranked else None)
            updated_at = max((row["captured_at"] for row in rows), default=None)
            return jsonify(destination=destination_json, current={
                "district_score": district_hell_score(score_rows),
                "valid_lot_count": len(score_rows)},
                history={"hell_score": first.get("historical_hell_score") if first else None,
                         "sample_count": first.get("history_sample_count", 0) if first else 0,
                         "comparison": first.get("history_comparison") if first else None},
                intent=parsed["intent"],
                updated_at=updated_at.isoformat() if updated_at else None, **groups)
        except Exception:
            app.logger.exception("停車查詢失敗")
            return jsonify(error="服務暫時無法使用，請稍後再試"), 503
        finally:
            connection.close()
```

Add the district-only ranking used by the route to `analysis.py`:

```python
def rank_district_candidates(rows):
    """手動行政區模式不計距離，只依即時 80% 與歷史 20% 排序。"""
    ranked = []
    for row in rows:
        current = hell_score(row["total_spaces"], row["available_spaces"])
        if current is None:
            continue
        historical = row.get("historical_hell_score")
        score = (100 - current) if historical is None else (100 - current) * 0.8 + (100 - historical) * 0.2
        item = dict(row, distance_m=None, hell_score=current,
                    hell_label=hell_label(current), recommendation_score=round(score, 2))
        ranked.append(item)
    return sorted(ranked, key=lambda item: item["recommendation_score"], reverse=True)
```

The route above uses `rank_district_candidates(rows)` when `destination` is `None`. This preserves honest semantics: district-only results never display a fabricated distance.

- [ ] **Step 7: Add the seven-day history route**

Inside `create_app`:

```python
    @app.get("/api/parking/<lot_id>/history")
    def parking_history(lot_id):
        """回傳單一場站最近七天的有效空位序列供唯一折線圖使用。"""
        end_utc = datetime.now(timezone.utc)
        start_utc = end_utc - timedelta(days=7)
        connection = get_connection()
        try:
            rows = fetch_history(connection, lot_id, start_utc, end_utc)
            return jsonify(lot_id=lot_id, points=build_history_series(rows))
        except Exception:
            app.logger.exception("歷史查詢失敗")
            return jsonify(error="暫時無法取得歷史資料"), 503
        finally:
            connection.close()
```

- [ ] **Step 8: Run tests, verify response JSON serialization, and commit**

Run:

```bash
python -m pytest tests/test_core.py -v
python -m compileall app.py analysis.py database.py
```

Expected: all tests pass. The test response contains numeric latitude, longitude and scores, while `updated_at` is an ISO string; no `Decimal` or raw `datetime` reaches `jsonify`.

```bash
git add app.py analysis.py database.py tests/test_core.py
git commit -m "feat: expose parking recommendation and history APIs"
```

---

### Task 9: Single-Page Interface, Leaflet Map, and One Chart

**Files:**
- Create: `templates/index.html`
- Create: `static/style.css`
- Create: `static/app.js`

**Interfaces:**
- Consumes: `POST /api/query` and `GET /api/parking/<lot_id>/history` response fields defined in Task 8.
- Produces: chat form, manual fallback form, score cards, recommended/warning/avoid lists, Leaflet markers, nearest list, one Chart.js line chart, visible source attribution.

- [ ] **Step 1: Create the semantic one-page HTML shell**

Create `templates/index.html` with exactly one main page and CDN assets:

```html
<!doctype html>
<html lang="zh-Hant">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>停車地獄雷達</title>
  <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
  <link rel="stylesheet" href="{{ url_for('static', filename='style.css') }}">
</head>
<body>
  <header class="hero">
    <p class="eyebrow">TAIPEI PARKING DECISION TOOL</p>
    <h1>停車地獄雷達</h1>
    <p>現在有多難停？哪裡值得去？用即時資料與歷史參考做決定。</p>
    <p id="updated-at" aria-live="polite">尚未查詢</p>
  </header>
  <main>
    <section class="query-panel">
      <form id="chat-form">
        <label for="message">直接告訴我目的地</label>
        <div class="input-row"><input id="message" required placeholder="例如：今晚六點去臺北市政府，哪裡比較好停？"><button>分析</button></div>
      </form>
      <details id="manual-panel">
        <summary>Gemini 無法使用？改用手動查詢</summary>
        <form id="manual-form" class="manual-grid">
          <label>地址<input id="address" placeholder="信義區市府路1號"></label>
          <label>行政區<select id="district"><option value="">由地址判斷</option></select></label>
          <label>抵達時間<input id="arrival-time" type="datetime-local" required></label>
          <button>手動分析</button>
        </form>
      </details>
      <p id="status" role="status"></p>
    </section>
    <section class="score-grid" aria-label="分析摘要">
      <article><span>目的地</span><strong id="destination">—</strong></article>
      <article><span>目前地獄指數</span><strong id="district-score">—</strong></article>
      <article><span>首選場站抵達時段歷史</span><strong id="history-score">—</strong><small id="history-compare"></small></article>
      <article><span>有效停車場</span><strong id="valid-count">—</strong></article>
    </section>
    <section><h2>推薦與避雷</h2><div id="recommendations" class="card-grid"></div></section>
    <section class="map-layout"><div><h2>附近停車場</h2><div id="map"></div></div><aside><h2>距離最近</h2><ol id="nearest"></ol></aside></section>
    <section><h2>最近七天空位變化</h2><p id="history-note">選擇推薦停車場後載入歷史參考。</p><canvas id="history-chart"></canvas></section>
  </main>
  <footer>資料來源：臺北市資料大平臺｜地圖 © OpenStreetMap contributors｜歷史資料僅供參考，非空位預測。</footer>
  <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.7/dist/chart.umd.min.js"></script>
  <script src="{{ url_for('static', filename='app.js') }}"></script>
</body>
</html>
```

- [ ] **Step 2: Add the locked dark/orange responsive visual system**

Create `static/style.css`. It must define these concrete design tokens and layouts:

```css
/* 深色底搭配橘紅警示，讓分數層級在展示時一眼可辨識。 */
:root { --bg:#0e1116; --panel:#171c24; --line:#2a3340; --text:#f7f8fa; --muted:#9aa6b2; --orange:#ff8a3d; --red:#ff4d4f; --yellow:#f6c344; --green:#4ecb8d; }
* { box-sizing:border-box; }
body { margin:0; background:radial-gradient(circle at top right,#2a1715 0,#0e1116 35%); color:var(--text); font-family:"Noto Sans TC",system-ui,sans-serif; line-height:1.6; }
.hero, main, footer { width:min(1120px,calc(100% - 32px)); margin:auto; }
.hero { padding:56px 0 28px; }
.hero h1 { margin:4px 0; font-size:clamp(2.2rem,7vw,5rem); line-height:1; }
.eyebrow { color:var(--orange); letter-spacing:.15em; font-weight:700; }
main { display:grid; gap:24px; padding-bottom:48px; }
section, .score-grid article { background:rgba(23,28,36,.94); border:1px solid var(--line); border-radius:18px; padding:20px; }
.input-row, .manual-grid { display:grid; grid-template-columns:1fr auto; gap:12px; }
input, select, button { min-height:46px; border-radius:10px; border:1px solid var(--line); padding:0 14px; font:inherit; }
input, select { background:#0f141b; color:var(--text); }
button { border:0; background:var(--orange); color:#1b1008; font-weight:800; cursor:pointer; }
.score-grid { display:grid; grid-template-columns:repeat(4,1fr); gap:16px; background:none; border:0; padding:0; }
.card-grid { display:grid; grid-template-columns:repeat(3,1fr); gap:16px; background:none; border:0; padding:0; }
.score-grid strong { display:block; margin-top:8px; font-size:1.7rem; }
.parking-card { border-left:5px solid var(--green); }
.parking-card.warning { border-left-color:var(--yellow); }
.parking-card.avoid { border-left-color:var(--red); }
.map-layout { display:grid; grid-template-columns:2fr 1fr; gap:20px; }
#map { height:430px; border-radius:14px; }
#status.error { color:#ff9294; }
#status.success { color:#7de2ad; }
footer { color:var(--muted); padding:8px 0 40px; font-size:.9rem; }
@media (max-width:760px) { .score-grid,.card-grid,.map-layout,.manual-grid { grid-template-columns:1fr; } .input-row { grid-template-columns:1fr; } #map { height:340px; } }
```

- [ ] **Step 3: Implement forms and deterministic result cards**

Create `static/app.js` with a file comment, twelve districts, current local time default, and these functions: `submitQuery(payload)`, `renderSummary(data)`, `renderCards(data)`, `renderMap(data)`, `loadHistory(lotId)`, `showStatus(message, type)`. The essential implementation is:

```javascript
/* 單頁互動：呼叫固定 API、更新卡片、Leaflet 與唯一 Chart.js 圖表。 */
const districts = ["松山區","信義區","大安區","中山區","中正區","大同區","萬華區","文山區","南港區","內湖區","士林區","北投區"];
let map = L.map("map").setView([25.0478, 121.5319], 12);
let markerLayer = L.layerGroup().addTo(map);
let historyChart = null;
L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", { attribution:"© OpenStreetMap contributors" }).addTo(map);

const districtSelect = document.querySelector("#district");
districts.forEach(name => districtSelect.add(new Option(name, name)));
const localNow = new Date(Date.now() - new Date().getTimezoneOffset() * 60000).toISOString().slice(0,16);
document.querySelector("#arrival-time").value = localNow;

async function submitQuery(payload) {
  showStatus("正在分析停車難度…", "");
  const response = await fetch("/api/query", { method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(payload) });
  const data = await response.json();
  if (!response.ok) {
    if (data.fallback === "manual") document.querySelector("#manual-panel").open = true;
    throw new Error(data.error || "查詢失敗");
  }
  renderSummary(data); renderCards(data); renderMap(data);
  if (data.recommendations.length) await loadHistory(data.recommendations[0].lot_id);
  showStatus("分析完成；數字來自官方資料與固定公式。", "success");
}

document.querySelector("#chat-form").addEventListener("submit", async event => {
  event.preventDefault();
  try { await submitQuery({ mode:"chat", message:document.querySelector("#message").value }); }
  catch (error) { showStatus(error.message, "error"); }
});

document.querySelector("#manual-form").addEventListener("submit", async event => {
  event.preventDefault();
  const arrival = new Date(document.querySelector("#arrival-time").value).toISOString();
  try { await submitQuery({ mode:"manual", address:document.querySelector("#address").value,
    district:districtSelect.value, arrival_time:arrival }); }
  catch (error) { showStatus(error.message, "error"); }
});
```

Continue the same file with concrete renderers:

```javascript
function showStatus(message, type) { const node=document.querySelector("#status"); node.textContent=message; node.className=type; }
function formatDistance(value) { return value == null ? "行政區模式" : value < 1000 ? `${Math.round(value)} m` : `${(value/1000).toFixed(1)} km`; }
function renderSummary(data) {
  document.querySelector("#destination").textContent = data.destination?.display_address || "行政區查詢";
  document.querySelector("#district-score").textContent = data.current.district_score == null ? "資料不足" : `${data.current.district_score} 分`;
  document.querySelector("#history-score").textContent = data.history.hell_score == null ? "樣本不足" : `${data.history.hell_score} 分`;
  const compare=data.history.comparison; const weekday=compare?.weekday?.hell_score; const weekend=compare?.weekend?.hell_score;
  document.querySelector("#history-compare").textContent = weekday == null || weekend == null ? `有效樣本 ${data.history.sample_count} 筆` : `平日 ${weekday}｜週末 ${weekend}`;
  document.querySelector("#valid-count").textContent = `${data.current.valid_lot_count} 座`;
  document.querySelector("#updated-at").textContent = data.updated_at ? `資料時間 ${new Date(data.updated_at).toLocaleString("zh-TW")}` : "目前無有效即時資料";
}
function parkingCard(lot, kind="") {
  return `<article class="parking-card ${kind}"><p>${lot.hell_label}</p><h3>${lot.lot_name}</h3><strong>剩餘 ${lot.available_spaces} 格</strong><p>地獄指數 ${lot.hell_score}｜${formatDistance(lot.distance_m)}</p><p>推薦分數 ${lot.recommendation_score}</p></article>`;
}
function renderCards(data) {
  const cards = [...data.recommendations.map(x=>parkingCard(x)), ...data.warning.map(x=>parkingCard(x,"warning")), ...data.avoid.map(x=>parkingCard(x,"avoid"))];
  document.querySelector("#recommendations").innerHTML = cards.join("") || "<p>目前沒有符合條件的停車場。</p>";
  document.querySelector("#nearest").innerHTML = data.nearest.map(x=>`<li><button type="button" data-lot="${x.lot_id}">${x.lot_name}<br>${formatDistance(x.distance_m)}</button></li>`).join("");
  document.querySelectorAll("[data-lot]").forEach(button=>button.addEventListener("click",()=>loadHistory(button.dataset.lot)));
}
function renderMap(data) {
  markerLayer.clearLayers();
  const points = [];
  if (data.destination) { L.marker([data.destination.latitude,data.destination.longitude]).bindPopup("目的地").addTo(markerLayer); points.push([data.destination.latitude,data.destination.longitude]); }
  data.recommendations.concat(data.warning,data.avoid).forEach(lot=>{
    if (lot.latitude == null) return;
    L.circleMarker([lot.latitude,lot.longitude],{radius:8,color:lot.hell_score>=95?"#ff4d4f":lot.hell_score>=80?"#f6c344":"#4ecb8d"})
      .bindPopup(`${lot.lot_name}<br>剩餘 ${lot.available_spaces} 格<br>${formatDistance(lot.distance_m)}`).addTo(markerLayer);
    points.push([lot.latitude,lot.longitude]);
  });
  if (points.length) map.fitBounds(points,{padding:[28,28]});
}
async function loadHistory(lotId) {
  const response = await fetch(`/api/parking/${encodeURIComponent(lotId)}/history`); const data = await response.json();
  if (!response.ok) return showStatus(data.error,"error");
  const labels=data.points.map(x=>new Date(x.captured_at).toLocaleString("zh-TW")); const values=data.points.map(x=>x.available_spaces);
  if (historyChart) historyChart.destroy();
  historyChart=new Chart(document.querySelector("#history-chart"),{type:"line",data:{labels,datasets:[{label:"剩餘汽車位",data:values,borderColor:"#ff8a3d",backgroundColor:"#ff8a3d33",fill:true,tension:.25}]},options:{responsive:true,scales:{y:{beginAtZero:true}}}});
  document.querySelector("#history-note").textContent = data.points.length ? `共 ${data.points.length} 筆有效歷史資料` : "歷史樣本尚不足";
}
```

- [ ] **Step 4: Verify the page without adding a front-end build system**

Run:

```bash
node --check static/app.js
python -m pytest tests/test_core.py -v
flask --app app run --debug
```

Expected: JavaScript syntax check passes; `/` renders; mobile width becomes one column; only one map and one chart appear. In the browser network panel, typing alone must not call Nominatim—only form submission may call the backend.

- [ ] **Step 5: Commit the complete single page**

```bash
git add templates/index.html static/style.css static/app.js
git commit -m "feat: add parking radar single-page interface"
```

---

### Task 10: Linux Deployment, Documentation, and Final Scope Gate

**Files:**
- Create: `deploy/parking-radar.service`
- Create: `deploy/nginx-parking-radar.conf`
- Create: `README.md`
- Modify: `.gitignore`

**Interfaces:**
- Consumes: `app:app`, `collector.py --once`, `.env`, MySQL schema.
- Produces: reproducible local setup, GCP service configuration, cron command, manual demonstration checklist, final line-count and test gates.

- [ ] **Step 1: Add Gunicorn systemd service**

Create `deploy/parking-radar.service`:

```ini
[Unit]
Description=Parking Hell Radar Flask application
After=network.target mysql.service

[Service]
User=parking
Group=www-data
WorkingDirectory=/opt/parking-hell
EnvironmentFile=/opt/parking-hell/.env
ExecStart=/opt/parking-hell/.venv/bin/gunicorn --workers 1 --bind 127.0.0.1:8000 --timeout 60 app:app
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 2: Add Nginx reverse proxy with no public MySQL port**

Create `deploy/nginx-parking-radar.conf`:

```nginx
server {
    listen 80;
    server_name _;
    client_max_body_size 1m;

    location /static/ {
        alias /opt/parking-hell/static/;
        expires 1h;
    }

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

- [ ] **Step 3: Write README with exact local workflow**

Create `README.md` with this complete operational outline:

````markdown
# 停車地獄雷達

整合臺北市官方停車資料，以固定公式分析即時難度、歷史參考、距離與推薦；Gemini 只解析限定意圖。

## 功能範圍

- 臺北市路外停車場、地址 1.5 公里搜尋、三名推薦、避雷、平日／週末歷史參考。
- 單頁 Leaflet 地圖與一張最近七天折線圖。
- 不含導航、AI 空位預測、路邊格位、會員及個別民營業者爬蟲。

## Windows 本機啟動

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
mysql -u root -p -e "CREATE DATABASE parking_hell CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"
Get-Content -Raw schema.sql | mysql -u root -p parking_hell
python collector.py --once
python -m pytest tests/test_core.py -v
flask --app app run --debug
```

## 環境變數

| 名稱 | 用途 |
|---|---|
| `FLASK_SECRET_KEY` | Flask session 簽章；部署時必須換成長隨機字串 |
| `MYSQL_HOST` / `MYSQL_PORT` | MySQL 位址，部署時固定 localhost:3306 |
| `MYSQL_USER` / `MYSQL_PASSWORD` / `MYSQL_DATABASE` | 專題資料庫帳號與名稱 |
| `GEMINI_API_KEY` | 留空時停用對話並使用手動表單 |
| `GEMINI_MODEL` | 預設 `gemini-3.5-flash` |
| `NOMINATIM_USER_AGENT` | 必須包含可辨識的專題名稱與聯絡資訊 |

## 計算與資料清洗

- 停車場地獄指數：`(總車位 - 剩餘車位) / 總車位 × 100`。
- 行政區地獄指數：`全區已使用有效車位 / 全區有效總車位 × 100`。
- 有歷史樣本：即時容易度 50% + 距離容易度 30% + 歷史容易度 20%。
- 歷史不足：即時容易度 60% + 距離容易度 40%。
- `-9`、`-11`、`-12`、`-13` 是官方特殊狀態，不是負車位，不進入數值計算。
- 歷史分析為過去樣本參考，不代表抵達時仍有相同空位。

## GCP 1 vCPU／1 GB 部署

1. 建立 Ubuntu VM，執行 `sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile && sudo mkswap /swapfile && sudo swapon /swapfile`，並在 `/etc/fstab` 加入 `/swapfile none swap sw 0 0`。
2. 安裝 Python、MySQL、Nginx，建立 `parking` 系統使用者，專案放在 `/opt/parking-hell` 並由該使用者擁有。
3. 在 MySQL 設定加入：`bind-address=127.0.0.1`、`innodb_buffer_pool_size=128M`、`max_connections=30`、`performance_schema=OFF`，重新啟動後確認 3306 未對外開放。
4. 建立 `.venv` 與不進 Git 的 `.env`，執行 schema 與第一次 collector。
5. 安裝 `deploy/parking-radar.service` 與 `deploy/nginx-parking-radar.conf`。
6. 以 `systemctl enable --now parking-radar nginx` 啟動。
7. 以 `parking` 使用者執行 `crontab -e` 加入：

```cron
*/30 * * * * cd /opt/parking-hell && /opt/parking-hell/.venv/bin/python collector.py --once >> /opt/parking-hell/collector.log 2>&1
```

## 展示檢查

1. 手動選行政區可完成查詢。
2. 輸入臺北市地址可顯示 1.5 公里候選、最近與前三名推薦。
3. Gemini 可處理推薦、歷史、平週末比較及一次簡單追問。
4. 關閉 Gemini 金鑰後，頁面會引導手動查詢。
5. Nominatim 查不到地址時，可退回行政區查詢。
6. 地圖、唯一折線圖、資料時間與 OpenStreetMap 標示皆可見。
````

- [ ] **Step 4: Add final ignore rules and run the full automatic gate**

Ensure `.gitignore` includes:

```gitignore
.env
.venv/
__pycache__/
.pytest_cache/
*.pyc
.superpowers/
```

Run:

```bash
python -m pytest tests/test_core.py -v
python -m compileall app.py config.py collector.py database.py analysis.py ai_service.py geocoder.py
node --check static/app.js
git status --short
```

Expected: all tests pass, all Python files compile, JavaScript syntax passes, and only intended deployment/documentation changes remain uncommitted.

- [ ] **Step 5: Enforce the code-size gate**

Run in PowerShell:

```powershell
$files = @('app.py','config.py','collector.py','database.py','analysis.py','ai_service.py','geocoder.py','schema.sql','templates/index.html','static/style.css','static/app.js','tests/test_core.py')
$counts = foreach ($file in $files) { [pscustomobject]@{ File=$file; Lines=(Get-Content $file).Count } }
$counts | Format-Table -AutoSize
($counts | Measure-Object Lines -Sum).Sum
```

Expected: no listed file exceeds 250 lines; total is 1,500–2,000 where practical and never exceeds 2,500. If over budget, remove in this order: extra map interactions, nonessential generated prose, visual animation. Do not remove address search, manual fallback, historical analysis, core recommendation, tests, or Traditional Chinese explanatory comments.

- [ ] **Step 6: Perform the manual failure and demonstration checklist**

With local MySQL and Flask running, verify exactly these cases:

1. `python collector.py --once` stores lots and valid snapshots; running it again with the same official update time does not duplicate snapshots.
2. Manual district query works with `GEMINI_API_KEY` empty.
3. Exact Taipei address shows destination plus candidates within 1.5 km; nearest and best are separately labeled.
4. A lot with 0–3 spaces or score at least 95 appears in avoid.
5. A history group with fewer than 3 samples shows “歷史樣本尚不足”.
6. “那週末呢？” reuses the prior session destination and returns `compare` or history-oriented output.
7. Simulated Gemini failure opens manual fallback; simulated geocoder miss suggests district fallback.
8. Page shows official data time, Taipei source, OpenStreetMap attribution, and historical-reference disclaimer.

- [ ] **Step 7: Commit deployment and documentation**

```bash
git add deploy README.md .gitignore
git commit -m "docs: add local and gcp runbook"
```

---

## Final Acceptance Gate

Implementation is complete only when all statements below are true:

- `python -m pytest tests/test_core.py -v` passes without network access.
- `python collector.py --once` succeeds against the two locked official URLs and uses exact `id` JOIN.
- Negative and impossible availability values never enter scoring or snapshots.
- Manual district querying works even when Gemini and Nominatim are unavailable.
- Exact-address querying uses cache-first Nominatim and only returns candidates within 1,500 metres.
- District score is space-weighted; nearest and best recommendation remain distinct.
- Historical score requires at least 3 matching weekday/weekend and hour samples.
- Gemini returns only the three Pydantic intents and never supplies parking facts or SQL.
- UI remains a single responsive page with one map and one chart.
- MySQL is localhost-only in deployment, Gunicorn uses one worker, and cron runs every 30 minutes.
- The locked files stay below 2,500 total lines and retain useful Traditional Chinese comments.
