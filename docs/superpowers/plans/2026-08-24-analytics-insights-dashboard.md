# Analytics Insights Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Capture useful team-test query diagnostics, recommendation snapshots and feedback, then present them in a simple four-section admin Dashboard.

**Architecture:** Keep `analytics_events` for interaction events, add one query-detail row and at most three recommendation snapshot rows per request, and isolate construction rules in `analytics_capture.py`. Public query handling remains best-effort: analytics failures never change the parking response. The existing admin API returns one bounded, pre-aggregated payload and the existing plain JavaScript Dashboard renders cards and tables without a chart library.

**Tech Stack:** Python 3.11+, Flask, PyMySQL/MySQL 8, Pydantic-free fixed dictionaries, vanilla JavaScript, pytest, systemd/Nginx on GCP e2-micro.

**Spec:** `docs/superpowers/specs/2026-08-24-analytics-insights-dashboard-design.md`

## Global Constraints

- Add exactly two tables: `analytics_query_details` and `analytics_recommendations`.
- Add at most one production Python file: `analytics_capture.py`.
- Added production code must keep the net delta at or below 950 lines (raw added and removed are reported separately); stop and reduce scope if the net exceeds the cap.
- Do not alter parking recommendation, fee, walking-order, Gemini or geocoding behavior.
- Never store Cookie, Authorization, API keys, full HTTP headers, model prompts/responses or tracebacks.
- Raw input is limited to 500 characters and is nulled after 14 days; all analytics rows are deleted after 90 days.
- Dashboard keeps `today`, `7d` and `30d`, uses bounded lists, adds no framework and adds no chart.
- Analytics persistence is best-effort and must never change the public query status code or payload.
- Team VM uses `ANALYTICS_SEGMENT_MIN_DEVICES=1`; source default remains `5`.

---

### Task 1: Query-detail and recommendation persistence

**Files:**
- Modify: `schema.sql`
- Create: `migrations/20260824_add_analytics_insights.sql`
- Modify: `analytics_database.py`
- Modify: `analytics_cleanup.py`
- Test: `tests/test_analytics_insights_database.py`
- Test: `tests/test_deploy_analytics_contract.py`

**Interfaces:**
- Produces: `upsert_query_detail(connection, detail) -> int`
- Produces: `replace_recommendation_snapshots(connection, request_id, rows) -> int`
- Produces: `update_query_feedback(connection, request_id, anonymous_id_hash, feedback_code) -> int`
- Produces: `fetch_insight_details(connection, start_utc, end_utc, recent_limit=20) -> list[dict]`
- Produces: `fetch_insight_recommendations(connection, start_utc, end_utc) -> list[dict]`
- Produces: `scrub_expired_query_text(connection, cutoff_utc) -> int`
- Produces: `delete_expired_insights(connection, cutoff_utc) -> dict[str, int]`

- [ ] **Step 1: Write failing persistence and migration tests**

Create `tests/test_analytics_insights_database.py` with spy connection tests that require fixed parameter order, one `executemany`, bounded time predicates and deletion order:

```python
def test_upsert_query_detail_binds_text_only_as_parameters():
    connection = SpyConnection(rowcount=1)
    detail = sample_detail(raw_query_text="今晚去台北車站")
    assert upsert_query_detail(connection, detail) == 1
    sql, params = connection.executions[0]
    assert "INSERT INTO analytics_query_details" in sql
    assert "ON DUPLICATE KEY UPDATE" in sql
    assert "今晚去台北車站" not in sql
    assert "今晚去台北車站" in params

def test_recommendations_replace_in_one_transaction_shape():
    connection = SpyConnection(rowcount=3)
    count = replace_recommendation_snapshots(
        connection, VALID_REQUEST_ID, [sample_recommendation(rank=n) for n in (1, 2, 3)])
    assert count == 3
    assert "DELETE FROM analytics_recommendations WHERE request_id = %s" in connection.executions[0][0]
    assert "INSERT INTO analytics_recommendations" in connection.executions[1][0]
    assert len(connection.executions[1][1]) == 3

def test_cleanup_scrubs_text_before_deleting_children_and_parent():
    connection = SpyConnection()
    scrub_expired_query_text(connection, RAW_CUTOFF)
    result = delete_expired_insights(connection, RETENTION_CUTOFF)
    sql = "\n".join(call[0] for call in connection.executions)
    assert "SET raw_query_text = NULL" in sql
    assert sql.index("DELETE FROM analytics_recommendations") < sql.index("DELETE FROM analytics_query_details")
    assert set(result) == {"recommendations", "query_details"}
```

