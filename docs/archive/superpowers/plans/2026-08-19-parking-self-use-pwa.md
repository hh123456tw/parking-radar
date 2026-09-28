# Parking Radar Self-Use PWA Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade the existing Taipei parking radar into an installable PWA that shows the arrival-day type, applicable small-car hourly fee, confirmed daily cap, and parking facility type on every result without slowing the live query path.

**Architecture:** Keep the existing recommendation and Gemini boundaries unchanged. The collector stores raw official fare rules and deterministic official facility hints; local services interpret calendar and fee data during a query, while a scheduled metadata job enriches facility types from manual overrides and OpenStreetMap before users query. The current Flask API passes these fields to the existing vanilla JavaScript cards, and a service worker caches only the application shell.

**Tech Stack:** Python 3.11, Flask 3, PyMySQL, requests, pytest, MySQL 8, vanilla JavaScript, HTML/CSS, Web App Manifest, Service Worker, systemd, existing Leaflet and Chart.js.

**Spec:** `docs/superpowers/specs/2026-08-19-parking-self-use-pwa-design.md`

## Global Constraints

- Do not add login, membership, saved destinations, recent searches, LINE Bot, LIFF, native mobile apps, push notifications, background location, paid map APIs, reservations, payments, or AI vacancy prediction.
- Do not change the current recommendation rules, 1,500-metre radius, decision thresholds, Gemini intent-only boundary, history-on-demand behavior, or Google Maps navigation behavior.
- Only identify an hourly fee from small-car `ParkingType` values `C` or `CM` and `RateType=1`; uncertain or conflicting rules must be shown as a range or `官方未標示`.
- Only identify a daily cap when the small-car text explicitly says `當日最高`, `每日最高`, `24 小時最高`, or `上限`; motorcycle, monthly, and per-entry prices are never daily caps.
- Facility-source priority is `manual > official > osm > unknown`; underground and multi-storey must never be inferred as mechanical.
- Calendar and Overpass downloads run only from explicit maintenance commands, never from `/api/query`.
- `/api/*`, OpenStreetMap tiles, and Google Maps links are never cached by the service worker.
- Every new Python function and every non-obvious JavaScript, CSS, SQL, or service-worker rule receives a concise Traditional Chinese comment.
- Existing user changes in the dirty worktree must be preserved; each task stages only the files listed in that task.
- All existing tests must remain green, and all tests use fixtures or fakes rather than real external APIs.

---

## File Structure

- Create `fee_service.py`: parse official `FareInfo` and conservative `payex` fallbacks into display-only small-car hourly fee and daily-cap fields.
- Create `calendar_service.py`: download annual TaiwanCalendar files outside the request path and classify an arrival datetime from local JSON.
- Create `parking_metadata.py`: classify explicit official text, apply manual overrides, batch-match OSM parking features, and provide the monthly sync CLI.
- Create `data/parking_overrides.json`: reviewed per-lot facility-type corrections; starts as an empty JSON object.
- Create `migrations/20260819_add_parking_metadata.sql`: repeatable migration for raw fare and facility metadata columns.
- Create `static/manifest.webmanifest`, `static/sw.js`, and `static/icons/*`: installable PWA metadata, shell caching, and app icons.
- Create `deploy/parking-metadata-refresh.service` and `deploy/parking-metadata-refresh.timer`: scheduled calendar and OSM refresh outside Gunicorn.
- Modify `collector.py`: retain `FareInfo`, derive only explicit official facility types, and leave higher-priority metadata intact.
- Modify `database.py`: read/write the new columns and expose batch metadata update helpers.
- Modify `schema.sql`: include new columns for clean installations.
- Modify `app.py`: enrich candidate JSON from local calendar, fare, and facility data.
- Modify `static/app.js`: render concise fee, cap, day-type, and facility-type rows on primary and compact results; register the service worker.
- Modify `static/style.css`: style the new information hierarchy and PWA install hint without enlarging the result cards excessively.
- Modify `templates/index.html`: add manifest, theme, Apple icon, and install-help hooks.
- Modify `README.md`: document data attribution, migration, maintenance, PWA installation, failure degradation, and deployment verification.
- Modify existing tests and create `tests/test_calendar_service.py`, `tests/test_fee_service.py`, `tests/test_parking_metadata.py`, and `tests/test_pwa_contract.py`.

---

### Task 1: Persist Raw Fare Rules Safely

**Files:**
- Create: `migrations/20260819_add_parking_metadata.sql`
- Modify: `schema.sql`
- Modify: `collector.py`
- Modify: `database.py`
- Modify: `tests/fixtures/taipei_static.json`
- Modify: `tests/test_collector_parsing.py`
- Modify: `tests/test_database_collector.py`

