# New Taipei Off-Street Parking Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不退化既有臺北查詢的前提下，交付可由功能旗標安全開啟的臺北市／新北市路外停車場查詢、推薦、地圖、費率與七天歷史。

**Architecture:** 保留 `collector.py` 為排程入口，臺北解析留在原模組，新北下載與正規化集中於 `new_taipei_source.py`；兩個來源各自使用獨立交易。`city_config.py` 成為後端唯一城市／行政區設定來源，前端由 Flask 注入同一份公開設定；資料進入資料庫後共用既有分析、步行、費率與圖卡流程。

**Tech Stack:** Python 3.13、Flask、PyMySQL、Requests、Pydantic、PyProj、Vanilla JavaScript、Pytest、MySQL 8

**Spec:** `docs/superpowers/specs/2026-08-26-new-taipei-off-street-parking-design.md`

## Global Constraints

- 既有「台北車站」查詢結果與排序行為不得退化。
- 新北與臺北收集必須使用獨立連線及交易，一個來源失敗不得 rollback 另一來源。
- 新北 TWD97 使用 EPSG:3826 轉為 WGS84 EPSG:4326；無效座標不得參與推薦。
- 地址查詢搜尋目的地 1.5 公里內雙市場站；行政區查詢只搜尋所選城市及行政區。
- 推薦規則不分城市：先風險分組，再依步行時間排序，安全場站不足才補備選。
- 新北動態資料每 15 分鐘收集；因沒有可靠官方時間，前端只能稱為「系統取得時間」。
- 七天圖表保留；每天刪除超過 8 天的 `parking_snapshots`。
- `NEW_TAIPEI_ENABLED=0` 時不收集、不顯示、不查詢新北，但保留資料。
- 所有 CI 測試離線執行，不呼叫真實外部 API。
- 不加入路邊車格、AI 預測、登入、付款、全臺資料或個別業者爬蟲。

## File Structure

- Create `city_config.py`: 城市代碼、別名、行政區、地理邊界與公開前端設定。
- Create `new_taipei_source.py`: 新北分頁下載、TWD97 轉換、靜態／動態正規化。
- Create `parking_cleanup.py`: 8 天快照清理的獨立交易與 CLI。
- Create `deploy/parking-snapshot-cleanup.service` and `deploy/parking-snapshot-cleanup.timer`: 每日清理排程。
- Create `migrations/20260826_add_parking_sources.sql`: additive、可重複執行的城市／來源 migration。
- Create `tests/fixtures/new_taipei_static.json` and `tests/fixtures/new_taipei_dynamic.json`: 離線官方格式 fixture。
- Create `tests/test_city_config.py`, `tests/test_new_taipei_source.py`, `tests/test_parking_cleanup.py`: 新責任的聚焦測試。
- Modify `config.py`, `.env.example`, `requirements.txt`: 功能旗標與 PyProj dependency。
- Modify `schema.sql`, `database.py`: 城市欄位、來源唯一鍵、城市查詢與每來源新鮮度。
- Modify `collector.py`: 臺北 adapter 包裝、雙來源獨立交易與結果摘要。
- Modify `ai_service.py`, `geocoder.py`, `app.py`: 雙北 intent、地址驗證、跨市查詢及來源時間。
- Modify `templates/index.html`, `static/app.js`, `static/style.css`, `static/sw.js`: 城市選擇與來源呈現。
- Modify existing tests beside each production file; do not create one catch-all test module.

---

### Task 1: Lock Taipei behavior and introduce the city registry

**Files:**
- Create: `city_config.py`
- Create: `tests/test_city_config.py`
- Modify: `config.py`
- Modify: `.env.example`
- Modify: `tests/test_app_routes.py`
- Modify: `tests/test_app_errors.py`

**Interfaces:**
- Produces: `CityDefinition(code, name, aliases, districts, bounds)`.
- Produces: `normalize_city(value: str | None) -> str | None` returning `taipei` or `new_taipei`.
- Produces: `city_name(code: str) -> str` returning the official database label.
- Produces: `validate_city_district(city: str, district: str | None) -> None`.
- Produces: `public_city_options(new_taipei_enabled: bool) -> list[dict]`.

- [ ] **Step 1: Add characterization tests for the existing Taipei contract**