Extend the migration contract test to require both table names, primary keys, `occurred_at` indexes, the 500-character raw input column and no additional analytics table.

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```powershell
python -m pytest tests/test_analytics_insights_database.py tests/test_deploy_analytics_contract.py -q
```

Expected: import or assertion failures because the new SQL functions and migration do not exist.

- [ ] **Step 3: Add the idempotent migration and schema definitions**

Define both tables in `schema.sql` and the migration. Use this fixed shape:

```sql
CREATE TABLE IF NOT EXISTS analytics_query_details (
    request_id CHAR(36) PRIMARY KEY,
    occurred_at DATETIME NOT NULL,
    anonymous_id_hash CHAR(64) NOT NULL,
    source VARCHAR(20) NOT NULL,
    query_mode VARCHAR(10) NOT NULL,
    raw_query_text VARCHAR(500) NULL,
    parsed_query_json JSON NULL,
    destination_label VARCHAR(255) NULL,
    district VARCHAR(20) NULL,
    arrival_time DATETIME NULL,
    intent VARCHAR(20) NULL,
    outcome_code VARCHAR(40) NOT NULL,
    error_stage VARCHAR(32) NULL,
    fallback_reason VARCHAR(80) NULL,
    data_status VARCHAR(20) NULL,
    result_count INT NOT NULL DEFAULT 0,
    location_choice_count TINYINT NOT NULL DEFAULT 0,
    parse_ms INT NULL, geocode_ms INT NULL, freshness_ms INT NULL,
    database_ms INT NULL, walking_ms INT NULL, total_ms INT NOT NULL,
    official_data_at DATETIME NULL,
    collected_at DATETIME NULL,
    feedback_code VARCHAR(24) NULL,
    INDEX idx_query_details_occurred (occurred_at),
    INDEX idx_query_details_district_occurred (district, occurred_at),
    INDEX idx_query_details_device_occurred (anonymous_id_hash, occurred_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS analytics_recommendations (
    request_id CHAR(36) NOT NULL,
    rank_position TINYINT NOT NULL,
    occurred_at DATETIME NOT NULL,
    parking_lot_id VARCHAR(32) NOT NULL,
    lot_name VARCHAR(100) NOT NULL,
    recommendation_group VARCHAR(20) NOT NULL,
    available_spaces INT NULL,
    total_spaces INT NULL,
    pressure_label VARCHAR(20) NULL,
    decision_status VARCHAR(20) NULL,
    straight_distance_m INT NULL,
    walking_distance_m INT NULL,
    walking_minutes DECIMAL(8,2) NULL,
    distance_source VARCHAR(16) NOT NULL,
    hourly_fee_label VARCHAR(100) NULL,
    daily_cap_label VARCHAR(100) NULL,
    facility_type_label VARCHAR(40) NULL,
    navigation_clicked_at DATETIME NULL,
    PRIMARY KEY (request_id, rank_position),
    INDEX idx_recommendations_occurred (occurred_at),
    INDEX idx_recommendations_lot_occurred (parking_lot_id, occurred_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
```

- [ ] **Step 4: Implement parameterized persistence and cleanup**

Use fixed column tuples like existing `EVENT_COLUMNS`. `replace_recommendation_snapshots` must reject more than three rows with `ValueError`, delete the request rows, then issue one `executemany`. `update_query_feedback` must use:

```sql
UPDATE analytics_query_details
SET feedback_code = %s
WHERE request_id = %s AND anonymous_id_hash = %s
```

`fetch_insight_details` and `fetch_insight_recommendations` must use `[start_utc, end_utc)` and `ORDER BY occurred_at DESC`; only the recent-query query applies `LIMIT %s`.

Update `analytics_cleanup.run_cleanup()` to scrub at 14 days, delete recommendation/detail rows at 90 days, then delete existing events at 90 days in one transaction.