**Interfaces:**
- Consumes: official static parking objects containing optional `FareInfo`.
- Produces: `parse_static(...)` rows with `fare_rules_json: str | None`.
- Produces: `parking_lots.fare_rules_json`, `facility_type`, `facility_source`, and `metadata_checked_at`, all nullable.
- Preserves: `upsert_parking_lots(connection, lots) -> int` and all existing columns.

- [ ] **Step 1: Add a structured fare fixture and failing collector test**

Add this field to the first lot in `tests/fixtures/taipei_static.json`:

```json
"FareInfo":{"FareRule":[{"ParkingType":"C","RateType":"1","ChargeableSTime":"0800","ChargeableETime":"2200","ParkingRates":"60"}]}
```

Add to `tests/test_collector_parsing.py`:

```python
def test_static_parser_preserves_raw_fare_rules_as_utf8_json():
    """原始 FareInfo 必須完整保存，中文不得被 ASCII 跳脫。"""
    lot = collector.parse_static(
        load_fixture("taipei_static.json"), {"TPE0001"})[0]

    assert json.loads(lot["fare_rules_json"])["FareRule"][0] == {
        "ParkingType": "C", "RateType": "1",
        "ChargeableSTime": "0800", "ChargeableETime": "2200",
        "ParkingRates": "60",
    }
    assert "\\u" not in lot["fare_rules_json"]
```

- [ ] **Step 2: Run the collector test and verify the missing field failure**

Run: `python -m pytest tests/test_collector_parsing.py::test_static_parser_preserves_raw_fare_rules_as_utf8_json -v`

Expected: FAIL with `KeyError: 'fare_rules_json'`.

- [ ] **Step 3: Serialize `FareInfo` in the collector**

Import `json` and add this key inside each `parse_static` row:

```python
"fare_rules_json": (
    json.dumps(raw["FareInfo"], ensure_ascii=False, separators=(",", ":"))
    if raw.get("FareInfo") else None
),
```

- [ ] **Step 4: Extend schema and add a repeatable migration**

Add these nullable columns after `fee_info` in `schema.sql`:

```sql
fare_rules_json LONGTEXT NULL,
facility_type VARCHAR(20) NULL,
facility_source VARCHAR(20) NULL,
metadata_checked_at DATETIME NULL,
```

Create `migrations/20260819_add_parking_metadata.sql` with these four explicit checks; this is safe to run repeatedly:

```sql
SET @ddl = IF(
  (SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS
   WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'parking_lots'
     AND COLUMN_NAME = 'fare_rules_json') = 0,
  'ALTER TABLE parking_lots ADD COLUMN fare_rules_json LONGTEXT NULL AFTER fee_info',
  'SELECT 1');
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @ddl = IF(
  (SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS
   WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'parking_lots'
     AND COLUMN_NAME = 'facility_type') = 0,
  'ALTER TABLE parking_lots ADD COLUMN facility_type VARCHAR(20) NULL AFTER fare_rules_json',
  'SELECT 1');
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @ddl = IF(
  (SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS
   WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'parking_lots'
     AND COLUMN_NAME = 'facility_source') = 0,
  'ALTER TABLE parking_lots ADD COLUMN facility_source VARCHAR(20) NULL AFTER facility_type',
  'SELECT 1');
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @ddl = IF(
  (SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS
   WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'parking_lots'
     AND COLUMN_NAME = 'metadata_checked_at') = 0,
  'ALTER TABLE parking_lots ADD COLUMN metadata_checked_at DATETIME NULL AFTER facility_source',
  'SELECT 1');
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;
```

- [ ] **Step 5: Add the failing parameter-binding test**

Extend `sample_lot()` in `tests/test_database_collector.py` with:

```python
"fare_rules_json": '{"FareRule":[]}',
"facility_type": None,
"facility_source": None,
"metadata_checked_at": None,
```

Extend `test_upsert_parking_lots_binds_complete_row_as_parameters` to assert that the first bound tuple contains `'{"FareRule":[]}'` and that the INSERT SQL names `fare_rules_json`, `facility_type`, `facility_source`, and `metadata_checked_at`.

- [ ] **Step 6: Update the parameterized upsert**

Add the four fields to the INSERT column list, placeholders, `keys`, and duplicate-key update. For this task, `fare_rules_json` is updated from official data; nullable facility fields are accepted but remain unset until Task 4.

- [ ] **Step 7: Run persistence tests**

Run: `python -m pytest tests/test_collector_parsing.py tests/test_database_collector.py -v`

Expected: PASS.

- [ ] **Step 8: Commit only persistence files**

```bash
git add migrations/20260819_add_parking_metadata.sql schema.sql collector.py database.py tests/fixtures/taipei_static.json tests/test_collector_parsing.py tests/test_database_collector.py
git commit -m "feat: retain official parking fare rules"
```