```python
def test_taipei_station_characterization_keeps_top_order(monkeypatch):
    near = lot_row()
    near.update(lot_id="NEAR", latitude=25.0477, longitude=121.5169)
    far = lot_row()
    far.update(lot_id="FAR", latitude=25.0520, longitude=121.5230)
    rows = [near, far]
    monkeypatch.setattr(app_module, "geocode_address", lambda *_args: {
        "display_address": "臺北車站, 臺北市", "latitude": 25.0478,
        "longitude": 121.5170, "city": "taipei", "district": "中正區"})
    monkeypatch.setattr(app_module, "fetch_current_lots", lambda *_args, **_kwargs: rows)
    response = make_client().post("/api/query", json={
        "mode": "manual", "address": "台北車站",
        "arrival_time": "2026-08-26T18:00:00+08:00"})
    body = response.get_json()
    assert response.status_code == 200
    assert [row["lot_id"] for row in body["recommendations"]][:2] == ["NEAR", "FAR"]
```

- [ ] **Step 2: Run characterization tests before changing behavior**

Run: `python -m pytest tests/test_app_routes.py tests/test_app_errors.py -q`

Expected: PASS.

- [ ] **Step 3: Add failing city registry tests**

```python
def test_city_registry_normalizes_aliases_and_rejects_cross_city_district():
    assert normalize_city("台北市") == "taipei"
    assert normalize_city("新北") == "new_taipei"
    with pytest.raises(ValueError, match="不屬於"):
        validate_city_district("new_taipei", "信義區")

def test_feature_flag_hides_new_taipei_from_public_options():
    assert [row["code"] for row in public_city_options(False)] == ["taipei"]
    assert [row["code"] for row in public_city_options(True)] == ["taipei", "new_taipei"]
```

- [ ] **Step 4: Run the registry tests and verify the missing module failure**

Run: `python -m pytest tests/test_city_config.py -q`

Expected: FAIL with `ModuleNotFoundError: No module named 'city_config'`.

- [ ] **Step 5: Implement the immutable city registry and feature flag**

```python
@dataclass(frozen=True)
class CityDefinition:
    code: str
    name: str
    aliases: tuple[str, ...]
    districts: tuple[str, ...]
    bounds: tuple[float, float, float, float]

CITIES = {
    "taipei": CityDefinition("taipei", "臺北市", ("臺北市", "台北市", "臺北", "台北"),
        ("松山區", "信義區", "大安區", "中山區", "中正區", "大同區", "萬華區",
         "文山區", "南港區", "內湖區", "士林區", "北投區"),
        (24.8, 25.3, 121.3, 121.8)),
    "new_taipei": CityDefinition("new_taipei", "新北市", ("新北市", "新北"),
        ("板橋區", "三重區", "中和區", "永和區", "新莊區", "新店區", "土城區",
         "蘆洲區", "汐止區", "樹林區", "鶯歌區", "三峽區", "淡水區", "瑞芳區",
         "五股區", "泰山區", "林口區", "深坑區", "石碇區", "坪林區", "三芝區",
         "石門區", "八里區", "平溪區", "雙溪區", "貢寮區", "金山區", "萬里區",
         "烏來區"), (24.5, 25.4, 121.2, 122.1)),
}

def normalize_city(value):
    if value is None or not str(value).strip():
        return None
    cleaned = str(value).strip()
    for code, definition in CITIES.items():
        if cleaned == code or cleaned in definition.aliases:
            return code
    raise ValueError("不支援的城市")

def city_name(code):
    return CITIES[code].name

def validate_city_district(city, district):
    if district and district not in CITIES[city].districts:
        raise ValueError(f"{district} 不屬於 {CITIES[city].name}")

def public_city_options(new_taipei_enabled):
    codes = ["taipei"] + (["new_taipei"] if new_taipei_enabled else [])
    return [{"code": code, "name": CITIES[code].name,
             "districts": list(CITIES[code].districts)} for code in codes]
```

Add to `Config`:

```python
NEW_TAIPEI_ENABLED = os.getenv("NEW_TAIPEI_ENABLED", "0") == "1"
```

- [ ] **Step 6: Run focused and characterization tests**

Run: `python -m pytest tests/test_city_config.py tests/test_app_routes.py tests/test_app_errors.py -q`

Expected: PASS.

- [ ] **Step 7: Commit the registry boundary**

```bash
git add city_config.py config.py .env.example tests/test_city_config.py tests/test_app_routes.py tests/test_app_errors.py
git commit -m "feat: centralize supported parking cities"
```

---

### Task 2: Add additive city/source schema and city-aware queries

**Files:**
- Create: `migrations/20260826_add_parking_sources.sql`
- Modify: `schema.sql`
- Modify: `database.py`
- Modify: `tests/test_database_collector.py`
- Modify: `tests/test_deploy_analytics_contract.py`