- [ ] **Step 5: Run focused and full tests**

Run:

```powershell
python -m pytest tests/test_analytics_insights_database.py tests/test_deploy_analytics_contract.py -q
python -m pytest -q
```

Expected: all pass.

- [ ] **Step 6: Commit Task 1**

```powershell
git add schema.sql migrations/20260824_add_analytics_insights.sql analytics_database.py analytics_cleanup.py tests/test_analytics_insights_database.py tests/test_deploy_analytics_contract.py
git commit -m "feat: add analytics insight persistence"
```

---

### Task 2: Fixed query trace and recommendation builders

**Files:**
- Create: `analytics_capture.py`
- Test: `tests/test_analytics_capture.py`

**Interfaces:**
- Produces: `infer_destination_district(explicit, parsed_address, display_address) -> str | None`
- Produces: `new_query_trace(payload, query_mode, source, occurred_at) -> dict`
- Produces: `build_query_detail(trace, request_id, anonymous_id_hash, outcome_code, total_ms) -> dict | None`
- Produces: `build_recommendation_snapshots(request_id, occurred_at, groups) -> list[dict]`
- Consumes: Taipei district names from `ai_service.TAIPEI_DISTRICTS`

- [ ] **Step 1: Write failing builder tests**

Create tests with literal expected values:

```python
def test_district_inference_does_not_guess_from_nearest_lot():
    assert infer_destination_district(None, "臺北市中正區北平西路3號", None) == "中正區"
    assert infer_destination_district(None, "台北車站", "臺北市, 中正區, 北平西路") == "中正區"
    assert infer_destination_district("信義區", "臺北市中正區北平西路3號", None) == "信義區"
    assert infer_destination_district(None, "台北車站", "臺北市") is None

def test_query_detail_truncates_raw_input_and_whitelists_parsed_json():
    trace = new_query_trace({"mode": "chat", "message": "甲" * 600}, "chat", "direct", NOW)
    trace["parsed"] = {"address": "北平西路3號", "district": "中正區", "secret": "drop"}
    detail = build_query_detail(trace, REQUEST_ID, "a" * 64, "success", 1234)
    assert len(detail["raw_query_text"]) == 500
    assert "secret" not in detail["parsed_query_json"]
    assert detail["district"] == "中正區"

def test_recommendation_snapshots_keep_only_first_three():
    rows = build_recommendation_snapshots(REQUEST_ID, NOW, sample_groups())
    assert [row["rank_position"] for row in rows] == [1, 2, 3]
    assert rows[0]["distance_source"] == "walking"
    assert rows[2]["recommendation_group"] == "backup"
```

- [ ] **Step 2: Run tests and verify RED**

Run `python -m pytest tests/test_analytics_capture.py -q`.

Expected: FAIL because `analytics_capture` does not exist.

- [ ] **Step 3: Implement the small fixed builders**

Use one frozen whitelist for parsed fields:

```python
PARSED_FIELDS = ("intent", "address", "district", "arrival_time", "destination_label")

def infer_destination_district(explicit, parsed_address, display_address):
    if explicit in TAIPEI_DISTRICTS:
        return explicit
    for text in (parsed_address, display_address):
        matches = [district for district in TAIPEI_DISTRICTS if district in (text or "")]
        if len(matches) == 1:
            return matches[0]
    return None
```

`new_query_trace` must choose chat `message`, manual `address`, or manual `district` as raw text. `build_query_detail` returns `None` without a valid anonymous hash. Serialize only `PARSED_FIELDS`, converting datetime values to ISO strings. Recommendation snapshots consume only `groups["recommendations"][:3]`; derive `recommendation_group` as `recommended` when `decision_status == "recommended"`, otherwise `backup`. Use walking distance when both walking distance and minutes exist, otherwise straight-line distance.

- [ ] **Step 4: Run focused and full tests**

Run:

```powershell
python -m pytest tests/test_analytics_capture.py -q
python -m pytest -q
```

- [ ] **Step 5: Enforce the file budget and commit**

Run `(Get-Content analytics_capture.py).Count`; expected at or below 160 lines.

