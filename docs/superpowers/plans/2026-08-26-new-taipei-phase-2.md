# New Taipei Off-Street Parking Phase 2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在新北累積至少七天有效快照後，加入有樣本門檻的雙北分析、城市化 Dashboard、常見新北費率解析及受控的場站型態補強。

**Architecture:** 查詢推薦仍使用第一階段的共用規則；第二階段只新增分析與維運可觀測性。城市維度寫入 Analytics 明細，collector 執行摘要寫入小型 `collector_runs` 表；費率與 OSM 只擴充既有 service，不新增平行服務。

**Tech Stack:** Python 3.13、Flask、PyMySQL、Pandas、Pytest、Vanilla JavaScript、MySQL 8

**Spec:** `docs/superpowers/specs/2026-08-26-new-taipei-off-street-parking-design.md`

## Global Constraints

- 執行前必須證明新北已有連續至少 7 天有效快照；未達門檻時停止本計畫。
- 臺北與新北行政區分開排名，不產生跨城市單一平均名次。
- 未達最低有效場站數或歷史樣本數時顯示「樣本不足」，不得產生精確分數。
- Fee parser 只支援真實資料中高頻且可由固定 fixture 驗證的格式，不新增場站名稱特例。
- 場站型態來源優先序固定為 `manual > official > osm > unknown`，OSM 不推斷機械式。
- Dashboard 查詢必須有界，不記錄 IP、精確手機位置、Cookie、Authorization 或 API 金鑰。
- 第一階段全部回歸測試必須持續通過。

## File Structure

- Create `migrations/20260902_add_new_taipei_analytics.sql`: Analytics city 與 collector run schema。
- Create `tests/fixtures/new_taipei_fee_samples.json`: 去識別、高頻官方費率樣本與期望結果。
- Modify `analytics_capture.py`, `analytics_database.py`, `analytics_service.py`: 城市事件、collector health 與城市彙整。
- Modify `collector.py`, `parking_cleanup.py`: 每來源與每日清理結束後寫入 bounded run summary。
- Modify `database.py`, `analysis.py`, `app.py`: 歷史樣本門檻與分城市行政區分析。
- Modify `fee_service.py`: 高頻新北費率格式。
- Modify `parking_metadata.py`: 新北 bbox 與保守 OSM 補強。
- Modify `templates/index.html`, `static/app.js`, `static/style.css`: 簡潔的分城市行政區排行。
- Modify `templates/admin_analytics.html`, `static/admin_analytics.js`, `static/admin_analytics.css`: 城市篩選與來源健康。

---

### Task 1: Enforce the seven-day data-readiness gate

**Files:**
- Modify: `database.py`
- Modify: `app.py`
- Modify: `tests/test_database_collector.py`
- Modify: `tests/test_app_routes.py`

**Interfaces:**
- Produces: `fetch_source_history_coverage(connection, source: str) -> dict` with `first_at`, `last_at`, `snapshot_count`, `active_lot_count`.
- Produces: `history_source_ready(coverage: dict, minimum_days: int = 7) -> bool`.

- [ ] **Step 1: Write failing readiness tests**

```python
def test_new_taipei_history_requires_seven_full_days():
    coverage = {"first_at": datetime(2026, 8, 19), "last_at": datetime(2026, 8, 26),
                "snapshot_count": 5000, "active_lot_count": 300}
    assert history_source_ready(coverage, 7) is True
    coverage["first_at"] = datetime(2026, 8, 20)
    assert history_source_ready(coverage, 7) is False

def test_coverage_query_is_grouped_and_source_bounded():
    connection = SpyConnection([])
    database.fetch_source_history_coverage(connection, "new_taipei")
    sql, params = connection.spy_cursor.calls[0]
    assert "MIN(s.captured_at)" in sql and "COUNT(DISTINCT s.lot_id)" in sql
    assert params == ("new_taipei",)
```

- [ ] **Step 2: Run focused tests and verify failure**

Run: `python -m pytest tests/test_database_collector.py tests/test_app_routes.py -q`

Expected: FAIL because readiness interfaces do not exist.

- [ ] **Step 3: Implement one aggregate query and pure readiness predicate**