**Interfaces:**
- Changes lot input contract to require `city`, `source`, `source_lot_id`.
- Produces: `fetch_latest_snapshot_times(connection) -> dict[str, datetime]` keyed by source.
- Changes: `fetch_current_lots(connection, city=None, district=None, freshness_minutes=45)`.

- [ ] **Step 1: Write failing SQL contract tests**

```python
def test_upsert_lots_binds_city_source_and_source_id():
    connection = SpyConnection([])
    row = sample_lot()
    row.update(city="臺北市", source="taipei", source_lot_id="TPE0001")
    database.upsert_parking_lots(connection, [row])
    sql, values = connection.spy_cursor.calls[0]
    assert "city" in sql and "source_lot_id" in sql
    assert values[0][:4] == ("TPE0001", "臺北市", "taipei", "TPE0001")

def test_current_lots_can_filter_city_and_district():
    connection = SpyConnection([])
    database.fetch_current_lots(connection, city="新北市", district="板橋區", freshness_minutes=45)
    sql, params = connection.spy_cursor.calls[0]
    assert "AND city = %s" in sql
    assert params == (45, "新北市", "板橋區")
```

- [ ] **Step 2: Verify the SQL tests fail on the old signatures**

Run: `python -m pytest tests/test_database_collector.py -q`

Expected: FAIL because `city/source/source_lot_id` are absent and `city` is not accepted.

- [ ] **Step 3: Write the idempotent migration**

The migration must query `INFORMATION_SCHEMA.COLUMNS` before each `ALTER`, backfill existing rows, then add the unique key only when absent:

```sql
UPDATE parking_lots
SET city = '臺北市', source = 'taipei', source_lot_id = lot_id
WHERE city IS NULL OR source IS NULL OR source_lot_id IS NULL;

ALTER TABLE parking_lots MODIFY city VARCHAR(20) NOT NULL;
ALTER TABLE parking_lots MODIFY source VARCHAR(20) NOT NULL;
ALTER TABLE parking_lots MODIFY source_lot_id VARCHAR(64) NOT NULL;
```

Use the existing prepared-statement pattern in `migrations/20260819_add_parking_metadata.sql` for conditional column and index creation.

- [ ] **Step 4: Update schema and parameterized database functions**

`fetch_latest_snapshot_times` must issue one grouped query:

```python
def fetch_latest_snapshot_times(connection):
    sql = """
        SELECT l.source, MAX(s.captured_at) AS captured_at
        FROM parking_snapshots s
        JOIN parking_lots l ON l.lot_id = s.lot_id
        GROUP BY l.source
    """
    with connection.cursor() as cursor:
        cursor.execute(sql)
        return {row["source"]: row["captured_at"] for row in cursor.fetchall()}
```

Keep `fetch_latest_snapshot_time` temporarily as a wrapper returning the maximum value so existing callers remain green until Task 6.

- [ ] **Step 5: Run database and migration contracts**

Run: `python -m pytest tests/test_database_collector.py tests/test_deploy_analytics_contract.py -q`

Expected: PASS.

- [ ] **Step 6: Commit the additive schema**

```bash
git add schema.sql migrations/20260826_add_parking_sources.sql database.py tests/test_database_collector.py tests/test_deploy_analytics_contract.py
git commit -m "feat: store parking lot city and source"
```

---

### Task 3: Implement the New Taipei source adapter and coordinate conversion

**Files:**
- Create: `new_taipei_source.py`
- Create: `tests/test_new_taipei_source.py`
- Create: `tests/fixtures/new_taipei_static.json`
- Create: `tests/fixtures/new_taipei_dynamic.json`
- Modify: `requirements.txt`

**Interfaces:**
- Produces: `twd97_to_wgs84(x: object, y: object) -> tuple[float | None, float | None]`.
- Produces: `fetch_pages(dataset_id: str, timeout: int, http_get=requests.get) -> list[dict]`.
- Produces: `parse_static(rows: list[dict], realtime_ids: set[str], captured_at: datetime) -> list[dict]`.
- Produces: `parse_dynamic(rows: list[dict], captured_at: datetime) -> list[dict]`.
- Produces: `NewTaipeiSourceAdapter.collect(timeout: int) -> tuple[list[dict], list[dict], dict]`.
- Uses static endpoint `https://data.ntpc.gov.tw/api/datasets/b1464ef0-9c7c-4a6f-abf7-6bdf32847e68/json`.
- Uses dynamic endpoint `https://data.ntpc.gov.tw/api/datasets/e09b35a5-a738-48cc-b0f5-570b67ad9c78/json`.