---

### Task 2: Classify Arrival Days from a Local Calendar

**Files:**
- Create: `calendar_service.py`
- Create: `tests/test_calendar_service.py`
- Modify: `.gitignore`

**Interfaces:**
- Produces: `sync_calendars(years: list[int] | None = None, calendar_dir: Path = CALENDAR_DIR, timeout: int = 10) -> list[Path]`.
- Produces: `classify_arrival_day(arrival_time: datetime, calendar_dir: Path = CALENDAR_DIR) -> dict`.
- Classification dict keys: `kind` (`weekday`, `weekend`, `holiday`, `makeup_workday`), `label`, `is_holiday: bool`, and `source` (`taiwan_calendar` or `weekday_fallback`).
- Requires: timezone-aware `arrival_time`; raises `ValueError("抵達時間必須包含時區")` for naive input.

- [ ] **Step 1: Write failing day-classification tests**

Create `tests/test_calendar_service.py` using `tmp_path` and a local `2026.json` fixture:

```python
import json
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from calendar_service import classify_arrival_day

TAIPEI = ZoneInfo("Asia/Taipei")


def write_calendar(tmp_path):
    rows = [
        {"date": "20261010", "week": "六", "isHoliday": True,
         "description": "國慶日"},
        {"date": "20260926", "week": "六", "isHoliday": False,
         "description": "補行上班日"},
        {"date": "20260823", "week": "日", "isHoliday": True,
         "description": ""},
    ]
    (tmp_path / "2026.json").write_text(
        json.dumps(rows, ensure_ascii=False), encoding="utf-8")


@pytest.mark.parametrize(("date", "kind", "label"), [
    ("2026-10-10T18:00:00+08:00", "holiday", "國定假日｜國慶日"),
    ("2026-09-26T18:00:00+08:00", "makeup_workday", "補班日"),
    ("2026-08-23T18:00:00+08:00", "weekend", "週末"),
    ("2026-08-24T18:00:00+08:00", "weekday", "平日"),
])
def test_classify_arrival_day_from_local_calendar(tmp_path, date, kind, label):
    write_calendar(tmp_path)

    result = classify_arrival_day(datetime.fromisoformat(date), tmp_path)

    assert (result["kind"], result["label"]) == (kind, label)
    assert result["source"] == "taiwan_calendar"
```

Add tests for a missing annual file falling back to weekday/weekend and for naive datetimes raising the exact `ValueError`.

- [ ] **Step 2: Run tests and verify import failure**

Run: `python -m pytest tests/test_calendar_service.py -v`

Expected: FAIL because `calendar_service` does not exist.

- [ ] **Step 3: Implement local classification**

In `calendar_service.py`, define `CALENDAR_DIR = Path("data/calendar")`, load only `{year}.json`, normalize `arrival_time` to `Asia/Taipei`, index the source's `YYYYMMDD` rows, look up with `local.strftime("%Y%m%d")`, and apply these exact rules in order:

```python
if row and row["isHoliday"] and row.get("description"):
    return day("holiday", f"國定假日｜{row['description']}", True, "taiwan_calendar")
if row and row["isHoliday"]:
    return day("weekend", "週末", True, "taiwan_calendar")
if row and local.weekday() == 5 and not row["isHoliday"]:
    return day("makeup_workday", "補班日", False, "taiwan_calendar")
return day("weekday", "平日", False, "taiwan_calendar")
```

When the file or date row is unavailable, classify Saturday/Sunday as `週末`, other days as `平日`, and set `source="weekday_fallback"`.

- [ ] **Step 4: Add a failing sync test**

Use a fake response object and monkeypatch `requests.get`. Assert that `sync_calendars([2026], tmp_path, timeout=7)` requests exactly `https://cdn.jsdelivr.net/gh/ruyut/TaiwanCalendar/data/2026.json`, passes timeout 7, calls `raise_for_status()`, validates the JSON list, and atomically replaces `2026.json` through a sibling temporary file.

- [ ] **Step 5: Implement explicit calendar sync**

Implement `sync_calendars`; when `years` is omitted use the current Taipei year and next year. Write UTF-8 JSON to `2026.json.tmp`, then call `Path.replace`. Add an argparse `--sync` entry point that prints the paths written. Do not call this function during module import.

- [ ] **Step 6: Ignore downloaded annual files but retain the directory**

Add `data/calendar/*.json` and `data/calendar/*.tmp` to `.gitignore`. The application creates the directory when sync runs, so no tracked placeholder is needed.

- [ ] **Step 7: Run calendar tests**

Run: `python -m pytest tests/test_calendar_service.py -v`