```powershell
git add analytics_capture.py tests/test_analytics_capture.py
git commit -m "feat: build bounded analytics query traces"
```

---

### Task 3: Capture query details and top-three snapshots

**Files:**
- Modify: `app.py`
- Modify: `analytics_database.py`
- Test: `tests/test_analytics_routes.py`

**Interfaces:**
- Consumes all Task 1 persistence functions and Task 2 builders.
- Produces no public API changes except continued existing `request_id`.

- [ ] **Step 1: Write failing route tests**

Add tests that use real route flow with analytics writers replaced by separate event/detail spies:

```python
def test_address_query_records_inferred_district_timings_and_three_snapshots(monkeypatch):
    app = make_analytics_app(monkeypatch)
    captured = {"details": [], "recommendations": []}
    app.extensions["analytics_detail_writer"] = captured["details"].append
    app.extensions["analytics_recommendation_writer"] = captured["recommendations"].append
    response = app.test_client().post(
        "/api/query", json=manual_payload("台北車站"), headers=analytics_headers())
    assert response.status_code == 200
    assert captured["details"][0]["district"] == "中正區"
    assert captured["details"][0]["total_ms"] >= 0
    assert len(captured["recommendations"][0]) <= 3

def test_analytics_detail_failure_never_changes_success_response(monkeypatch):
    app = make_analytics_app(monkeypatch)
    app.extensions["analytics_detail_writer"] = lambda _row: (_ for _ in ()).throw(RuntimeError("down"))
    response = app.test_client().post(
        "/api/query", json=manual_payload(), headers=analytics_headers())
    assert response.status_code == 200
    assert response.get_json()["recommendations"]
```

Also assert failed geocode records `error_stage="geocode"`, location choices record `location_choice_count`, and no-consent public mode records no details.

- [ ] **Step 2: Run focused tests and verify RED**

Run the new test names with `python -m pytest tests/test_analytics_routes.py -k "detail or snapshot" -q`.

- [ ] **Step 3: Add isolated writers and route trace updates**

Register two best-effort extensions in `create_app`:

```python
app.extensions["analytics_detail_writer"] = analytics_detail_writer
app.extensions["analytics_recommendation_writer"] = analytics_recommendation_writer
```

Each opens its own short connection, commits on success, rolls back on failure and closes. Initialize `trace = new_query_trace(...)` immediately after parsing request JSON. Update only fixed keys at parse, geocode, freshness, database and walking boundaries. Extend `terminal(..., trace=None, recommendation_groups=None)` to build and write detail/snapshots after the existing event, inside separate `try/except` blocks whose warnings contain only `request_id` and stage. The location-choice response remains HTTP 200 and is not counted as a completed or failed query event; before returning it, call the same detail writer directly with `outcome_code="location_choice_required"` and `location_choice_count=len(verified_choices)`.

For district inference, pass explicit district, parsed address and the geocoder `display_address`; do not assign the inferred result back to `parsed["district"]`.

- [ ] **Step 4: Run focused and full tests**

Run:

```powershell
python -m pytest tests/test_analytics_routes.py -q
python -m pytest -q
python -m compileall -q app.py analytics_capture.py analytics_database.py
```

- [ ] **Step 5: Commit Task 3**

```powershell
git add app.py analytics_database.py tests/test_analytics_routes.py
git commit -m "feat: capture query diagnostics and recommendations"
```

---

### Task 4: Interaction events and parking feedback

**Files:**
- Modify: `analytics_service.py`
- Modify: `analytics_database.py`
- Modify: `app.py`
- Modify: `templates/index.html`
- Modify: `static/app.js`
- Modify: `static/sw.js`
- Test: `tests/test_analytics_routes.py`
- Test: `tests/test_frontend_contract.py`

**Interfaces:**
- Produces: `POST /api/analytics/feedback`
- Extends: `POST /api/analytics/events` accepted types
- Consumes: `update_query_feedback` and existing HMAC identity rules

- [ ] **Step 1: Write failing event and feedback tests**

Require the fixed event allowlist and feedback validation:

```python
@pytest.mark.parametrize("event_type", [
    "location_choice_shown", "location_choice_selected",
    "map_marker_clicked", "history_opened",
])
def test_new_browser_events_are_accepted(monkeypatch, event_type):
    app = make_analytics_app(monkeypatch)
    app.extensions["analytics_writer"] = lambda _event: None
    response = app.test_client().post("/api/analytics/events", json={
        "event_type": event_type, "analytics_id": VALID_UUID,
        "request_id": VALID_REQUEST_ID, "source": "direct",
        "clicked_rank": 1, "parking_lot_id": "TPE1",
    })
    assert response.status_code == 204

def test_feedback_updates_only_matching_request_and_uuid(monkeypatch):
    app = make_analytics_app(monkeypatch)
    captured = []
    app.extensions["analytics_feedback_writer"] = lambda *args: captured.append(args) or 1
    response = app.test_client().post("/api/analytics/feedback", json={
        "analytics_id": VALID_UUID, "request_id": VALID_REQUEST_ID,
        "feedback_code": "found_space",
    })
    assert response.status_code == 204
    assert captured[0][1:] == (VALID_REQUEST_ID, "found_space")
```

Frontend contract tests require one delegated handler each for map/history/navigation, the three feedback buttons, no navigation blocking, and `analytics-v3` PWA cache keys.

- [ ] **Step 2: Run focused tests and verify RED**

Run:

```powershell
python -m pytest tests/test_analytics_routes.py tests/test_frontend_contract.py -k "browser_events or feedback or interaction" -q
```

- [ ] **Step 3: Implement the fixed endpoints and frontend events**

Extend `BROWSER_EVENT_TYPES` only with the four fixed names. Permit existing scalar fields but reject unknown keys. Add an `analytics_feedback_writer` extension that calls `update_query_feedback` in a short transaction. The feedback endpoint accepts exactly `analytics_id`, `request_id`, `feedback_code`; validate UUIDs and the three-code allowlist, return 404 when no matching detail exists, otherwise 204.

Render this result block, hidden until a successful recommendation response:

```html
<section id="parking-feedback" class="parking-feedback" hidden>
  <h2>這次推薦有幫助嗎？</h2>
  <button data-feedback="found_space">有，找到車位</button>
  <button data-feedback="full_on_arrival">到場已滿</button>
  <button data-feedback="did_not_go">沒有前往</button>
  <p id="feedback-status" role="status"></p>
</section>
```

Use event delegation and existing `activeRequestId`/UUID. Mark feedback buttons disabled after a successful 204. Send location-choice events when choices render/select; map event when a marker priority is selected; history event before loading history; retain current navigation event. Bump template and service-worker asset versions to `analytics-v3`.

- [ ] **Step 4: Run frontend and full checks**

```powershell
python -m pytest tests/test_analytics_routes.py tests/test_frontend_contract.py tests/test_pwa_contract.py -q
node --check static/app.js
node --check static/sw.js
python -m pytest -q
```

- [ ] **Step 5: Commit Task 4**

```powershell
git add analytics_service.py analytics_database.py app.py templates/index.html static/app.js static/sw.js tests/test_analytics_routes.py tests/test_frontend_contract.py tests/test_pwa_contract.py
git commit -m "feat: track parking interactions and feedback"
```

---

### Task 5: Insight aggregation and bounded admin API

**Files:**
- Modify: `analytics_service.py`
- Modify: `app.py`
- Modify: `config.py`
- Modify: `.env.example`
- Test: `tests/test_analytics_metrics.py`
- Test: `tests/test_admin_dashboard.py`

**Interfaces:**
- Produces: `summarize_insights(details, recommendations, events) -> dict`
- Extends: `GET /admin/api/analytics` response with `funnel`, `destinations`, `lots`, `stage_timings`, `recent_queries`, `feedback`

- [ ] **Step 1: Write failing aggregation tests**

Use hand-built rows and literal expectations:

```python
def test_insights_are_simple_bounded_and_use_query_counts():
    result = summarize_insights(sample_details(), sample_recommendations(), sample_events())
    assert result["districts"] == [{"district": "中正區", "queries": 3}]
    assert result["funnel"] == {
        "completed": 3, "location_choices": 1, "navigations": 2, "feedback": 1,
    }
    assert result["feedback"] == {"found_space": 1, "full_on_arrival": 0, "did_not_go": 0}
    assert len(result["destinations"]) <= 10
    assert len(result["recent_queries"]) <= 20

def test_stage_medians_ignore_null_values():
    result = summarize_insights(stage_detail_rows(), [], [])
    assert result["stage_timings"] == {
        "parse_ms": 10, "geocode_ms": 200, "freshness_ms": 2,
        "database_ms": 30, "walking_ms": 500,
    }
```

Add a route test that spies on exactly one details query and one recommendations query, requires `ANALYTICS_SEGMENT_MIN_DEVICES` from config, and asserts the admin response stays JSON-serializable.

- [ ] **Step 2: Run focused tests and verify RED**

Run `python -m pytest tests/test_analytics_metrics.py tests/test_admin_dashboard.py -q`.

- [ ] **Step 3: Implement bounded summaries and config**

Make the existing setting environment-controlled:

```python
ANALYTICS_SEGMENT_MIN_DEVICES = int(
    os.getenv("ANALYTICS_SEGMENT_MIN_DEVICES", "5"))
```

Add `ANALYTICS_SEGMENT_MIN_DEVICES=5` to `.env.example`. Aggregate in Python from bounded 30-day rows: districts by query count after the device threshold, destinations top 10, navigated lots top 10, stage medians, failure counts, fixed funnel, feedback counts and recent 20. Do not expose `anonymous_id_hash` or parsed JSON in the API.

The admin route fetches details and recommendations once each and passes them with events to `summarize_insights`; any read failure returns the existing 503 without partial contradictory cards.

- [ ] **Step 4: Run focused, full and serialization tests**

```powershell
python -m pytest tests/test_analytics_metrics.py tests/test_admin_dashboard.py -q
python -m pytest -q
python -m compileall -q analytics_service.py app.py config.py
```

- [ ] **Step 5: Commit Task 5**

```powershell
git add analytics_service.py app.py config.py .env.example tests/test_analytics_metrics.py tests/test_admin_dashboard.py
git commit -m "feat: summarize team analytics insights"
```

---

### Task 6: Simple four-section Dashboard

**Files:**
- Modify: `templates/admin_analytics.html`
- Modify: `static/admin_analytics.js`
- Modify: `static/admin_analytics.css`
- Test: `tests/test_admin_dashboard.py`
- Test: `tests/test_frontend_contract.py`

**Interfaces:**
- Consumes the Task 5 admin JSON keys only.
- Produces no new endpoint.

- [ ] **Step 1: Write failing Dashboard contract tests**

Require the four visible section headings and prohibit charts/device hashes:

```python
def test_dashboard_has_four_plain_language_sections_and_no_charts():
    html = DASHBOARD_TEMPLATE.read_text(encoding="utf-8")
    script = DASHBOARD_SCRIPT.read_text(encoding="utf-8")
    for heading in ("目前使用狀況", "使用者去哪裡", "系統哪裡需要改善", "最近查詢"):
        assert heading in html
    assert "canvas" not in html
    assert "Chart(" not in script
    assert "anonymous_id_hash" not in html + script

def test_empty_tables_use_specific_helpful_messages():
    script = DASHBOARD_SCRIPT.read_text(encoding="utf-8")
    assert "尚無行政區資料，請完成一次新查詢" in script
    assert "尚無導航點擊" in script
    assert "尚無回饋" in script
```

- [ ] **Step 2: Run focused tests and verify RED**

Run `python -m pytest tests/test_admin_dashboard.py tests/test_frontend_contract.py -q`.

- [ ] **Step 3: Replace diagnostic-heavy layout with four simple sections**

Keep six KPI cards only: completed, success, median/P95 combined, navigation rate, found-space feedback rate and anonymous devices. Render:

- a four-step text funnel;
- three compact tables for districts, destinations and navigated lots;
- one stage timing row plus failure/location-choice tables;
- a recent-query table capped at 20.

Use `textContent` and DOM nodes exclusively for API values. Add a `renderEmptyRow(bodyId, columns, message)` helper so each table has a specific explanation. Preserve range buttons, status strip, no-store behavior and mobile one-column layout.

