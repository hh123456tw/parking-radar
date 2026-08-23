# Parking Analytics and Operations Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add opt-in anonymous product analytics and a password-protected, read-only owner dashboard that shows usage, parking-data freshness, and lightweight VM health without affecting the public parking query flow.

**Architecture:** Keep the public PWA login-free. Store four allowlisted event types in one MySQL table, hash the browser UUID with an HMAC secret, and never persist destination text, exact coordinates, dialogue, IP, or User-Agent. Reuse Flask/MySQL/Nginx, add small focused analytics/status modules, and protect `/admin/` with Nginx Basic Auth instead of building application accounts.

**Tech Stack:** Python 3.11+, Flask 3, PyMySQL, MySQL 8, vanilla JavaScript, HTML/CSS, Nginx, Linux cron, pytest, Node syntax check.

**Spec:** `C:/Users/cygnu/.gstack/projects/hh123456tw-parking-radar/zebra-master-design-20260823-230638.md`

## Global Constraints

- Public users remain login-free; only `/admin/` is password protected.
- Explicit opt-in is required before any analytics UUID or event is created.
- Never persist complete addresses, destination labels, dialogue, IP, User-Agent, phone location, or exact destination coordinates.
- Analytics failures must never change the public query response.
- Use one `analytics_events` table and no external analytics, queue, monitoring, alerting, or account service.
- Keep the existing recommendation rules, three primary cards, map, history flow, and Google Maps behavior unchanged.
- Store event timestamps in UTC; display and date-window calculations use `Asia/Taipei`.
- Raw analytics events expire after 90 days.
- Every new Python function and non-obvious JavaScript block receives a concise Traditional Chinese comment or docstring.
- Do not deploy, merge, or push during plan execution unless the user separately authorizes it.

## Planned File Structure

- Create `analytics_service.py`: consent parsing, HMAC identity, fixed enums, privacy-safe buckets, event construction, metrics.
- Create `analytics_database.py`: parameterized analytics inserts, navigation validation, dashboard reads, cleanup.
- Create `status_service.py`: app uptime and lightweight Linux/MySQL/data-freshness status with graceful unsupported states.
- Create `analytics_cleanup.py`: one-shot 90-day cleanup entry point for cron.
- Create `templates/admin_analytics.html`: owner-only read-only dashboard shell.
- Create `static/admin_analytics.js`: load and render admin APIs; no third-party SDK.
- Create `static/admin_analytics.css`: isolated dashboard styles.
- Create `migrations/20260823_add_analytics_events.sql`: production migration matching `schema.sql`.
- Create `deploy/nginx-parking-radar-log-format.conf`: no-IP access log and in-memory admin rate-limit zone.
- Modify `app.py`: analytics identity setup, best-effort query events, event endpoint, admin routes.
- Modify `config.py`: analytics secret, retention, admin thresholds, deploy version.
- Modify `schema.sql`: one event table and indexes.
- Modify `static/app.js`, `static/style.css`, `static/sw.js`, `templates/index.html`: opt-in UI and event capture.
- Modify `deploy/nginx-parking-radar.conf`, `.env.example`, `README.md`: Basic Auth, cron, secrets, privacy and operations runbook.
- Create focused tests instead of expanding the already-large `tests/test_app_routes.py` further.

---

### Task 1: Privacy-safe analytics domain helpers

**Files:**
- Create: `analytics_service.py`
- Create: `tests/test_analytics_service.py`
- Modify: `config.py`

**Interfaces:**
- Consumes: Flask-style header mappings and config values.
- Produces: `analytics_identity(headers, secret) -> str | None`, `coarse_area_bucket(latitude, longitude) -> str | None`, `availability_bucket(spaces) -> str`, `build_query_event(...) -> dict`, `summarize_events(rows, now_utc) -> dict`.

- [ ] **Step 1: Write failing privacy and bucket tests**

```python
from uuid import UUID

from analytics_service import (
    analytics_identity, availability_bucket, coarse_area_bucket,
)


def test_identity_requires_opt_in_valid_uuid_and_secret():
    raw_id = "550e8400-e29b-41d4-a716-446655440000"
    assert analytics_identity({}, "secret") is None
    assert analytics_identity({"X-Analytics-Consent": "1"}, "secret") is None
    headers = {"X-Analytics-Consent": "1", "X-Analytics-Id": raw_id}
    digest = analytics_identity(headers, "secret")
    assert len(digest) == 64
    assert raw_id not in digest
    assert analytics_identity(headers, "") is None


def test_buckets_discard_exact_location_and_follow_space_boundaries():
    assert coarse_area_bucket(25.04781, 121.53191) == "25.04,121.53"
    assert [availability_bucket(value) for value in (0, 1, 3, 4, 10, 11)] == [
        "0", "1_3", "1_3", "4_10", "4_10", "11_plus",
    ]
```