Expected: PASS with no network access.

- [ ] **Step 8: Commit the calendar service**

```bash
git add calendar_service.py tests/test_calendar_service.py .gitignore
git commit -m "feat: classify Taiwan arrival days locally"
```

---

### Task 3: Interpret Small-Car Fees Conservatively

**Files:**
- Create: `fee_service.py`
- Create: `tests/test_fee_service.py`

**Interfaces:**
- Produces: `build_fee_summary(fare_rules_json: str | None, fee_info: str | None, arrival_time: datetime, day_kind: str) -> dict`.
- Result keys: `hourly_fee_label`, `daily_cap_label`, `fee_note`, and `fee_confidence` (`exact`, `range`, `unknown`).
- Returns display strings only; it never changes recommendation ranking.

- [ ] **Step 1: Write failing structured-rule tests**

Create `tests/test_fee_service.py`:

```python
import json
from datetime import datetime

from fee_service import build_fee_summary


def rules(*rows):
    return json.dumps({"FareRule": list(rows)}, ensure_ascii=False)


def test_selects_small_car_hourly_rule_for_arrival_time():
    result = build_fee_summary(rules(
        {"ParkingType": "C", "RateType": "1", "ChargeableSTime": "0800",
         "ChargeableETime": "2200", "ParkingRates": "60"},
        {"ParkingType": "M", "RateType": "1", "ChargeableSTime": "0000",
         "ChargeableETime": "2400", "ParkingRates": "20"},
    ), "", datetime.fromisoformat("2026-08-19T18:00:00+08:00"), "weekday")

    assert result == {
        "hourly_fee_label": "60 元／時",
        "daily_cap_label": "官方未標示",
        "fee_note": None,
        "fee_confidence": "exact",
    }
```

Add focused tests for `CM`, cross-midnight `2200`–`0800`, excluding `RateType` 2 and 3, malformed JSON, and multiple applicable rates returning `40～60 元／時` with `fee_confidence="range"`.

- [ ] **Step 2: Run tests and verify import failure**

Run: `python -m pytest tests/test_fee_service.py -v`

Expected: FAIL because `fee_service` does not exist.

- [ ] **Step 3: Implement structured hourly-rule selection**

Implement private helpers that normalize `FareRule` to a list, accept only `ParkingType in {"C", "CM"}` and `RateType == "1"`, parse four-digit times, support a `2400` end marker and cross-midnight ranges, and extract numeric `ParkingRates`. Deduplicate prices before returning an exact value or range. Invalid data returns the unknown result rather than raising.

- [ ] **Step 4: Add failing conservative text tests**

Add tests with these exact expectations:

```python
def test_extracts_small_car_hourly_fee_and_cap_from_text():
    result = build_fee_summary(
        None, "小型車每小時 40 元，當日最高 240 元；機車每次 20 元",
        datetime.fromisoformat("2026-08-19T18:00:00+08:00"), "weekday")
    assert result["hourly_fee_label"] == "40 元／時"
    assert result["daily_cap_label"] == "240 元"


def test_does_not_use_motorcycle_or_monthly_numbers_as_daily_cap():
    result = build_fee_summary(
        None, "小型車每小時 30 元，月租 3000 元；機車每日最高 50 元",
        datetime.fromisoformat("2026-08-19T18:00:00+08:00"), "weekday")
    assert result["daily_cap_label"] == "官方未標示"
```

Also test that text mentioning different weekday/holiday or event prices returns a range and `fee_note="依日期、活動或現場公告"` instead of guessing.

- [ ] **Step 5: Implement text fallback and daily-cap extraction**

Split the official text into a small-car segment that ends before explicit motorcycle headings. Match only hourly phrases adjacent to `小時`, `時`, or `每半小時` after normalizing half-hour prices to an hourly display value. Match a cap only when one of the approved cap phrases appears in the same small-car segment. If structured hourly rules are valid, use them for the hourly label and use text only for cap and ambiguity notes.

- [ ] **Step 6: Run fee tests**

Run: `python -m pytest tests/test_fee_service.py -v`

Expected: PASS.

- [ ] **Step 7: Commit the fee interpreter**

```bash
git add fee_service.py tests/test_fee_service.py
git commit -m "feat: explain applicable small-car fees"
```

---

### Task 4: Enrich Facility Type without Query-Time Network Calls

**Files:**
- Create: `parking_metadata.py`
- Create: `data/parking_overrides.json`
- Create: `tests/test_parking_metadata.py`
- Modify: `collector.py`
- Modify: `database.py`
- Modify: `tests/test_collector_parsing.py`
- Modify: `tests/test_database_collector.py`