- [ ] **Step 1: Save minimal anonymous fixtures from the official field contract**

Use these exact reduced records so the fixture contains no unnecessary official fields:

```json
[
  {"ID":"010056","AREA":"板橋區","NAME":"遠東百貨停車場","ADDRESS":"板橋區中山路一段152號","PAYEX":"小型車計時60元;","SERVICETIME":"0~24時","TW97X":"296882","TW97Y":"2767068","TOTALCAR":"453","SUMMARY":"立體式建築附設停車空間"},
  {"ID":"060040","AREA":"新店區","NAME":"崇光女子高中地下停車場","ADDRESS":"新北市新店區三民路19號B2","PAYEX":"小型車月租3000元;","SERVICETIME":"0~24時","TW97X":"304461.5","TW97Y":"2762748.5","TOTALCAR":"60","SUMMARY":"立體式-機械建築附設停車空間"}
]
```

```json
[
  {"ID":"010056","AVAILABLECAR":"24"},
  {"ID":"060040","AVAILABLECAR":"-9"},
  {"ID":"999999","AVAILABLECAR":"3"}
]
```

- [ ] **Step 2: Write failing parser, pagination and coordinate tests**

```python
def test_twd97_known_point_converts_to_wgs84():
    latitude, longitude = twd97_to_wgs84(296882.0, 2767068.0)
    assert latitude == pytest.approx(25.0109252, abs=0.00001)
    assert longitude == pytest.approx(121.4644919, abs=0.00001)

def test_dynamic_skips_negative_and_prefixes_ids(captured_at):
    rows = parse_dynamic(load_fixture("new_taipei_dynamic.json"), captured_at)
    assert [row["lot_id"] for row in rows] == ["NTP:010056", "NTP:999999"]
    assert rows[0]["source_updated_at"] == captured_at

def test_fetch_pages_retries_one_timeout_then_completes(monkeypatch):
    http_get = SequenceGet([requests.Timeout(), page_response(1, 2), page_response(2, 2)])
    assert len(fetch_pages("dataset", 3, http_get=http_get)) == 2
    assert http_get.call_count == 3
```

- [ ] **Step 3: Run the adapter tests and verify missing implementation failures**

Run: `python -m pytest tests/test_new_taipei_source.py -q`

Expected: FAIL because `new_taipei_source` does not exist.

- [ ] **Step 4: Add PyProj and implement conversion with one reusable transformer**

```python
from pyproj import Transformer

_TWD97_TO_WGS84 = Transformer.from_crs("EPSG:3826", "EPSG:4326", always_xy=True)

def twd97_to_wgs84(x, y):
    try:
        longitude, latitude = _TWD97_TO_WGS84.transform(float(x), float(y))
    except (TypeError, ValueError):
        return None, None
    if not (24.5 <= latitude <= 25.4 and 121.2 <= longitude <= 122.1):
        return None, None
    return latitude, longitude
```

Pin dependency as `pyproj>=3.7,<4`.

- [ ] **Step 5: Implement bounded page collection and normalized records**

Use `page` and `size=1000`; retry each failed page at most twice. Deduplicate by `ID` with last official row winning and return metrics containing `duplicates`, `invalid_dynamic`, and `unmatched_dynamic`.

`parse_static` must emit exactly the keys accepted by `database.upsert_parking_lots`, including:

```python
{"lot_id": f"NTP:{source_id}", "city": "新北市", "source": "new_taipei",
 "source_lot_id": source_id, "operator_type": "官方路外停車場",
 "supports_realtime": source_id in realtime_ids, "fare_rules_json": None}
```

- [ ] **Step 6: Run adapter tests and compile the module**

Run: `python -m pytest tests/test_new_taipei_source.py -q && python -m compileall -q new_taipei_source.py`

Expected: PASS and exit code 0.

- [ ] **Step 7: Commit the source adapter**

```bash
git add new_taipei_source.py requirements.txt tests/test_new_taipei_source.py tests/fixtures/new_taipei_static.json tests/fixtures/new_taipei_dynamic.json
git commit -m "feat: parse New Taipei parking data"
```

---

### Task 4: Collect Taipei and New Taipei in isolated transactions

**Files:**
- Modify: `collector.py`
- Modify: `tests/test_database_collector.py`
- Modify: `tests/test_collector_parsing.py`