- [ ] **Step 2: Run the focused tests and confirm RED**

Run: `python -m pytest tests/test_analytics_service.py -q`

Expected: collection fails because `analytics_service` does not exist.

- [ ] **Step 3: Implement the minimal helpers and locked configuration**

```python
# analytics_service.py
import hashlib
import hmac
from math import floor
from uuid import UUID

EVENT_TYPES = frozenset({
    "query_completed", "query_failed", "navigation_clicked", "pwa_opened",
})
QUERY_MODES = frozenset({"manual", "chat"})
SOURCES = frozenset({"direct", "shared", "installed_pwa", "unknown"})


def analytics_identity(headers, secret):
    """只有明確同意且 UUID 合法時，才回傳不可逆的固定 HMAC。"""
    if headers.get("X-Analytics-Consent") != "1" or not secret:
        return None
    raw_id = headers.get("X-Analytics-Id", "")
    try:
        UUID(raw_id)
    except (TypeError, ValueError, AttributeError):
        return None
    return hmac.new(secret.encode(), raw_id.encode(), hashlib.sha256).hexdigest()


def coarse_area_bucket(latitude, longitude):
    """把精確座標立即降為約一公里網格；輸出不含原始座標。"""
    if latitude is None or longitude is None:
        return None
    return f"{floor(float(latitude) * 100) / 100:.2f},{floor(float(longitude) * 100) / 100:.2f}"


def availability_bucket(spaces):
    spaces = max(0, int(spaces))
    if spaces == 0:
        return "0"
    if spaces <= 3:
        return "1_3"
    if spaces <= 10:
        return "4_10"
    return "11_plus"
```

Add to `Config`:

```python
ANALYTICS_HMAC_SECRET = os.getenv("ANALYTICS_HMAC_SECRET", "")
ANALYTICS_RETENTION_DAYS = 90
ANALYTICS_SEGMENT_MIN_DEVICES = 5
DEPLOY_VERSION = os.getenv("DEPLOY_VERSION", "unknown")
```

- [ ] **Step 4: Add event-construction tests that reject free text**

```python
def test_query_event_contains_only_allowlisted_fields():
    event = build_query_event(
        event_type="query_completed", request_id="req-1",
        anonymous_id_hash="a" * 64, query_mode="chat",
        outcome_code="success", duration_ms=1234, result_count=3,
        source="shared", district="中正區", latitude=25.04781,
        longitude=121.53191, place_type="station",
    )
    assert event["area_bucket"] == "25.04,121.53"
    assert "address" not in event
    assert "message" not in event
```

- [ ] **Step 5: Implement `build_query_event` with explicit keyword arguments and allowlists**

The function must return only the schema keys listed in Task 2, normalize unknown `source` to `unknown`, raise `ValueError` for an invalid event type/query mode/outcome code, and never accept `**payload` from the request.

- [ ] **Step 6: Run tests and commit**

Run: `python -m pytest tests/test_analytics_service.py -q`

Expected: PASS.

```bash
git add analytics_service.py config.py tests/test_analytics_service.py
git commit -m "feat: add privacy-safe analytics helpers"
```

---

### Task 2: Single analytics table and parameterized persistence

**Files:**
- Create: `analytics_database.py`
- Create: `tests/test_analytics_database.py`
- Create: `migrations/20260823_add_analytics_events.sql`
- Modify: `schema.sql`

**Interfaces:**
- Consumes: event dictionaries from `analytics_service.build_query_event`.
- Produces: `insert_event(connection, event) -> int`, `insert_navigation_event(connection, event) -> int`, `fetch_events(connection, start_utc, end_utc) -> list[dict]`, `delete_expired_events(connection, cutoff_utc) -> int`, `fetch_status_times(connection) -> dict`.

- [ ] **Step 1: Write failing SQL contract tests with spy cursors**