**Interfaces:**
- Produces: `infer_official_facility_type(name: str, summary: str) -> tuple[str, str]` returning `(facility_type, facility_source)`.
- Produces: `match_osm_facilities(lots: list[dict], elements: list[dict], max_distance_m: float = 40) -> dict[str, str]`.
- Produces: `sync_parking_metadata(connection, overrides_path: Path = OVERRIDES_PATH, timeout: int = 15) -> dict`.
- Produces database helpers `fetch_parking_metadata_candidates(connection) -> list[dict]` and `update_parking_metadata(connection, updates: list[dict]) -> int`.
- Facility values are limited to `mechanical`, `surface`, `underground`, `multi_storey`, `mixed`, and `unknown`.

- [ ] **Step 1: Write failing official-text and priority tests**

Create `tests/test_parking_metadata.py`:

```python
import pytest

from parking_metadata import infer_official_facility_type


@pytest.mark.parametrize(("text", "expected"), [
    ("忠孝機械停車場", ("mechanical", "official")),
    ("市民大道地下停車場", ("underground", "official")),
    ("河濱平面停車場", ("surface", "official")),
    ("公有立體停車場", ("multi_storey", "official")),
    ("一般停車場", ("unknown", "unknown")),
])
def test_infer_official_facility_type_uses_only_explicit_words(text, expected):
    assert infer_official_facility_type(text, "") == expected


def test_underground_is_not_inferred_as_mechanical():
    assert infer_official_facility_type("地下停車場", "") == (
        "underground", "official")
```

Add tests proving manual overrides beat official and OSM, official beats OSM, and an unknown lot may accept OSM.

- [ ] **Step 2: Run tests and verify import failure**

Run: `python -m pytest tests/test_parking_metadata.py -v`

Expected: FAIL because `parking_metadata` does not exist.

- [ ] **Step 3: Implement explicit official classification**

Map only the literal words `機械`, `平面`, `地下`, and `立體`; when two distinct types are explicitly present return `mixed`. Return `unknown` for descriptions without those words. Add `facility_type` and `facility_source` to `collector.parse_static` by calling this pure function with `raw.get("name", "")` and `raw.get("summary", "")`.

- [ ] **Step 4: Preserve metadata priority in the upsert**

Add failing SQL assertions, then update duplicate-key SQL so:

- existing `manual` is always preserved;
- incoming `official` replaces existing `osm` or `unknown`;
- existing `official` is preserved when incoming data is `unknown`;
- existing `osm` is preserved when incoming data is `unknown`.

Use fixed SQL `CASE` expressions; do not construct SQL from facility values.

- [ ] **Step 5: Write failing OSM matching tests**

Create lots and OSM elements with fixed coordinates. Test: one `parking=surface` feature within 40 metres matches; a 41-metre feature does not; two supported features within 40 metres are ambiguous and do not match; unsupported `parking=street_side` does not match.

- [ ] **Step 6: Implement OSM matching and batch fetch**

Use the existing `analysis.haversine_m`. Map only `surface`, `underground`, and `multi-storey`. Fetch one Overpass query for the Taipei bounding box and `amenity=parking`, with an explicit timeout and identifiable `User-Agent`. Convert node coordinates and way/relation `center` coordinates into one element list. Matching remains a pure function and never runs from Flask routes.

- [ ] **Step 7: Implement manual overrides and transactional sync**

Create `data/parking_overrides.json` containing `{}`. Validate every override key as a string lot ID and every value as an allowed facility type. `sync_parking_metadata` loads current lots, applies manual overrides first, keeps official values second, adds only unambiguous OSM matches to unknown lots, writes all updates with `metadata_checked_at=datetime.now(timezone.utc)`, commits once, rolls back on failure, and returns counts for `manual`, `official`, `osm`, and `unknown`.

- [ ] **Step 8: Add database helper tests and implementation**

Verify `fetch_parking_metadata_candidates` selects `lot_id`, `lot_name`, `latitude`, `longitude`, `facility_type`, and `facility_source`. Verify `update_parking_metadata` uses one parameterized `executemany` and never interpolates lot IDs. Implement both helpers accordingly.

- [ ] **Step 9: Add the explicit sync CLI and run tests**

Add `python parking_metadata.py --sync`; it opens one database connection, invokes `sync_parking_metadata`, closes the connection in `finally`, and prints the count dictionary.

Run: `python -m pytest tests/test_parking_metadata.py tests/test_collector_parsing.py tests/test_database_collector.py -v`

Expected: PASS without real Overpass requests.

- [ ] **Step 10: Commit metadata enrichment**

```bash
git add parking_metadata.py data/parking_overrides.json collector.py database.py tests/test_parking_metadata.py tests/test_collector_parsing.py tests/test_database_collector.py
git commit -m "feat: enrich parking facility types"
```

---