**Interfaces:**
- Produces: `collect_source(source: str, timeout: int = 15) -> dict`.
- Changes: `collect_once(timeout=15, new_taipei_enabled=None) -> dict[str, dict]`.
- Produces: `fetch_source_lot_state(connection, source: str) -> dict` containing latest lot update time and `{lot_id: total_spaces}`.
- Keeps existing `parse_static` and `parse_dynamic` import compatibility for Taipei tests.

- [ ] **Step 1: Write failing transaction-isolation tests**

```python
def test_collect_once_commits_taipei_when_new_taipei_fails(monkeypatch):
    monkeypatch.setattr(collector, "collect_source", lambda source, timeout=15: (
        {"lots": 2, "snapshots": 2} if source == "taipei"
        else (_ for _ in ()).throw(requests.Timeout("new taipei timeout"))))
    result = collector.collect_once(new_taipei_enabled=True)
    assert result["taipei"]["status"] == "ok"
    assert result["new_taipei"]["status"] == "error"

def test_source_write_rolls_back_only_its_own_connection(monkeypatch):
    taipei_connection = TransactionConnection()
    new_taipei_connection = TransactionConnection()
    connections = [taipei_connection, new_taipei_connection]
    monkeypatch.setattr(collector, "get_connection", lambda: connections.pop(0))
    monkeypatch.setattr(collector, "insert_snapshots", lambda connection, rows: (
        (_ for _ in ()).throw(RuntimeError("insert failed"))
        if connection is new_taipei_connection else len(rows)))
    result = collector.collect_once(new_taipei_enabled=True)
    assert result["taipei"]["status"] == "ok"
    assert result["new_taipei"]["status"] == "error"
    assert taipei_connection.committed is True
    assert new_taipei_connection.rolled_back is True

def test_new_taipei_static_download_is_skipped_within_one_day(monkeypatch):
    monkeypatch.setattr(collector, "fetch_source_lot_state", lambda *_args: {
        "latest_updated_at": datetime.now(timezone.utc) - timedelta(hours=2),
        "totals": {"NTP:010056": 453}})
    static_fetch = Mock(side_effect=AssertionError("static endpoint must be skipped"))
    monkeypatch.setattr(collector, "fetch_new_taipei_static", static_fetch)
    collector.collect_source("new_taipei", timeout=3)
    static_fetch.assert_not_called()
```

- [ ] **Step 2: Run focused tests and verify failure**

Run: `python -m pytest tests/test_database_collector.py tests/test_collector_parsing.py -q`

Expected: FAIL because `collect_once` still uses one Taipei transaction.

- [ ] **Step 3: Extract one-source transaction and orchestrate independently**

```python
def collect_once(timeout=15, new_taipei_enabled=None):
    enabled = Config.NEW_TAIPEI_ENABLED if new_taipei_enabled is None else new_taipei_enabled
    sources = ["taipei"] + (["new_taipei"] if enabled else [])
    results = {}
    for source in sources:
        try:
            results[source] = {"status": "ok", **collect_source(source, timeout)}
        except Exception as exc:
            logger.exception("collector_source_failed source=%s", source)
            results[source] = {"status": "error", "error": type(exc).__name__}
    return results
```

`collect_source` opens the source-owned connection, reads its current lot state, and always downloads dynamic data. For New Taipei it downloads static data only when no lots exist or `latest_updated_at` is at least 24 hours old; otherwise it validates dynamic rows against the stored totals. It finishes all downloads and validation before the first write, then commits or rolls back only that connection.

- [ ] **Step 4: Preserve CLI failure observability**

When any enabled source has status `error`, print the JSON summary and exit with status 1; a scheduler can detect partial failure even though the successful city remains committed.

- [ ] **Step 5: Run collector suites**

Run: `python -m pytest tests/test_collector_parsing.py tests/test_database_collector.py tests/test_new_taipei_source.py -q`

Expected: PASS.

- [ ] **Step 6: Commit independent collection**

```bash
git add collector.py tests/test_database_collector.py tests/test_collector_parsing.py
git commit -m "feat: isolate parking source collection"
```

---

### Task 5: Make Gemini and geocoding city-aware

**Files:**
- Modify: `ai_service.py`
- Modify: `geocoder.py`
- Modify: `tests/test_services.py`
- Modify: `tests/test_app_errors.py`

**Interfaces:**
- Adds `city: str | None` to `LocationCandidate` and `ParkingIntent`.
- Changes: `normalize_address(address: str, city: str | None = None) -> str`.
- Changes: `geocode_address(address, connection, city=None, http_get=requests.get)`.
- Geocode result adds `city` and `district` when they can be inferred from verified display text.

- [ ] **Step 1: Write failing intent and geocoder tests**