```python
def test_insert_event_uses_fixed_parameterized_columns():
    connection = SpyConnection()
    count = insert_event(connection, sample_query_event())
    sql, params = connection.cursor_instance.executions[0]
    assert "INSERT INTO analytics_events" in sql
    assert "%s" in sql
    assert "台北車站" not in sql
    assert len(params) == 18
    assert count == 1


def test_navigation_insert_requires_matching_recent_query():
    connection = SpyConnection(rowcount=1)
    insert_navigation_event(connection, sample_navigation_event())
    sql, params = connection.cursor_instance.executions[0]
    assert "INSERT IGNORE INTO analytics_events" in sql
    assert "event_type = 'query_completed'" in sql
    assert "INTERVAL 24 HOUR" in sql
    assert params.count("a" * 64) == 2
```

- [ ] **Step 2: Run the database tests and confirm RED**

Run: `python -m pytest tests/test_analytics_database.py -q`

Expected: import failure because `analytics_database.py` does not exist.

- [ ] **Step 3: Add the table to both schema and migration**

```sql
CREATE TABLE IF NOT EXISTS analytics_events (
    event_id BIGINT AUTO_INCREMENT PRIMARY KEY,
    event_type VARCHAR(32) NOT NULL,
    occurred_at DATETIME NOT NULL,
    request_id CHAR(36) NULL,
    anonymous_id_hash CHAR(64) NOT NULL,
    district VARCHAR(20) NULL,
    area_bucket VARCHAR(32) NULL,
    place_type VARCHAR(32) NULL,
    query_mode VARCHAR(10) NULL,
    outcome_code VARCHAR(40) NULL,
    duration_ms INT NULL,
    result_count INT NULL,
    clicked_rank TINYINT NULL,
    parking_lot_id VARCHAR(32) NULL,
    walking_minutes DECIMAL(8, 2) NULL,
    availability_bucket VARCHAR(16) NULL,
    source VARCHAR(20) NOT NULL,
    INDEX idx_analytics_occurred (occurred_at),
    INDEX idx_analytics_type_occurred (event_type, occurred_at),
    INDEX idx_analytics_device_occurred (anonymous_id_hash, occurred_at),
    UNIQUE KEY uq_analytics_request_event (request_id, event_type)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
```

- [ ] **Step 4: Implement fixed SQL functions**

Use an explicit 18-key tuple in one place. `insert_navigation_event` must use `INSERT ... SELECT` from a same-hash `query_completed` event no older than 24 hours. Every function accepts an existing connection; callers own commit, rollback, and close.

- [ ] **Step 5: Add cleanup and read tests**

```python
def test_cleanup_and_fetch_use_bounded_utc_parameters():
    connection = SpyConnection(rows=[])
    cutoff = datetime(2026, 5, 25, tzinfo=timezone.utc)
    delete_expired_events(connection, cutoff)
    fetch_events(connection, cutoff, datetime(2026, 8, 23, tzinfo=timezone.utc))
    assert "occurred_at < %s" in connection.executions[0][0]
    assert "occurred_at >= %s AND occurred_at < %s" in connection.executions[1][0]
```

- [ ] **Step 6: Run tests and commit**

Run: `python -m pytest tests/test_analytics_database.py tests/test_database_collector.py -q`

Expected: PASS with existing database tests unchanged.

```bash
git add analytics_database.py schema.sql migrations/20260823_add_analytics_events.sql tests/test_analytics_database.py
git commit -m "feat: persist anonymous analytics events"
```

---

### Task 3: Best-effort query instrumentation and browser event API

**Files:**
- Modify: `app.py`
- Modify: `analytics_service.py`
- Create: `tests/test_analytics_routes.py`

**Interfaces:**
- Consumes: `analytics_identity`, event builders, analytics DB inserts.
- Produces: query responses with `request_id`; `POST /api/analytics/events`; `app.extensions["analytics_writer"]` test seam.

- [ ] **Step 1: Write failing route tests**

```python
def test_query_without_consent_never_writes_analytics(monkeypatch):
    app = make_analytics_app(monkeypatch)
    written = []
    app.extensions["analytics_writer"] = written.append
    response = app.test_client().post("/api/query", json=manual_payload())
    assert response.status_code == 200
    assert written == []


def test_consented_success_returns_request_id_and_records_no_destination(monkeypatch):
    app = make_analytics_app(monkeypatch)
    written = []
    app.extensions["analytics_writer"] = written.append
    response = app.test_client().post(
        "/api/query", json=manual_payload(address="臺北車站"),
        headers=analytics_headers(),
    )
    body = response.get_json()
    UUID(body["request_id"])
    assert written[0]["event_type"] == "query_completed"
    assert "address" not in written[0]
    assert "臺北車站" not in repr(written[0])
```