### Task 5: Add Day, Fee, and Facility Fields to the API

**Files:**
- Modify: `app.py`
- Modify: `tests/test_app_routes.py`
- Modify: `tests/test_app_errors.py`

**Interfaces:**
- Consumes: `classify_arrival_day(arrival_time) -> dict` and `build_fee_summary(fare_rules_json, fee_info, arrival_time, day_kind) -> dict`.
- Produces candidate JSON keys: `arrival_day_label`, `calendar_source`, `hourly_fee_label`, `daily_cap_label`, `fee_note`, `fee_confidence`, `facility_type`, `facility_type_label`, and `facility_source`.
- Preserves every current response key and grouping.

- [ ] **Step 1: Add a failing candidate-enrichment test**

Extend `lot_row()` with raw fare and facility fields. Add:

```python
def test_query_enriches_every_result_with_local_decision_metadata(monkeypatch):
    client = make_client()
    monkeypatch.setattr(app_module, "classify_arrival_day", lambda _arrival: {
        "kind": "holiday", "label": "國定假日｜國慶日",
        "is_holiday": True, "source": "taiwan_calendar",
    })
    monkeypatch.setattr(app_module, "build_fee_summary", lambda *_args: {
        "hourly_fee_label": "60 元／時", "daily_cap_label": "230 元",
        "fee_note": None, "fee_confidence": "exact",
    })
    # Reuse the route's existing database, geocoder, and ranking fakes.
    response = client.post("/api/query", json={
        "mode": "manual", "district": "中正區",
        "arrival_time": "2026-10-10T18:00:00+08:00",
    })
    lot = response.get_json()["recommendations"][0]
    assert lot["arrival_day_label"] == "國定假日｜國慶日"
    assert lot["hourly_fee_label"] == "60 元／時"
    assert lot["daily_cap_label"] == "230 元"
    assert lot["facility_type_label"] == "地下停車場"
```

Implement this test using the existing fixture helpers rather than opening MySQL.

- [ ] **Step 2: Run the focused route test and verify missing fields**

Run: `python -m pytest tests/test_app_routes.py::test_query_enriches_every_result_with_local_decision_metadata -v`

Expected: FAIL because the new fields are absent.

- [ ] **Step 3: Implement one pure candidate enrichment helper**

Add:

```python
FACILITY_LABELS = {
    "mechanical": "機械式", "surface": "平面式",
    "underground": "地下停車場", "multi_storey": "立體停車場",
    "mixed": "混合型", "unknown": "型態待確認",
}


def enrich_candidate_metadata(row, arrival_time, day_info):
    """以本機資料補上費率、抵達日與場站型態，不修改推薦分數。"""
    row.update(build_fee_summary(
        row.get("fare_rules_json"), row.get("fee_info"),
        arrival_time, day_info["kind"]))
    facility_type = row.get("facility_type") or "unknown"
    row.update(
        arrival_day_label=day_info["label"],
        calendar_source=day_info["source"],
        facility_type=facility_type,
        facility_type_label=FACILITY_LABELS.get(facility_type, "型態待確認"),
        facility_source=row.get("facility_source") or "unknown",
    )
    return row
```

Call `classify_arrival_day` once per request after validation, then enrich all ranked rows before `split_recommendation_groups`. Add all new safe fields to `public_candidate`.

- [ ] **Step 4: Add degradation tests**

Test a missing calendar file returns weekday fallback fields, malformed `fare_rules_json` returns `官方未標示`, and null facility metadata returns `型態待確認`. Also monkeypatch `requests.get` to raise if called during `/api/query`, proving the request path uses no calendar or OSM network.

- [ ] **Step 5: Run route and error tests**

Run: `python -m pytest tests/test_app_routes.py tests/test_app_errors.py -v`

Expected: PASS with existing API behavior unchanged.

- [ ] **Step 6: Commit API enrichment**

```bash
git add app.py tests/test_app_routes.py tests/test_app_errors.py
git commit -m "feat: expose parking fee and facility details"
```

---

### Task 6: Present Decision Metadata on Every Result

**Files:**
- Modify: `static/app.js`
- Modify: `static/style.css`
- Modify: `templates/index.html`
- Modify: `tests/test_frontend_contract.py`

**Interfaces:**
- Consumes: candidate display fields from Task 5.
- Produces: primary-card metadata row and compact-lot metadata row.
- Preserves: Google Maps links, result groups, address, capacity, reasons, history action, map markers, and responsive one-column mobile behavior.

- [ ] **Step 1: Add failing frontend contract assertions**

Add a new test that slices both `primaryCard` and `compactLot` and asserts both contain:

```python
for field in (
    "lot.arrival_day_label", "lot.hourly_fee_label",
    "lot.daily_cap_label", "lot.facility_type_label",
):
    assert field in primary_card
    assert field in compact_lot
```

Also assert all inserted values pass through `escapeHtml`, the primary card keeps the full official fee details, and the compact result does not gain another `<details>` element.

- [ ] **Step 2: Run the contract test and verify failure**

Run: `python -m pytest tests/test_frontend_contract.py -v`

Expected: FAIL because the new fields are not rendered.

- [ ] **Step 3: Add small display helpers**

Add `displayValue(value, fallback="官方未標示")` that trims and escapes strings, and `feeMetaLine(lot)` that returns exactly this semantic structure:

```html
<div class="decision-meta">
  <span>抵達：國定假日｜國慶日</span>
  <span>60 元／時</span>
  <span>上限 230 元</span>
  <span>地下停車場</span>
</div>
```

When the cap is unknown display `上限官方未標示`; when facility type is unknown display `型態待確認`. If `fee_note` is present, append a separate escaped `.fee-note` such as `依日期、活動或現場公告` in both primary and compact results. Do not derive or recalculate any fee in JavaScript.

- [ ] **Step 4: Render full and compact variants**

Insert `feeMetaLine(lot)` below capacity on primary cards. Add one `.compact-meta` line containing day, hourly fee, cap, and facility type below each compact lot name while keeping distance and Google Maps action visible.

- [ ] **Step 5: Style hierarchy and responsive behavior**

Use small pill-like metadata with existing green/yellow/red card borders, `display:flex`, `flex-wrap:wrap`, and a minimum 14px mobile font. Keep official raw fee text inside the current scrollable details. Do not add a new panel or page section.

- [ ] **Step 6: Update the copy and static version**

Change the hero description to mention `費率與場站型態`, and update the `v=` query string for `style.css` and `app.js` to `self-use-v1` so deployed browsers receive the new assets.

- [ ] **Step 7: Verify frontend syntax and contract**

Run: `node --check static/app.js`

Run: `python -m pytest tests/test_frontend_contract.py -v`

Expected: both PASS.

- [ ] **Step 8: Commit result presentation**

```bash
git add static/app.js static/style.css templates/index.html tests/test_frontend_contract.py
git commit -m "feat: show fee and facility details on cards"
```

---

### Task 7: Make the Existing Site Installable as a PWA

**Files:**
- Create: `static/manifest.webmanifest`
- Create: `static/sw.js`
- Create: `static/icons/icon-192.png`
- Create: `static/icons/icon-512.png`
- Create: `static/icons/icon-maskable-512.png`
- Create: `tests/test_pwa_contract.py`
- Modify: `templates/index.html`
- Modify: `static/app.js`
- Modify: `static/style.css`

**Interfaces:**
- Produces: manifest `name="停車地獄雷達"`, `display="standalone"`, `start_url="/"`, and three icon entries.
- Produces: service-worker cache `parking-radar-shell-v1`.
- Preserves: all API responses as network-only and all external map assets outside application caches.

- [ ] **Step 1: Write failing manifest and service-worker contract tests**

Create `tests/test_pwa_contract.py` that loads the manifest as JSON and asserts name, short name, start URL, standalone display, theme/background colors, 192 and 512 icons, and one icon with `purpose="maskable"`. Read `static/sw.js` and assert `/api/`, `tile.openstreetmap.org`, and `google.com/maps` are excluded before cache lookup. Assert `templates/index.html` contains manifest, `theme-color`, and `apple-touch-icon` links.

- [ ] **Step 2: Run PWA contract tests and verify missing-file failure**

Run: `python -m pytest tests/test_pwa_contract.py -v`

Expected: FAIL because the manifest and service worker do not exist.

- [ ] **Step 3: Create the manifest and app icons**

Create a simple radar-style icon using the existing dark background `#0b1118`, green ring `#36c98f`, and orange location pin `#ff8a3d`. Export square PNGs at 192×192 and 512×512; keep all maskable artwork inside the central 80% safe zone. Reference them with exact sizes and `image/png` MIME types in the manifest.

- [ ] **Step 4: Implement shell-only caching**

In `static/sw.js`, precache `/`, the versioned local stylesheet, script, manifest, and icons. For navigation use network-first with the cached `/` fallback. For same-origin static assets use cache-first. Return `fetch(request)` immediately for `/api/`, non-GET requests, and cross-origin requests. In `activate`, delete every cache whose name differs from `parking-radar-shell-v1`.

- [ ] **Step 5: Add registration and install guidance**

Register `/static/sw.js` after `DOMContentLoaded` only when `serviceWorker` exists. Capture `beforeinstallprompt`, reveal a small `安裝到手機` button, and call `prompt()` only after the user clicks. For iOS standalone-capable Safari where that event is unavailable, show the text `在 Safari 點分享，再選「加入主畫面」`. Do not add favorites or recent destinations.