```python
def test_new_taipei_intent_accepts_city_and_board_district():
    intent = ParkingIntent(intent="recommend", original_destination="板橋車站",
        address="板橋車站", city="new_taipei", district="板橋區",
        arrival_time=None, missing_fields=[], location_candidates=[])
    assert intent.city == "new_taipei"

def test_geocoder_accepts_verified_new_taipei_result(connection):
    result = geocode_address("板橋車站", connection, city="new_taipei",
        http_get=lambda *_args, **_kwargs: response([{
            "display_name": "板橋車站, 板橋區, 新北市, 臺灣",
            "lat": "25.0143", "lon": "121.4638"}]))
    assert result["city"] == "new_taipei"
    assert result["district"] == "板橋區"
```

- [ ] **Step 2: Verify tests fail under Taipei-only validation**

Run: `python -m pytest tests/test_services.py tests/test_app_errors.py -q`

Expected: FAIL with Taipei-only validation or missing `city`.

- [ ] **Step 3: Replace local district constants with `city_config` validation**

Pydantic validators must validate the pair in `model_validator(mode="after")`; a district without city may infer its unique owning city, while ambiguous or inconsistent pairs raise `ValueError`.

Update `_prompt` to say only:

```text
只接受臺北市與新北市地址；city 必須是 taipei 或 new_taipei。
地標不唯一時列出最多 3 個候選，不得虛構停車資料或座標。
```

- [ ] **Step 4: Generalize Nominatim query and response verification**

Do not prepend Taipei when the input already contains either city. If a city is supplied, reject display names that do not contain that city's official name. Continue caching by the fully normalized city-qualified address.

- [ ] **Step 5: Run intent/geocoder regression tests**

Run: `python -m pytest tests/test_services.py tests/test_app_errors.py tests/test_app_routes.py -q`

Expected: PASS, including existing Taipei landmark aliases.

- [ ] **Step 6: Commit city-aware location parsing**

```bash
git add ai_service.py geocoder.py tests/test_services.py tests/test_app_errors.py
git commit -m "feat: resolve Taipei and New Taipei destinations"
```

---

### Task 6: Query both cities with per-source freshness

**Files:**
- Modify: `app.py`
- Modify: `database.py`
- Modify: `tests/test_app_routes.py`
- Modify: `tests/test_app_errors.py`
- Modify: `tests/test_database_collector.py`

**Interfaces:**
- Changes manual payload to `{city, district, address, arrival_time}`.
- Produces: `parking_data_status(required_sources: set[str], now=None) -> dict[str, dict]`.
- Address query calls `fetch_current_lots(city=None, district=None, ...)`; district query passes city name and district.
- API candidates expose `city`, `source`, and `data_time_label`.

- [ ] **Step 1: Write failing route tests for cross-city behavior and freshness**

```python
def test_address_query_can_return_cross_border_lots(monkeypatch):
    taipei = lot_row()
    taipei.update(lot_id="TPE", city="臺北市", source="taipei")
    new_taipei = lot_row()
    new_taipei.update(lot_id="NTP:1", city="新北市", source="new_taipei",
                      district="板橋區")
    rows = [taipei, new_taipei]
    monkeypatch.setattr(app_module, "fetch_current_lots", lambda *_args, **_kwargs: rows)
    response = make_client(NEW_TAIPEI_ENABLED=True).post("/api/query", json={
        "mode": "manual", "city": "new_taipei", "address": "板橋車站",
        "arrival_time": "2026-08-26T18:00:00+08:00"})
    body = response.get_json()
    visible = body["recommendations"] + body["other_recommended"]
    assert {row["city"] for row in visible} == {"臺北市", "新北市"}

def test_taipei_fresh_does_not_hide_stale_new_taipei(monkeypatch):
    monkeypatch.setattr(app_module, "fetch_latest_snapshot_times", lambda _connection: {
        "taipei": datetime.now(timezone.utc),
        "new_taipei": datetime.now(timezone.utc) - timedelta(hours=2)})
    statuses = app_module.parking_data_status({"taipei", "new_taipei"})
    assert statuses["taipei"]["status"] == "fresh"
    assert statuses["new_taipei"]["status"] == "stale"
```

- [ ] **Step 2: Run focused route tests and verify failure**

Run: `python -m pytest tests/test_app_routes.py tests/test_app_errors.py tests/test_database_collector.py -q`

Expected: FAIL because route and SQL use one Taipei-wide freshness state.

- [ ] **Step 3: Implement city-aware parsing and required-source selection**