- [ ] **Step 2: Run focused route tests and confirm RED**

Run: `python -m pytest tests/test_analytics_routes.py -q`

Expected: missing `request_id` and analytics writer behavior.

- [ ] **Step 3: Add request-scoped instrumentation without changing query decisions**

At query start:

```python
request_id = str(uuid4())
anonymous_hash = analytics_identity(
    request.headers, app.config.get("ANALYTICS_HMAC_SECRET", ""),
)
query_source = request.headers.get("X-Analytics-Source", "unknown")
```

Create one closure inside `create_app`:

```python
def write_analytics_safely(event):
    """分析寫入失敗只能留下不含目的地的警告，不得影響查詢。"""
    if not event:
        return
    try:
        app.extensions["analytics_writer"](event)
    except Exception:
        app.logger.warning("analytics_write_failed event=%s", event["event_type"])
```

Initialize the production writer to open a fresh MySQL connection, call the appropriate insert, commit, rollback on failure, and always close. Tests replace the extension with `list.append`.

- [ ] **Step 4: Tag every terminal query outcome**

Map the existing route exits as follows:

- final recommendation JSON: `query_completed` with `success` or `degraded_stale_data`;
- Gemini unavailable but manual fallback response: `query_failed/failed_internal` only when consent exists;
- malformed input and validation exceptions: `query_failed/failed_validation`;
- unresolved address: `query_failed/failed_geocode`;
- no ranked candidates: `query_failed/failed_no_candidates`;
- `ParkingDataUnavailable`: `query_failed/failed_database`;
- unexpected exception: `query_failed/failed_internal`;
- location-choice intermediate response: no completed/failed event yet.

Every terminal JSON includes `request_id`; no event contains payload text.

- [ ] **Step 5: Test and implement the public event endpoint**

```python
def test_navigation_event_requires_consent_and_allowlisted_fields():
    response = client.post("/api/analytics/events", json={
        "event_type": "navigation_clicked",
        "analytics_id": VALID_UUID,
        "request_id": VALID_REQUEST_ID,
        "clicked_rank": 1,
        "parking_lot_id": "TPE001",
        "walking_minutes": 6.5,
        "availability_bucket": "11_plus",
        "source": "installed_pwa",
        "address": "不得接受",
    })
    assert response.status_code == 400
```

The endpoint accepts only `pwa_opened` and `navigation_clicked`, rejects unknown keys, limits JSON to fixed scalar fields, hashes the UUID before persistence, returns `204` for accepted events, and returns `204` without writing when analytics is disabled. Navigation inserts rely on Task 2's same-hash recent-query SQL.

- [ ] **Step 6: Run route and regression tests, then commit**

Run: `python -m pytest tests/test_analytics_routes.py tests/test_app_routes.py tests/test_app_errors.py -q`

Expected: PASS; existing query behavior unchanged.

```bash
git add app.py analytics_service.py tests/test_analytics_routes.py
git commit -m "feat: record consented parking query events"
```

---

### Task 4: Minimal opt-in UI and reliable navigation capture

**Files:**
- Modify: `templates/index.html`
- Modify: `static/app.js`
- Modify: `static/style.css`
- Modify: `static/sw.js`
- Modify: `tests/test_frontend_contract.py`
- Modify: `tests/test_pwa_contract.py`

**Interfaces:**
- Consumes: query `request_id` and `/api/analytics/events`.
- Produces: localStorage consent/UUID, query headers, `pwa_opened`, and first navigation click events.

- [ ] **Step 1: Write failing frontend contract tests**

```python
def test_opt_in_controls_and_privacy_link_exist():
    template = TEMPLATE.read_text(encoding="utf-8")
    script = SCRIPT.read_text(encoding="utf-8")
    assert 'id="analytics-consent"' in template
    assert 'id="analytics-accept"' in template
    assert 'id="analytics-decline"' in template
    assert "parking_analytics_consent" in script
    assert "crypto.randomUUID()" in script


def test_navigation_uses_beacon_with_keepalive_fallback():
    script = SCRIPT.read_text(encoding="utf-8")
    assert "navigator.sendBeacon" in script
    assert "keepalive:true" in script.replace(" ", "")
    assert "data-navigation-rank" in script
```