Treat database datetimes as UTC. Return false for missing values, non-positive samples, fewer than three active lots, or coverage shorter than exactly seven 24-hour periods.

- [ ] **Step 4: Expose readiness without changing recommendation output**

Add `analysis_availability` to API metadata; when false, current parking and seven-day per-lot chart still work, but city ranking endpoints return `sample_status: insufficient`.

- [ ] **Step 5: Run backend regression tests and commit**

Run: `python -m pytest tests/test_database_collector.py tests/test_app_routes.py -q`

```bash
git add database.py app.py tests/test_database_collector.py tests/test_app_routes.py
git commit -m "feat: gate New Taipei analysis by history coverage"
```

---

### Task 2: Add sample-aware city and district analysis

**Files:**
- Modify: `database.py`
- Modify: `analysis.py`
- Modify: `app.py`
- Modify: `templates/index.html`
- Modify: `static/app.js`
- Modify: `static/style.css`
- Modify: `static/sw.js`
- Modify: `tests/test_analysis.py`
- Modify: `tests/test_app_routes.py`
- Modify: `tests/test_frontend_contract.py`
- Modify: `tests/test_pwa_contract.py`

**Interfaces:**
- Produces: `fetch_district_history_summary(connection, city, start_utc, end_utc) -> list[dict]`.
- Produces: `rank_city_districts(rows, min_lots=3, min_samples=12) -> list[dict]`.
- Produces route: `GET /api/analysis/districts?city=taipei|new_taipei`.

- [ ] **Step 1: Write failing sample-threshold tests**

```python
def test_city_district_ranking_excludes_insufficient_samples():
    rows = [
        {"district": "板橋區", "lot_count": 8, "sample_count": 96, "hell_score": 72.4},
        {"district": "烏來區", "lot_count": 1, "sample_count": 4, "hell_score": 99.0},
    ]
    result = rank_city_districts(rows, min_lots=3, min_samples=12)
    assert result == [{"district": "板橋區", "lot_count": 8,
                       "sample_count": 96, "hell_score": 72.4, "rank": 1}]

def test_district_route_never_mix_cities(monkeypatch):
    monkeypatch.setattr(app_module, "fetch_source_history_coverage", lambda *_args: {
        "first_at": datetime(2026, 8, 19), "last_at": datetime(2026, 8, 26),
        "snapshot_count": 5000, "active_lot_count": 300})
    monkeypatch.setattr(app_module, "fetch_district_history_summary", lambda *_args: [{
        "city": "新北市", "district": "板橋區", "lot_count": 8,
        "sample_count": 96, "hell_score": 72.4}])
    response = make_client(NEW_TAIPEI_ENABLED=True).get(
        "/api/analysis/districts?city=new_taipei")
    assert all(row["city"] == "新北市" for row in response.get_json()["districts"])
```

- [ ] **Step 2: Verify the analysis interfaces are missing**

Run: `python -m pytest tests/test_analysis.py tests/test_app_routes.py -q`

Expected: FAIL.

- [ ] **Step 3: Implement bounded SQL aggregation**

The SQL must filter `city`, `captured_at BETWEEN start AND end`, valid totals and `0 <= available <= total`, then return district, distinct lot count, sample count, and weighted occupied ratio. Do not load raw seven-day snapshots into Python.

- [ ] **Step 4: Implement pure ranking and honest API output**

Sort eligible districts by hell score descending and include `lot_count`, `sample_count`, `from`, `to`, and `sample_status`. Ineligible districts appear under `insufficient_districts` without score or rank.

Add one collapsed `行政區停車壓力排行` section to the public page. It loads the endpoint only when opened, keeps Taipei and New Taipei in separate selectable views, and renders a plain list with score, valid lots and samples; it renders `樣本不足` without a number for ineligible districts.

Bump the app asset query and service-worker cache once in this task so installed PWAs receive the new section.

- [ ] **Step 5: Run tests and commit**

Run: `node --check static/app.js && python -m pytest tests/test_analysis.py tests/test_app_routes.py tests/test_frontend_contract.py tests/test_pwa_contract.py -q`