- [ ] **Step 6: Run PWA and JavaScript checks**

Run: `python -m pytest tests/test_pwa_contract.py tests/test_frontend_contract.py -v`

Run: `node --check static/app.js`

Run: `node --check static/sw.js`

Expected: all PASS.

- [ ] **Step 7: Commit PWA assets**

```bash
git add static/manifest.webmanifest static/sw.js static/icons templates/index.html static/app.js static/style.css tests/test_pwa_contract.py
git commit -m "feat: make parking radar installable"
```

---

### Task 8: Schedule Enrichment, Document Deployment, and Verify the Whole System

**Files:**
- Create: `deploy/parking-metadata-refresh.service`
- Create: `deploy/parking-metadata-refresh.timer`
- Modify: `README.md`
- Modify: `CHANGELOG.md`

**Interfaces:**
- Produces: a monthly oneshot maintenance job that runs calendar sync before parking metadata sync.
- Preserves: the existing 15-minute collector schedule and one-worker Gunicorn service.

- [ ] **Step 1: Create the maintenance service and timer**

Use `Type=oneshot`, `User=parking`, `WorkingDirectory=/opt/parking-hell`, and the existing environment file. Define two ordered commands:

```ini
ExecStart=/opt/parking-hell/.venv/bin/python calendar_service.py --sync
ExecStart=/opt/parking-hell/.venv/bin/python parking_metadata.py --sync
```

Set the timer to `OnCalendar=monthly`, `Persistent=true`, and `RandomizedDelaySec=1h`. The timer failure must not restart or stop `parking-radar.service`.

- [ ] **Step 2: Update operating instructions**

Document these exact deployment steps in `README.md`:

1. Back up `parking_lots`.
2. Run `mysql ... < migrations/20260819_add_parking_metadata.sql`.
3. Run `python collector.py --once`.
4. Run `python calendar_service.py --sync`.
5. Run `python parking_metadata.py --sync`.
6. Install and enable the maintenance timer.
7. Restart Gunicorn and verify `/health`.
8. Query 台北車站 and inspect primary, compact, map, and history behavior.
9. Install the PWA from Android Chrome and iOS Safari.

Also document that government data is commercially reusable subject to attribution, that OSM attribution remains visible, that unknown values are not guarantees, and that no account or personal destination history is stored.

- [ ] **Step 3: Record the user-visible change**

Add one dated `CHANGELOG.md` section covering arrival-day classification, structured hourly fees, conservative caps, facility source priority, PWA installation, and graceful degradation. Do not describe login, saved destinations, or prediction as implemented.

- [ ] **Step 4: Run the complete offline verification suite**

Run: `python -m pytest -q`

Run: `python -m compileall app.py collector.py database.py calendar_service.py fee_service.py parking_metadata.py`

Run: `node --check static/app.js`

Run: `node --check static/sw.js`

Run: `git diff --check`

Expected: all tests PASS, compilation and JavaScript checks exit 0, and no whitespace errors.

- [ ] **Step 5: Perform local browser acceptance**

With fixture-backed or local MySQL data, verify:

1. 台北車站 returns the same recommendation ordering as before the feature.
2. All primary and compact results show day type, hourly fee, cap, and facility type.
3. Unknown and ambiguous data use explicit fallback labels rather than invented values.
4. Google Maps, location choices, manual mode, chat mode, map markers, and seven-day history still work.
5. DevTools Application shows standalone manifest and an active service worker.
6. DevTools Network shows `/api/query` responses coming from the network, never the service-worker cache.
7. Simulated calendar and Overpass failures do not break parking recommendations.

- [ ] **Step 6: Commit operations and documentation**

```bash
git add deploy/parking-metadata-refresh.service deploy/parking-metadata-refresh.timer README.md CHANGELOG.md
git commit -m "docs: deploy parking metadata and PWA updates"
```

---

## Final Review Gate

- [ ] Compare every section of `docs/superpowers/specs/2026-08-19-parking-self-use-pwa-design.md` with the completed diff.
- [ ] Confirm the recommendation-ordering code in `analysis.py` is unchanged.
- [ ] Confirm `rg -n "requests\.|Overpass|sync_calendars" app.py` shows no request-time external enrichment calls.
- [ ] Confirm `rg -n "登入|會員|常用目的地|最近查詢|localStorage" static templates app.py` finds no new account or destination-storage feature.
- [ ] Review fee examples against official raw text and mark uncertain values as unknown or ranges.
- [ ] Run the complete verification suite again after review fixes.
- [ ] Deploy to GCP only after a database backup and successful migration rehearsal.