- [ ] **Step 2: Run frontend/PWA contracts and confirm RED**

Run: `python -m pytest tests/test_frontend_contract.py tests/test_pwa_contract.py -q`

Expected: new consent and navigation assertions fail.

- [ ] **Step 3: Add the non-blocking consent banner**

Add a compact bottom banner with this exact copy:

```html
<section id="analytics-consent" class="analytics-consent" hidden>
  <p>是否允許匿名使用分析？只記錄查詢是否成功、速度與導航點擊；不保存地址、對話、IP 或手機位置，90 天後刪除。</p>
  <button id="analytics-accept" type="button">允許匿名分析</button>
  <button id="analytics-decline" type="button">不要分析</button>
  <a href="#privacy-note">查看隱私說明</a>
</section>
```

Add a short `#privacy-note` in the footer and a button that reopens the choice. Do not change card layout or query forms.

- [ ] **Step 4: Implement consent and source helpers**

```javascript
const ANALYTICS_CONSENT_KEY = "parking_analytics_consent";
const ANALYTICS_ID_KEY = "parking_analytics_id";

function analyticsSource() {
  if (new URLSearchParams(location.search).get("src") === "share") return "shared";
  if (matchMedia("(display-mode: standalone)").matches) return "installed_pwa";
  if (!document.referrer || new URL(document.referrer).origin === location.origin) return "direct";
  return "unknown";
}

function analyticsHeaders() {
  if (localStorage.getItem(ANALYTICS_CONSENT_KEY) !== "accepted") return {};
  return {
    "X-Analytics-Consent": "1",
    "X-Analytics-Id": localStorage.getItem(ANALYTICS_ID_KEY),
    "X-Analytics-Source": analyticsSource(),
  };
}
```

Accept creates `crypto.randomUUID()` once; decline removes the UUID. Merge `analyticsHeaders()` into existing query headers without changing timeout behavior.

- [ ] **Step 5: Instrument rendered map links without inline JavaScript**

Store the latest successful `data.request_id` in `activeRequestId`. Add `data-navigation-rank`, lot ID, walking minutes, and availability bucket attributes to primary and compact Google Maps links. One delegated document click handler sends only the allowlisted scalar payload. Use `sendBeacon` first and `fetch(..., {keepalive:true})` only when Beacon returns false or is unavailable. Never delay or cancel the link click.

- [ ] **Step 6: Record PWA open once per page load after consent**

Call the same endpoint after DOMContentLoaded only when consent is accepted. `pwa_opened` contains `analytics_id`, `source`, and `event_type`; it contains no request ID or page URL.

- [ ] **Step 7: Bump the service-worker shell version and run checks**

Replace `navigation-v1` with `analytics-v1` in template asset query strings and `static/sw.js`. Update existing PWA contract assertions.

Run:

```bash
python -m pytest tests/test_frontend_contract.py tests/test_pwa_contract.py -q
node --check static/app.js
```

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add templates/index.html static/app.js static/style.css static/sw.js tests/test_frontend_contract.py tests/test_pwa_contract.py
git commit -m "feat: add opt-in parking analytics"
```

---

### Task 5: Deterministic dashboard metrics

**Files:**
- Modify: `analytics_service.py`
- Modify: `analytics_database.py`
- Create: `tests/test_analytics_metrics.py`

**Interfaces:**
- Consumes: events from `fetch_events` and `Asia/Taipei` date ranges.
- Produces: `parse_dashboard_range(value, now_utc) -> tuple[datetime, datetime]`, `summarize_events(rows, now_utc, min_devices=5) -> dict`.

- [ ] **Step 1: Write failing metric-definition tests**

```python
def test_summary_uses_locked_denominators_and_first_navigation_click():
    rows = metric_fixture_rows()
    summary = summarize_events(rows, NOW_UTC, min_devices=2)
    assert summary["completed_queries"] == 3
    assert summary["query_success_rate"] == 75.0
    assert summary["navigation_click_rate"] == 2 / 3 * 100
    assert summary["click_rank_counts"] == {"1": 1, "2": 1, "3": 0}
    assert summary["anonymous_query_devices"] == 3