```bash
git add database.py analysis.py app.py templates/index.html static/app.js static/style.css static/sw.js tests/test_analysis.py tests/test_app_routes.py tests/test_frontend_contract.py tests/test_pwa_contract.py
git commit -m "feat: analyze parking pressure by city district"
```

---

### Task 3: Record city and collector source health for Dashboard

**Files:**
- Create: `migrations/20260902_add_new_taipei_analytics.sql`
- Modify: `schema.sql`
- Modify: `analytics_capture.py`
- Modify: `analytics_database.py`
- Modify: `collector.py`
- Modify: `parking_cleanup.py`
- Modify: `tests/test_analytics_insights_database.py`
- Modify: `tests/test_analytics_routes.py`
- Modify: `tests/test_database_collector.py`
- Modify: `tests/test_parking_cleanup.py`

**Interfaces:**
- Adds nullable `city VARCHAR(20)` to `analytics_query_details`.
- Adds `collector_runs(source, started_at, completed_at, status, lots_seen, snapshots_inserted, invalid_count, timeout_count, error_code)`.
- Adds `maintenance_runs(job_name, started_at, completed_at, status, affected_rows, error_code)`.
- Produces: `insert_collector_run(connection, row: dict) -> int`.
- Produces: `insert_maintenance_run(connection, row: dict) -> int`.
- Produces: `fetch_collector_health(connection, start_utc, end_utc) -> list[dict]`.
- Produces: `fetch_source_quality(connection) -> list[dict]` and `fetch_snapshot_metrics(connection, start_utc, end_utc) -> dict`.

- [ ] **Step 1: Write failing parameterization and bounded-query tests**

```python
def test_collector_run_insert_uses_fixed_parameters():
    connection = SpyConnection([])
    analytics_database.insert_collector_run(connection, collector_run())
    sql, params = connection.spy_cursor.calls[0]
    assert "INSERT INTO collector_runs" in sql
    assert params[0] == "new_taipei"
    assert len(params) == 9

def test_collector_health_is_time_bounded():
    connection = SpyConnection([])
    analytics_database.fetch_collector_health(connection, START, END)
    sql, params = connection.spy_cursor.calls[0]
    assert "started_at BETWEEN %s AND %s" in sql
    assert params == (START, END)

def test_snapshot_metrics_report_total_daily_and_latest_cleanup():
    connection = SpyConnection([{"snapshot_total": 12000,
                                 "snapshots_added": 1800,
                                 "last_cleanup_at": END,
                                 "last_cleanup_rows": 1400}])
    result = analytics_database.fetch_snapshot_metrics(connection, START, END)
    assert result["snapshot_total"] == 12000
    assert result["last_cleanup_rows"] == 1400
```

- [ ] **Step 2: Run analytics/database tests and verify failures**

Run: `python -m pytest tests/test_analytics_insights_database.py tests/test_analytics_routes.py tests/test_database_collector.py -q`

Expected: FAIL because city and collector health are absent.

- [ ] **Step 3: Add idempotent schema and parameterized access**

The migration follows the existing `INFORMATION_SCHEMA` pattern. Add indexes on `(city, occurred_at)`, `(source, started_at)`, and `(job_name, started_at)`; neither run table needs a foreign key.

- [ ] **Step 4: Write collector summaries without affecting source commits**

After each source transaction closes, open a separate best-effort analytics connection and insert one run. If run logging fails, log the exception and preserve collector success/error result.

After snapshot cleanup commits or rolls back, write one best-effort `maintenance_runs` record; this secondary write must never change the cleanup exit result.

- [ ] **Step 5: Pass verified city into query detail capture**

Use the geocoded/validated city, never parse city again from raw query text. Old query rows remain nullable and render as `未知`.

- [ ] **Step 6: Run tests and commit**

Run: `python -m pytest tests/test_analytics_insights_database.py tests/test_analytics_routes.py tests/test_database_collector.py tests/test_parking_cleanup.py -q`

```bash
git add schema.sql migrations/20260902_add_new_taipei_analytics.sql analytics_capture.py analytics_database.py collector.py parking_cleanup.py tests/test_analytics_insights_database.py tests/test_analytics_routes.py tests/test_database_collector.py tests/test_parking_cleanup.py
git commit -m "feat: observe parking sources by city"
```