Manual district queries require `city`; full-address queries infer verified city and set required sources to both enabled cities because the 1.5 km circle may cross the boundary.

```python
required_sources = ({"taipei", "new_taipei"}
                    if destination and app.config["NEW_TAIPEI_ENABLED"]
                    else {parsed["city"]})
```

The stale fallback may include old rows only for the stale source; fresh source rows keep the 45-minute bound. Combine the two bounded queries rather than dropping freshness globally.

If `NEW_TAIPEI_ENABLED` is false, reject a submitted `city=new_taipei` with HTTP 400 even if stale New Taipei rows remain in the database.

- [ ] **Step 4: Keep recommendation and walking behavior unchanged**

Pass the combined rows to existing `rank_candidates`, `select_walking_candidates`, `fetch_walking_routes`, and `split_recommendation_groups`. Do not add a city weight or alter the risk thresholds.

- [ ] **Step 5: Return honest per-source data metadata**

```json
{"data_sources":[
  {"source":"taipei","city":"臺北市","status":"fresh","time_kind":"official"},
  {"source":"new_taipei","city":"新北市","status":"fresh","time_kind":"collected"}
]}
```

- [ ] **Step 6: Run complete backend tests**

Run: `python -m pytest tests/test_app_routes.py tests/test_app_errors.py tests/test_database_collector.py tests/test_analysis.py -q`

Expected: PASS.

- [ ] **Step 7: Commit the dual-city query path**

```bash
git add app.py database.py tests/test_app_routes.py tests/test_app_errors.py tests/test_database_collector.py
git commit -m "feat: query nearby parking across Taipei cities"
```

---

### Task 7: Add city selection and source labels to the PWA

**Files:**
- Modify: `templates/index.html`
- Modify: `static/app.js`
- Modify: `static/style.css`
- Modify: `static/sw.js`
- Modify: `tests/test_frontend_contract.py`
- Modify: `tests/test_pwa_contract.py`

**Interfaces:**
- Consumes: `public_city_options(Config.NEW_TAIPEI_ENABLED)` injected as JSON.
- Manual API payload includes `city`.
- Candidate cards consume `city`, `source`, and per-source time metadata.

- [ ] **Step 1: Write failing frontend contracts**

```python
def test_manual_form_uses_server_city_options_and_sends_city():
    assert 'id="city"' in HTML
    assert 'id="city-options" type="application/json"' in HTML
    assert "city:citySelect.value" in JS
    assert "const districts =" not in JS

def test_new_taipei_disabled_never_appears_in_rendered_options():
    response = create_app({"TESTING": True, "NEW_TAIPEI_ENABLED": False}).test_client().get("/")
    html = response.get_data(as_text=True)
    assert "新北市" not in html
```

- [ ] **Step 2: Verify contracts fail against the Taipei-only UI**

Run: `python -m pytest tests/test_frontend_contract.py tests/test_pwa_contract.py -q`

Expected: FAIL because the city select and injected options are absent.

- [ ] **Step 3: Render server-owned city options and dependent districts**

Add city select before district and embed JSON safely:

```html
<label>城市<select id="city"></select></label>
<script id="city-options" type="application/json">{{ city_options | tojson }}</script>
```

JavaScript parses this node, fills cities, then replaces district options on `change`. Address mode may leave district empty; district-only submit requires both selects.

- [ ] **Step 4: Add source-aware copy without changing card size or ranking**

Display `臺北市官方資料時間` for Taipei and `新北市系統取得時間` for New Taipei. `formatFullAddress` must prepend `lot.city`, not always `臺北市`; navigation URLs continue using the lot name plus full address.

Update the footer to link both official dataset pages when New Taipei is enabled; when disabled, render only the existing Taipei source link.

- [ ] **Step 5: Bump PWA asset version exactly once**

Update the `style.css` query version and `static/sw.js` cache name so installed iPhones receive the new form and JavaScript.

- [ ] **Step 6: Run frontend syntax and contracts**

Run: `node --check static/app.js && python -m pytest tests/test_frontend_contract.py tests/test_pwa_contract.py -q`

Expected: PASS.

- [ ] **Step 7: Commit the city-aware interface**

```bash
git add templates/index.html static/app.js static/style.css static/sw.js tests/test_frontend_contract.py tests/test_pwa_contract.py
git commit -m "feat: expose Taipei and New Taipei in PWA"
```

---

### Task 8: Retain exactly the data needed for seven-day history