def test_recent_navigation_window_is_marked_provisional():
    summary = summarize_events(recent_query_rows(), NOW_UTC)
    assert summary["navigation_provisional"] is True
```

- [ ] **Step 2: Run focused tests and confirm RED**

Run: `python -m pytest tests/test_analytics_metrics.py -q`

Expected: missing metric functions.

- [ ] **Step 3: Implement exact definitions from the approved spec**

Use distinct request IDs for completed/failed and first click. Count a repeat-use device only when query events occur on at least two different Taipei calendar dates in the latest 30 Taipei days. Use `statistics.median` and nearest-rank `ceil(0.95 * n) - 1` after sorting `duration_ms`. Hide district/place-type rows whose distinct querying devices are below `min_devices`.

- [ ] **Step 4: Add Taipei range and boundary tests**

```python
def test_today_range_uses_taipei_midnight_but_returns_utc():
    start, end = parse_dashboard_range("today", datetime(2026, 8, 23, 15, 30, tzinfo=timezone.utc))
    assert start == datetime(2026, 8, 22, 16, 0, tzinfo=timezone.utc)
    assert end == datetime(2026, 8, 23, 16, 0, tzinfo=timezone.utc)
```

- [ ] **Step 5: Run tests and commit**

Run: `python -m pytest tests/test_analytics_metrics.py tests/test_analytics_service.py tests/test_analytics_database.py -q`

Expected: PASS.

```bash
git add analytics_service.py analytics_database.py tests/test_analytics_metrics.py
git commit -m "feat: calculate parking analytics metrics"
```

---

### Task 6: Read-only admin dashboard and VM status

**Files:**
- Create: `status_service.py`
- Create: `templates/admin_analytics.html`
- Create: `static/admin_analytics.js`
- Create: `static/admin_analytics.css`
- Modify: `app.py`
- Create: `tests/test_admin_dashboard.py`
- Create: `tests/test_status_service.py`

**Interfaces:**
- Consumes: Task 5 metrics, existing MySQL connection and timestamps, Linux `/proc`/disk data.
- Produces: `GET /admin/analytics`, `GET /admin/api/analytics`, `GET /admin/api/status`.

- [ ] **Step 1: Write failing pure status tests**

```python
def test_status_thresholds_and_unknown_values_are_honest():
    assert classify_data_age(20)["tone"] == "green"
    assert classify_data_age(45)["tone"] == "yellow"
    assert classify_data_age(61)["tone"] == "red"
    assert classify_memory(None)["tone"] == "gray"
    assert classify_disk(9.9)["tone"] == "red"


def test_linux_status_parses_memory_and_uses_five_minute_load(tmp_path, monkeypatch):
    meminfo = tmp_path / "meminfo"
    meminfo.write_text("MemTotal: 1000 kB\nMemAvailable: 250 kB\n", encoding="utf-8")
    monkeypatch.setattr(os, "getloadavg", lambda: (0.1, 0.3, 0.4))
    status = read_linux_status(meminfo_path=meminfo, disk_path=tmp_path)
    assert status["memory_percent"] == 75.0
    assert status["load_5m"] == 0.3
```

- [ ] **Step 2: Implement status reads with no external requests**

Use `os.getloadavg`, `/proc/meminfo`, and `shutil.disk_usage`; catch unsupported OS/file errors and return `None`/gray. Keep module-level `APP_STARTED_AT`. Do not call systemd, Gemini, ORS, Nominatim, or Taipei APIs.

- [ ] **Step 3: Write failing admin route tests**

```python
def test_admin_pages_are_read_only_and_no_store(monkeypatch):
    client = make_admin_client(monkeypatch)
    page = client.get("/admin/analytics")
    data = client.get("/admin/api/analytics?range=7d")
    assert page.status_code == 200
    assert data.status_code == 200
    assert page.headers["Cache-Control"] == "no-store"
    assert data.headers["X-Robots-Tag"] == "noindex"
    assert client.post("/admin/api/status").status_code == 405


def test_status_api_degrades_each_component_independently(monkeypatch):
    body = make_admin_client(monkeypatch, database_error=True).get("/admin/api/status").get_json()
    assert body["application"]["tone"] == "green"
    assert body["database"]["tone"] == "red"
    assert body["official_data"]["tone"] == "gray"