---

### Task 4: Add city filtering and source-health sections to Dashboard

**Files:**
- Modify: `analytics_service.py`
- Modify: `app.py`
- Modify: `templates/admin_analytics.html`
- Modify: `static/admin_analytics.js`
- Modify: `static/admin_analytics.css`
- Modify: `tests/test_admin_dashboard.py`
- Modify: `tests/test_frontend_contract.py`
- Modify: `tests/test_analytics_metrics.py`

**Interfaces:**
- Admin query accepts `city=all|taipei|new_taipei`.
- Admin JSON adds `source_health`, `source_quality`, `snapshot_metrics`, and city-filtered `insights`.
- Produces: `summarize_collector_health(rows) -> list[dict]`.

- [ ] **Step 1: Write failing Dashboard contracts**

```python
def test_admin_api_filters_city_and_returns_source_health(monkeypatch):
    response = make_client().get("/admin/api/analytics?range=7d&city=new_taipei")
    body = response.get_json()
    assert body["selected_city"] == "new_taipei"
    assert body["source_health"][0]["source"] in {"taipei", "new_taipei"}

def test_dashboard_has_city_filter_and_plain_source_health_table():
    assert 'id="city-filter"' in ADMIN_HTML
    assert 'id="source-health-body"' in ADMIN_HTML
    assert "renderSourceHealth" in ADMIN_JS
```

- [ ] **Step 2: Run Dashboard tests and verify failure**

Run: `python -m pytest tests/test_admin_dashboard.py tests/test_frontend_contract.py tests/test_analytics_metrics.py -q`

Expected: FAIL.

- [ ] **Step 3: Implement allowlisted city filtering**

Reject unknown city values with HTTP 400. Pass a city parameter into bounded detail queries; `all` omits the predicate. Keep status endpoint and Basic Auth behavior unchanged.

- [ ] **Step 4: Render source health in plain language**

Show city/source, latest success, failed runs, timeout count, invalid ratio, inserted snapshots, and known/unknown fee/facility ratios. A separate maintenance row shows snapshot total, selected-period additions, latest cleanup time and deleted rows. Empty data says `尚無來源執行紀錄` rather than loading forever.

- [ ] **Step 5: Run syntax, accessibility contracts and commit**

Run: `node --check static/admin_analytics.js && python -m pytest tests/test_admin_dashboard.py tests/test_frontend_contract.py tests/test_analytics_metrics.py -q`

```bash
git add analytics_service.py app.py templates/admin_analytics.html static/admin_analytics.js static/admin_analytics.css tests/test_admin_dashboard.py tests/test_frontend_contract.py tests/test_analytics_metrics.py
git commit -m "feat: show city source health in dashboard"
```

---

### Task 5: Expand fee parsing only for measured New Taipei formats

**Files:**
- Create: `tests/fixtures/new_taipei_fee_samples.json`
- Modify: `fee_service.py`
- Modify: `tests/test_fee_service.py`

**Interfaces:**
- Keeps: `build_fee_summary(fare_rules_json, fee_info, arrival_time, day_info)`.
- Adds no lot-name or lot-ID parameters.

- [ ] **Step 1: Generate an anonymous frequency report from collected `PAYEX` values**

Normalize whitespace and digits, group formats by marker pattern, and select only patterns that together cover at least 80% of non-empty New Taipei fee strings. Save representative official strings without lot IDs or names in the fixture with explicit expected hourly/cap labels.

- [ ] **Step 2: Write one parametrized failing test over the fixture**

```python
@pytest.mark.parametrize("case", json.loads(FIXTURE.read_text(encoding="utf-8")))
def test_new_taipei_common_fee_formats(case):
    result = build_fee_summary(None, case["fee_info"], ARRIVAL_TIMES[case["day"]],
                               {"kind": case["day"], "label": case["day_label"]})
    assert result["hourly_fee_label"] == case["hourly_fee_label"]
    assert result["daily_cap_label"] == case["daily_cap_label"]
```

- [ ] **Step 3: Run fee tests and confirm only unsupported high-frequency cases fail**

Run: `python -m pytest tests/test_fee_service.py -q`