- [ ] **Step 4: Run visual-contract and JavaScript checks**

```powershell
python -m pytest tests/test_admin_dashboard.py tests/test_frontend_contract.py -q
node --check static/admin_analytics.js
python -m pytest -q
```

- [ ] **Step 5: Commit Task 6**

```powershell
git add templates/admin_analytics.html static/admin_analytics.js static/admin_analytics.css tests/test_admin_dashboard.py tests/test_frontend_contract.py
git commit -m "feat: simplify analytics dashboard insights"
```

---

### Task 7: Documentation, line budget and complete QA

**Files:**
- Modify: `README.md`
- Modify: `.env.example`
- Test: all tests

**Interfaces:**
- Consumes all previous tasks.
- Produces deployment and rollback runbook updates.

- [ ] **Step 1: Update the runbook**

Document the migration command, `ANALYTICS_SEGMENT_MIN_DEVICES`, 14-day scrubbing, 90-day deletion, feedback codes, Dashboard URL, team `admin/admin` warning and rollback order. Explicitly state that old `district=NULL` events are not backfilled.

- [ ] **Step 2: Run every offline quality gate**

```powershell
python -m pytest -q
python -m compileall -q .
node --check static/app.js
node --check static/sw.js
node --check static/admin_analytics.js
git diff --check
```

Expected: all pass with no warnings from application code.

- [ ] **Step 3: Enforce production line budget**

Run and sum added/removed production lines separately:

```powershell
git diff --numstat origin/master -- '*.py' '*.js' '*.html' '*.css' ':!tests/**'
$n = git diff --numstat origin/master -- '*.py' '*.js' '*.html' '*.css' ':!tests/**'
$added = ($n | ForEach-Object { [int](($_ -split "`t")[0]) } | Measure-Object -Sum).Sum
$removed = ($n | ForEach-Object { [int](($_ -split "`t")[1]) } | Measure-Object -Sum).Sum
"added=$added removed=$removed net=$($added - $removed)"
```

Final rule: the production net delta (`added - removed`) must be at or below 950 lines;
report raw added and removed totals separately. If the net exceeds 950, remove optional
prose/layout helpers or duplicate mapping code; do not weaken validation, best-effort
isolation or tests. Current branch status for the complete backend plus four-section
Dashboard: 1033 added / 198 removed / 835 net.

- [ ] **Step 4: Run pre-merge review**

Review the complete diff for Critical/Important correctness, security, privacy leakage, N+1 queries, unbounded responses, migration rollback compatibility and public-query behavior. Fix findings with focused regression tests and rerun Step 2.

- [ ] **Step 5: Commit documentation and any review fixes**

```powershell
git add README.md .env.example
git commit -m "docs: document analytics insights operations"
```

- [ ] **Step 6: Push, CI and squash merge**

Push `codex/analytics-insights-dashboard`, create a PR against `master`, wait for all CI jobs, and squash merge only when all are green.

- [ ] **Step 7: Back up and deploy**

On the VM, create a full compressed MySQL dump and preserve the active app and htpasswd as rollback artifacts. Apply `migrations/20260824_add_analytics_insights.sql`, set `ANALYTICS_SEGMENT_MIN_DEVICES=1`, deploy the merged commit as a new release, and automatically restore the previous app if process health, Nginx, admin auth or migration checks fail.

- [ ] **Step 8: Live browser acceptance**

With a fresh browser profile:

1. Open the homepage and confirm team mode has no consent card and no console errors.
2. Query `台北車站`; confirm three recommendation cards and destination `臺北市中正區北平西路3號`.
3. Open history, click the first navigation link, then submit `有，找到車位`.
4. Open `/admin/analytics` with `admin/admin`; confirm 中正區, the query, three snapshots, timing stages, navigation and feedback appear.
5. Confirm unauthorized admin remains 401, authorized API is 200 under 2 seconds, `/health` is 200 and journal has no traceback/worker timeout.

- [ ] **Step 9: Report rollback artifacts and final evidence**

Report merged commit, test count, CI URLs, live timings, DB backup path, app rollback path and htpasswd backup path. Do not disclose HMAC, database, Gemini or routing secrets.