```

- [ ] **Step 4: Implement owner routes**

`/admin/api/analytics` accepts only `today`, `7d`, `30d`; invalid values return 400. It opens one connection, fetches the bounded rows, closes it, and calls `summarize_events`. `/admin/api/status` measures `SELECT 1`, fetches snapshot/official/metadata times, reads local system values, and returns component objects with `label`, `value`, `tone`, and `detail`.

Add `Cache-Control: no-store` and `X-Robots-Tag: noindex` to all `/admin/` responses in the existing `after_request` hook.

- [ ] **Step 5: Build the small dashboard UI**

Use the approved order:

1. top system strip: app uptime, MySQL, official data age, Collector, metadata age, 5-minute load, memory, disk, deploy commit;
2. KPI cards: completed queries, success rate, navigation click rate, median/p95, 30-day repeat use;
3. diagnostic tables: click rank, districts/place types above sample threshold, fixed error/degraded codes.

The page fetches only on load and range-button clicks. Use textContent for data values; do not inject server values with `innerHTML`. Show loading, empty, partial, and error states.

- [ ] **Step 6: Run tests, syntax check, and commit**

```bash
python -m pytest tests/test_status_service.py tests/test_admin_dashboard.py -q
node --check static/admin_analytics.js
```

Expected: PASS.

```bash
git add status_service.py templates/admin_analytics.html static/admin_analytics.js static/admin_analytics.css app.py tests/test_status_service.py tests/test_admin_dashboard.py
git commit -m "feat: add owner analytics dashboard"
```

---

### Task 7: Cleanup command, Nginx protection, and deployment runbook

**Files:**
- Create: `analytics_cleanup.py`
- Create: `deploy/nginx-parking-radar-log-format.conf`
- Modify: `deploy/nginx-parking-radar.conf`
- Modify: `.env.example`
- Modify: `README.md`
- Create: `tests/test_deploy_analytics_contract.py`

**Interfaces:**
- Consumes: Task 2 cleanup and existing deployment layout `/opt/parking-hell`.
- Produces: one-shot cleanup CLI, Basic Auth coverage for every `/admin/` path, no-IP access logs, reproducible secret/cron setup.

- [ ] **Step 1: Write failing deployment contract tests**

```python
def test_nginx_protects_admin_and_does_not_log_ip():
    site = Path("deploy/nginx-parking-radar.conf").read_text(encoding="utf-8")
    logging = Path("deploy/nginx-parking-radar-log-format.conf").read_text(encoding="utf-8")
    assert "location /admin/" in site
    assert "auth_basic" in site and "auth_basic_user_file" in site
    assert "limit_req zone=parking_admin_login" in site
    assert "$remote_addr" not in logging
    assert "$http_x_forwarded_for" not in logging


def test_example_env_has_names_but_no_real_secrets():
    text = Path(".env.example").read_text(encoding="utf-8")
    assert "ANALYTICS_HMAC_SECRET=" in text
    assert "DEPLOY_VERSION=" in text
    assert "dev-only-change-me" not in text
```

- [ ] **Step 2: Implement and test the cleanup CLI**

```python
def main(now=None):
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=Config.ANALYTICS_RETENTION_DAYS)
    connection = get_connection()
    try:
        removed = delete_expired_events(connection, cutoff)
        connection.commit()
        return removed
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
```

Test commit, rollback, cutoff, and close with a fake connection.

- [ ] **Step 3: Add Nginx HTTP-level log/rate configuration**

`deploy/nginx-parking-radar-log-format.conf`:

```nginx
log_format parking_no_ip '$time_iso8601 "$request_method $uri $server_protocol" '
                         '$status $body_bytes_sent $request_time';