Expected: existing tests PASS; new fixture cases FAIL with `官方未標示` or mismatched day price.

- [ ] **Step 4: Extend token-based parsing without facility exceptions**

Add only regex branches tied to fixture markers for weekday, weekend/holiday, per-hour or per-half-hour, and same-day cap. Continue excluding motorcycle, monthly, per-entry and ambiguous numbers.

- [ ] **Step 5: Run fee and route regressions and commit**

Run: `python -m pytest tests/test_fee_service.py tests/test_fee_weekend_regression.py tests/test_app_routes.py -q`

```bash
git add fee_service.py tests/test_fee_service.py tests/fixtures/new_taipei_fee_samples.json
git commit -m "feat: parse common New Taipei parking fees"
```

---

### Task 6: Conservatively extend facility metadata to New Taipei

**Files:**
- Modify: `parking_metadata.py`
- Modify: `tests/test_parking_metadata.py`
- Modify: `deploy/parking-metadata-refresh.service`

**Interfaces:**
- Changes: `fetch_osm_parking_elements(city: str, timeout=15)` using an allowlisted city bbox.
- Keeps priority: `manual > official > osm > unknown`.

- [ ] **Step 1: Write failing city-bbox and matching tests**

```python
def test_new_taipei_osm_request_uses_new_taipei_bbox(monkeypatch):
    fetch_osm_parking_elements("new_taipei", timeout=4)
    query = captured_request["data"]
    assert "24.5,121.2,25.4,122.1" in query

def test_osm_never_infers_mechanical_for_unknown_new_taipei_lot():
    updates = match_osm_facilities([new_taipei_lot()], [osm_element(parking="multi-storey")])
    assert updates[0]["facility_type"] == "立體停車場"
    assert updates[0]["facility_type"] != "機械停車場"
```

- [ ] **Step 2: Verify existing Taipei-only fetch fails the city contract**

Run: `python -m pytest tests/test_parking_metadata.py -q`

Expected: FAIL on missing city argument/bbox behavior.

- [ ] **Step 3: Add allowlisted bboxes and sequential city refresh**

Fetch one city at a time to keep Overpass requests bounded. Match only lots with unknown facility type and valid coordinates; retain the existing 40-metre and ambiguity rejection rules.

- [ ] **Step 4: Run metadata tests and commit**

Run: `python -m pytest tests/test_parking_metadata.py tests/test_database_null_metadata_regression.py -q`

```bash
git add parking_metadata.py deploy/parking-metadata-refresh.service tests/test_parking_metadata.py
git commit -m "feat: enrich New Taipei parking facility metadata"
```

---

### Task 7: Full Phase 2 review and release evidence

**Files:**
- Modify: `README.md`
- Create: `docs/QA_REVIEW_2026-09-02_NEW_TAIPEI_PHASE2.md`

**Interfaces:**
- No new runtime interface; verifies analysis honesty, Dashboard bounds and complete regression safety.

- [ ] **Step 1: Prove the production-like data gate before enabling analysis**

Record `first_at`, `last_at`, `snapshot_count`, `active_lot_count`, invalid-coordinate ratio, fee-known ratio and facility-known ratio for New Taipei. Stop if coverage is under seven full days.

- [ ] **Step 2: Run all automated checks**

Run: `python -m pytest -q && python -m compileall -q . && node --check static/app.js && node --check static/admin_analytics.js && git diff --check`

Expected: every command exits 0.

- [ ] **Step 3: Rehearse the phase-two migration twice**

Verify one nullable `city` column, one `collector_runs` table, no duplicate indexes, unchanged old analytics row counts, and successful rollback of the application code without dropping additive schema.

- [ ] **Step 4: Perform browser and admin acceptance**

Check city filter `all/臺北市/新北市`, source timeout state, empty state, sample-insufficient district, eligible district ranking, high-frequency fee cards, facility source, mobile width, and that the public recommendation order remains unchanged.

- [ ] **Step 5: Commit measured QA evidence**

```bash
git add README.md docs/QA_REVIEW_2026-09-02_NEW_TAIPEI_PHASE2.md
git commit -m "docs: record New Taipei phase two QA"
```