**Files:**
- Create: `parking_cleanup.py`
- Create: `tests/test_parking_cleanup.py`
- Modify: `database.py`
- Create: `deploy/parking-snapshot-cleanup.service`
- Create: `deploy/parking-snapshot-cleanup.timer`
- Modify: `README.md`

**Interfaces:**
- Produces: `delete_old_snapshots(connection, cutoff_utc: datetime, batch_size: int = 5000) -> int`.
- Produces: `run_cleanup(now_utc: datetime | None = None) -> int` using cutoff `now - 8 days`.

- [ ] **Step 1: Write failing cleanup transaction tests**

```python
def test_cleanup_uses_eight_day_cutoff_and_commits(monkeypatch):
    now = datetime(2026, 8, 26, 12, tzinfo=timezone.utc)
    connection = TransactionConnection(rowcount=12)
    monkeypatch.setattr(parking_cleanup, "get_connection", lambda: connection)
    assert parking_cleanup.run_cleanup(now) == 12
    assert connection.params[0] == now - timedelta(days=8)
    assert connection.committed and connection.closed

def test_cleanup_rolls_back_and_closes_on_delete_error(monkeypatch):
    connection = TransactionConnection(delete_error=RuntimeError("delete failed"))
    monkeypatch.setattr(parking_cleanup, "get_connection", lambda: connection)
    with pytest.raises(RuntimeError, match="delete failed"):
        parking_cleanup.run_cleanup()
    assert connection.rolled_back and connection.closed
```

- [ ] **Step 2: Run cleanup tests and verify missing module failure**

Run: `python -m pytest tests/test_parking_cleanup.py -q`

Expected: FAIL with missing `parking_cleanup`.

- [ ] **Step 3: Implement bounded deletion**

Delete batches by selecting `snapshot_id` ordered by primary key and limiting to 5000, committing each completed batch. Stop when fewer than 5000 rows are deleted. Never delete rows at exactly the cutoff.

- [ ] **Step 4: Document one daily scheduler command**

Document `python parking_cleanup.py` as the daily command and state that collector/query behavior does not depend on cleanup success. Do not put credentials in README or service files.

- [ ] **Step 5: Run history and cleanup regression tests**

Run: `python -m pytest tests/test_parking_cleanup.py tests/test_app_routes.py::test_history_route_returns_real_series_and_closes_connection tests/test_analysis.py::test_history_series_converts_naive_utc_and_skips_invalid_rows -q`

Expected: PASS.

- [ ] **Step 6: Commit retention behavior**

```bash
git add parking_cleanup.py database.py deploy/parking-snapshot-cleanup.service deploy/parking-snapshot-cleanup.timer README.md tests/test_parking_cleanup.py
git commit -m "feat: retain eight days of parking snapshots"
```

---

### Task 9: Phase 1 migration rehearsal, full QA and release gate

**Files:**
- Modify: `README.md`
- Create: `docs/QA_REVIEW_2026-08-26_NEW_TAIPEI_PHASE1.md`

**Interfaces:**
- No new runtime interface; this task proves the phase is releasable with the flag off and on.

- [ ] **Step 1: Install locked dependencies and run all automated tests**

Run: `python -m pip install -r requirements.txt && python -m pytest -q`

Expected: all tests PASS.

- [ ] **Step 2: Run static validation**

Run: `python -m compileall -q . && node --check static/app.js && node --check static/admin_analytics.js && git diff --check`

Expected: exit code 0 for every command.

- [ ] **Step 3: Rehearse migration twice on a disposable MySQL database**

Run the schema followed by `migrations/20260826_add_parking_sources.sql` twice. Verify existing lot count is unchanged, all existing rows are `臺北市/taipei`, and `SHOW INDEX` contains `uq_lots_source_id` only once.

- [ ] **Step 4: Perform offline API smoke tests with the feature flag in both states**

With `NEW_TAIPEI_ENABLED=0`, verify `/`, `/health`, Taipei manual query, Taipei chat fallback, and history. With fixture-backed `NEW_TAIPEI_ENABLED=1`, verify 板橋車站, 新北市政府, a cross-border destination, navigation URL, fee details, map markers, and history.

- [ ] **Step 5: Record measured evidence and rollback instructions**

The QA document must include commands, pass counts, smoke outcomes, lot/snapshot counts, invalid-coordinate count, stale-source behavior, and rollback: set `NEW_TAIPEI_ENABLED=0` and restart; do not remove additive columns.

- [ ] **Step 6: Commit the release evidence**

```bash
git add README.md docs/QA_REVIEW_2026-08-26_NEW_TAIPEI_PHASE1.md
git commit -m "docs: record New Taipei phase one QA"
```