limit_req_zone $binary_remote_addr zone=parking_admin_login:1m rate=5r/m;
```

The server config uses `access_log /var/log/nginx/parking-radar.access.log parking_no_ip;` and adds a dedicated `/admin/` proxy location with Basic Auth, `limit_req`, existing forwarding headers, and `add_header Cache-Control "no-store" always`.

- [ ] **Step 4: Document exact VM setup and rollback**

README commands must include:

```bash
openssl rand -hex 32
sudo htpasswd -c /etc/nginx/.htpasswd-parking-radar admin
sudo install -m 644 deploy/nginx-parking-radar-log-format.conf /etc/nginx/conf.d/
sudo nginx -t && sudo systemctl reload nginx
```

Add the daily cron:

```cron
17 3 * * * cd /opt/parking-hell && /opt/parking-hell/.venv/bin/python analytics_cleanup.py >> /opt/parking-hell/analytics-cleanup.log 2>&1
```

Document rollback: restore prior Nginx files, remove only the analytics cleanup cron line, restart the previous app commit, and leave `analytics_events` in place unless the owner explicitly chooses to drop it.

- [ ] **Step 5: Run deployment contracts and commit**

Run: `python -m pytest tests/test_deploy_analytics_contract.py -q`

Expected: PASS.

```bash
git add analytics_cleanup.py deploy/nginx-parking-radar-log-format.conf deploy/nginx-parking-radar.conf .env.example README.md tests/test_deploy_analytics_contract.py
git commit -m "ops: protect and maintain analytics dashboard"
```

---

### Task 8: Whole-feature privacy, regression, and quality gate

**Files:**
- Modify only files implicated by failures found in this task.
- Create: `docs/QA_REVIEW_2026-08-23_ANALYTICS.md`

**Interfaces:**
- Consumes: complete branch from Tasks 1-7.
- Produces: verified branch, privacy scan evidence, manual acceptance checklist, no deployment.

- [ ] **Step 1: Run all offline automated checks**

```bash
python -m pytest -q
python -m compileall -q .
node --check static/app.js
node --check static/admin_analytics.js
```

Expected: all tests pass, compileall is silent, both Node checks exit 0.

- [ ] **Step 2: Run privacy and secret scans**

```bash
rg -n "address|destination|message|latitude|longitude|remote_addr|x_forwarded_for" analytics_service.py analytics_database.py status_service.py templates/admin_analytics.html static/admin_analytics.js deploy/nginx-parking-radar-log-format.conf
git grep -nE "ANALYTICS_HMAC_SECRET=.{8,}|auth_basic_user_file.*(Downloads|Users)"
```

Expected: matches are only explicit rejection/comments/bucket inputs; no persisted free-text columns, hard-coded secret, personal path, IP log token, or raw destination rendering in admin code.

- [ ] **Step 3: Verify analytics failure cannot break parking queries**

Add a regression test that makes `app.extensions["analytics_writer"]` raise, then asserts `/api/query` still returns the same 200 result and logs only `analytics_write_failed event=query_completed` without the input destination.

- [ ] **Step 4: Verify dashboard empty/partial states locally**

Run Flask with a test database or route fixtures and check:

- no analytics secret: public query works; status says analytics disabled;
- no events: dashboard shows zero/empty state;
- database failure: only DB/data components are red/gray;
- five-device threshold: four-device district is hidden, five-device district appears;
- expired events: cleanup deletes only rows older than 90 days;
- decline consent: no UUID and no analytics requests;
- navigation: Google Maps opens even when Beacon/fetch fails.

- [ ] **Step 5: Write the QA report**

Record exact commands, pass counts, any warnings, files reviewed, and the remaining live-only checks:

- apply migration on a backup/rollback-safe VM;
- create server-only HMAC secret and htpasswd;
- `nginx -t` before reload;
- verify 401 without credentials and 200 with credentials;
- verify cron and dashboard against live data;
- confirm no address/IP appears in the new logs.

- [ ] **Step 6: Request Superpowers two-stage review**

Use `superpowers:requesting-code-review` for spec compliance first, then code quality. Fix all Critical and Important findings, rerun affected tests, and record Minor deferrals in the QA report.

- [ ] **Step 7: Run final verification and commit**

Run the full Step 1 command set again from a clean shell and capture fresh output.

```bash
git add docs/QA_REVIEW_2026-08-23_ANALYTICS.md
git commit -m "test: verify analytics dashboard release"
```

Expected final state: all checks green, branch committed, no merge/push/deploy performed.

---

## Self-Review Result

- Spec coverage: consent, privacy fields, one event table, request/click linking, metrics, 90-day retention, dashboard, VM status, Basic Auth, no-IP logging, degradation and tests are each assigned to a task.
- Scope check: no member system, external analytics, monitoring platform, alerting, location permission, recommendation change, or deployment is included.
- Type consistency: `request_id` is a UUID string; `anonymous_id_hash` is a 64-character HMAC hex string; UTC datetimes flow from range parsing to parameterized DB reads; front-end enum values match server allowlists.
- Placeholder scan: no `TBD`, deferred implementation, or unspecified test step remains.


