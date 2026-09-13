# Local Snapshot Archive Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Archive complete expired parking-snapshot days as verified CSV.gz files on the VM before deleting those rows from MySQL.

**Architecture:** Keep the web query path unchanged and add a daily offline archive path. `database.py` streams and deletes one UTC day at a time, `snapshot_archive.py` owns safe file creation and validation, and `snapshot_cleanup.py` coordinates archive-before-delete using the existing cron entry.

**Tech Stack:** Python 3.13 standard library (`csv`, `gzip`, `pathlib`, `shutil`, `tempfile`), PyMySQL server-side cursor, MySQL 8, pytest, cron, GCP Compute Engine.

**Spec:** `docs/superpowers/specs/2026-09-13-local-snapshot-archive-design.md`

## Global Constraints

- Keep the application Taipei-only; do not restore New Taipei code or database columns.
- MySQL remains the only source used by web requests and the seven-day history chart.
- Archive files default to `/opt/parking-archives/YYYY/MM/parking-snapshots-YYYY-MM-DD.csv.gz`.
- Archive only complete UTC days older than the eight-day retention boundary.
- Never delete a day's MySQL rows unless that day's final archive validates successfully.
- Use streaming reads and batches of at most 10,000 deletes on the 1 GB VM.
- Stop without deleting when free archive-disk space is below 1 GiB.
- Use only Python standard-library file formats; add no runtime dependency.
- Leave the 2026-09-12 SQL backup untouched; importing historical backups is out of scope.
- Keep all new code commented with concise Chinese docstrings or comments.

## File Structure

- Create `snapshot_archive.py`: complete-day calculation, streaming CSV.gz writer, archive validation, atomic rename, disk guard.
- Modify `database.py`: oldest timestamp lookup, server-side daily-row iterator, date-range batched deletion.
- Modify `snapshot_cleanup.py`: archive each expired complete UTC day, then delete only that archived day.
- Modify `config.py`: `SNAPSHOT_ARCHIVE_DIR` with `/opt/parking-archives` default.
- Modify `.env.example`: document the archive-directory override without a secret value.
- Modify `.github/workflows/ci.yml`: compile the new archive module.
- Modify `tests/test_database_collector.py`: database boundary tests.
- Create `tests/test_snapshot_archive.py`: real gzip/CSV and failure-safety tests.
- Modify `tests/test_snapshot_cleanup.py`: orchestration, retention boundary, retry and transaction tests.
- Modify `tests/test_ci_contract.py`: keep the new module in CI coverage.
- Modify `README.md`: one concise operational note and archive location; no deployment runbook expansion.

---

### Task 1: Stream and Delete One UTC Day Through the Database Layer

**Files:**
- Modify: `database.py`
- Modify: `tests/test_database_collector.py`

**Interfaces:**
- Produces: `fetch_oldest_snapshot_time(connection) -> datetime | None`
- Produces: `iter_snapshot_archive_rows(connection, start_utc, end_utc, fetch_size=2000) -> Iterator[dict]`
- Produces: `delete_snapshot_range_batch(connection, start_utc, end_utc, batch_size=10000) -> int`
- Removes: `delete_expired_snapshots_batch`; Task 3 changes its only production caller.

- [ ] **Step 1: Write failing tests for oldest-time lookup and streaming rows**

Extend the database test cursor with controlled `fetchone()` and `fetchmany()` behavior. Add tests that expect the following observable SQL contract:

```python
def test_fetch_oldest_snapshot_time_uses_captured_index_order():
    oldest = datetime(2026, 9, 1, 0, 0)
    connection = StreamingSpyConnection(first_row={"captured_at": oldest})

    assert database.fetch_oldest_snapshot_time(connection) == oldest
    sql, params = connection.calls[0]
    assert "ORDER BY captured_at LIMIT 1" in " ".join(sql.split())
    assert params is None


def test_iter_snapshot_archive_rows_streams_joined_ml_columns():
    start = datetime(2026, 9, 1, tzinfo=timezone.utc)
    end = datetime(2026, 9, 2, tzinfo=timezone.utc)
    rows = [{
        "lot_id": "TPE0001", "lot_name": "測試停車場", "district": "信義區",
        "total_spaces": 100, "available_spaces": 8,
        "source_updated_at": datetime(2026, 9, 1, 0, 0),
        "captured_at": datetime(2026, 9, 1, 0, 1),
    }]
    connection = StreamingSpyConnection(batches=[rows, []])

    assert list(database.iter_snapshot_archive_rows(
        connection, start, end, fetch_size=2000)) == rows
    sql, params = connection.calls[0]
    assert "JOIN parking_lots l ON l.lot_id = s.lot_id" in sql
    assert "s.captured_at >= %s AND s.captured_at < %s" in sql
    assert params == (start, end)
    assert connection.fetch_sizes == [2000, 2000]
```

- [ ] **Step 2: Run the new tests and verify RED**

Run:

```powershell
python -m pytest -q tests/test_database_collector.py -k "oldest_snapshot or archive_rows"
```

Expected: FAIL because both database functions are absent.

- [ ] **Step 3: Implement indexed oldest lookup and server-side streaming**

Add to `database.py`:

```python
def fetch_oldest_snapshot_time(connection):
    """依 captured_at 索引取得最早快照；空表回傳 None。"""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT captured_at FROM parking_snapshots "
            "ORDER BY captured_at LIMIT 1"
        )
        row = cursor.fetchone()
        return row.get("captured_at") if row else None


def iter_snapshot_archive_rows(connection, start_utc, end_utc,
                               fetch_size=2000):
    """以伺服器端游標串流單日訓練欄位，避免一次載入全部資料。"""
    sql = """
        SELECT s.lot_id, l.lot_name, l.district, l.total_spaces,
               s.available_spaces, s.source_updated_at, s.captured_at
        FROM parking_snapshots s
        JOIN parking_lots l ON l.lot_id = s.lot_id
        WHERE s.captured_at >= %s AND s.captured_at < %s
        ORDER BY s.captured_at, s.lot_id
    """
    with connection.cursor(pymysql.cursors.SSDictCursor) as cursor:
        cursor.execute(sql, (start_utc, end_utc))
        while True:
            rows = cursor.fetchmany(fetch_size)
            if not rows:
                return
            yield from rows
```

Validate `fetch_size > 0` before opening the cursor and raise `ValueError("fetch_size must be positive")` otherwise.

- [ ] **Step 4: Write and verify a failing bounded range-delete test**

Replace the old cutoff-delete test with:

```python
def test_delete_snapshot_range_batch_is_half_open_and_bounded():
    connection = SpyConnection()
    connection.spy_cursor.rowcount = 9
    start = datetime(2026, 9, 1, tzinfo=timezone.utc)
    end = datetime(2026, 9, 2, tzinfo=timezone.utc)

    removed = database.delete_snapshot_range_batch(
        connection, start, end, batch_size=5000)

    sql, params = connection.spy_cursor.calls[0]
    assert "captured_at >= %s AND captured_at < %s" in " ".join(sql.split())
    assert "LIMIT %s" in sql
    assert params == (start, end, 5000)
    assert removed == 9
```

Run:

```powershell
python -m pytest -q tests/test_database_collector.py::test_delete_snapshot_range_batch_is_half_open_and_bounded
```

Expected: FAIL because `delete_snapshot_range_batch` does not exist.

- [ ] **Step 5: Implement range deletion and remove the obsolete cutoff function**

```python
def delete_snapshot_range_batch(connection, start_utc, end_utc,
                                batch_size=10000):
    """分批刪除單一半開 UTC 日期區間，縮短每次交易持鎖時間。"""
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")
    sql = """
        DELETE FROM parking_snapshots
        WHERE captured_at >= %s AND captured_at < %s
        ORDER BY captured_at
        LIMIT %s
    """
    with connection.cursor() as cursor:
        cursor.execute(sql, (start_utc, end_utc, batch_size))
        return cursor.rowcount
```

Delete `delete_expired_snapshots_batch` after Task 3 no longer imports it; until then, leave it temporarily so intermediate tests can run.

- [ ] **Step 6: Run database tests and commit**

Run:

```powershell
python -m pytest -q tests/test_database_collector.py
```

Expected: all tests PASS.

Commit:

```powershell
git add database.py tests/test_database_collector.py
git commit -m "feat: stream daily snapshot archive rows"
```

---

### Task 2: Write and Validate Atomic Daily CSV.gz Archives

**Files:**
- Create: `snapshot_archive.py`
- Create: `tests/test_snapshot_archive.py`

**Interfaces:**
- Consumes: `database.iter_snapshot_archive_rows(connection, start_utc, end_utc, fetch_size=2000)`
- Produces: `archive_path(root: Path, day: date) -> Path`
- Produces: `validate_archive(path: Path) -> int`
- Produces: `archive_day(connection, day: date, archive_root: Path, disk_usage=shutil.disk_usage) -> dict`
- Result dictionary: `{"path": Path | None, "rows": int, "created": bool}`

- [ ] **Step 1: Write failing path and real CSV.gz tests**

Create `tests/test_snapshot_archive.py` with literal Unicode rows and a temporary directory:

```python
def test_archive_day_writes_verified_unicode_csv_gz_atomically(tmp_path, monkeypatch):
    day = date(2026, 9, 1)
    rows = [{
        "lot_id": "TPE0001", "lot_name": "臺北車站停車場", "district": "中正區",
        "total_spaces": 100, "available_spaces": 8,
        "source_updated_at": datetime(2026, 9, 1, 0, 0),
        "captured_at": datetime(2026, 9, 1, 0, 1),
    }]
    monkeypatch.setattr(snapshot_archive, "iter_snapshot_archive_rows",
                        lambda *_args, **_kwargs: iter(rows))

    result = snapshot_archive.archive_day(
        object(), day, tmp_path, disk_usage=ample_disk)

    expected = tmp_path / "2026" / "09" / "parking-snapshots-2026-09-01.csv.gz"
    assert result == {"path": expected, "rows": 1, "created": True}
    assert snapshot_archive.validate_archive(expected) == 1
    with gzip.open(expected, "rt", encoding="utf-8", newline="") as handle:
        archived = list(csv.DictReader(handle))
    assert archived[0]["lot_name"] == "臺北車站停車場"
    assert not list(expected.parent.glob("*.tmp"))
```

Also assert `archive_path(tmp_path, date(2026, 9, 1))` produces the exact expected hierarchy.

- [ ] **Step 2: Run the archive test and verify RED**

Run:

```powershell
python -m pytest -q tests/test_snapshot_archive.py::test_archive_day_writes_verified_unicode_csv_gz_atomically
```

Expected: collection FAIL because `snapshot_archive.py` does not exist.

- [ ] **Step 3: Implement path, CSV fields, validation and atomic writing**

Create `snapshot_archive.py` with:

```python
CSV_FIELDS = (
    "lot_id", "lot_name", "district", "total_spaces",
    "available_spaces", "source_updated_at", "captured_at",
)
MIN_FREE_BYTES = 1024 ** 3


def archive_path(root, day):
    """依 UTC 日期建立可預測的年月封存路徑。"""
    root = Path(root)
    return root / f"{day:%Y}" / f"{day:%m}" / (
        f"parking-snapshots-{day:%Y-%m-%d}.csv.gz")


def validate_archive(path):
    """完整讀取 gzip 與固定 CSV 標頭，回傳資料列數。"""
    with gzip.open(path, "rt", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if tuple(reader.fieldnames or ()) != CSV_FIELDS:
            raise ValueError("invalid snapshot archive header")
        return sum(1 for _row in reader)
```

`archive_day` must:

1. Create only the year/month directory.
2. Check `disk_usage(final_path.parent).free >= MIN_FREE_BYTES`.
3. Validate and reuse an existing final file without overwriting it.
4. Stream rows to a `NamedTemporaryFile(delete=False, dir=final_path.parent)` wrapped by gzip text mode.
5. Return `{path: None, rows: 0, created: False}` and remove the temporary file when no rows exist.
6. Close the gzip stream, validate the temporary file and compare its row count with the written count.
7. Use `os.replace(temp_path, final_path)` only after validation.
8. Unlink the temporary path on every exception and re-raise.

- [ ] **Step 4: Write failing safety tests**

Add these independent behaviors:

```python
def test_archive_day_reuses_valid_existing_archive_without_querying_database(
        tmp_path, monkeypatch):
    day = date(2026, 9, 1)
    final = snapshot_archive.archive_path(tmp_path, day)
    final.parent.mkdir(parents=True)
    with gzip.open(final, "wt", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=snapshot_archive.CSV_FIELDS)
        writer.writeheader()
        writer.writerow(SAMPLE_ROW)
    monkeypatch.setattr(
        snapshot_archive, "iter_snapshot_archive_rows",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("既有檔驗證成功後不應查資料庫")))

    result = snapshot_archive.archive_day(
        object(), day, tmp_path, disk_usage=ample_disk)

    assert result == {"path": final, "rows": 1, "created": False}


def test_archive_day_rejects_corrupt_existing_archive_without_overwriting(
        tmp_path):
    final = snapshot_archive.archive_path(tmp_path, date(2026, 9, 1))
    final.parent.mkdir(parents=True)
    final.write_bytes(b"not-gzip")

    with pytest.raises((gzip.BadGzipFile, EOFError)):
        snapshot_archive.archive_day(
            object(), date(2026, 9, 1), tmp_path, disk_usage=ample_disk)

    assert final.read_bytes() == b"not-gzip"


def test_archive_day_low_disk_space_writes_nothing(tmp_path):
    low_disk = lambda _path: SimpleNamespace(free=1024 ** 3 - 1)

    with pytest.raises(
            RuntimeError, match="snapshot archive disk space below 1 GiB"):
        snapshot_archive.archive_day(
            object(), date(2026, 9, 1), tmp_path, disk_usage=low_disk)

    assert not list(tmp_path.rglob("*.gz"))


def test_archive_day_stream_failure_removes_temp_and_leaves_no_final(
        tmp_path, monkeypatch):
    def broken_rows(*_args, **_kwargs):
        yield SAMPLE_ROW
        raise OSError("database stream stopped")

    monkeypatch.setattr(
        snapshot_archive, "iter_snapshot_archive_rows", broken_rows)

    with pytest.raises(OSError, match="database stream stopped"):
        snapshot_archive.archive_day(
            object(), date(2026, 9, 1), tmp_path, disk_usage=ample_disk)

    assert not list(tmp_path.rglob("*.tmp"))
    assert not list(tmp_path.rglob("*.gz"))


def test_archive_day_with_no_rows_creates_no_empty_archive(
        tmp_path, monkeypatch):
    monkeypatch.setattr(
        snapshot_archive, "iter_snapshot_archive_rows",
        lambda *_args, **_kwargs: iter(()))

    result = snapshot_archive.archive_day(
        object(), date(2026, 9, 1), tmp_path, disk_usage=ample_disk)

    assert result == {"path": None, "rows": 0, "created": False}
    assert not list(tmp_path.rglob("*.gz"))
```

Define `SAMPLE_ROW` with the seven literal values from Step 1 and `ample_disk` as a function returning `SimpleNamespace(free=2 * 1024 ** 3)`. Import `pytest` and `SimpleNamespace` at the top of the test file.

- [ ] **Step 5: Run safety tests, complete minimal guards, and run all archive tests**

Run before the guards and expect the new tests to FAIL:

```powershell
python -m pytest -q tests/test_snapshot_archive.py
```

Implement only the branches described in Step 4, then rerun the same command and expect all tests to PASS.

- [ ] **Step 6: Commit the archive writer**

```powershell
git add snapshot_archive.py tests/test_snapshot_archive.py
git commit -m "feat: archive snapshots as verified csv gzip"
```

---

### Task 3: Enforce Archive-Before-Delete in the Daily Cleanup

**Files:**
- Modify: `snapshot_cleanup.py`
- Modify: `config.py`
- Modify: `.env.example`
- Modify: `tests/test_snapshot_cleanup.py`
- Modify: `database.py`

**Interfaces:**
- Consumes: `fetch_oldest_snapshot_time`, `archive_day`, `delete_snapshot_range_batch`
- Produces: `complete_expired_days(oldest: datetime | None, now: datetime, retention_days=8) -> list[date]`
- Produces: `run_cleanup(now=None, batch_size=10000, archive_root=None) -> dict`
- Result dictionary: `{"archived_files": int, "archived_rows": int, "deleted_snapshots": int}`

- [ ] **Step 1: Replace old cleanup expectations with failing complete-day tests**

Add literal date-boundary assertions:

```python
def test_complete_expired_days_keeps_partial_cutoff_day():
    oldest = datetime(2026, 9, 1, 2, 0, tzinfo=timezone.utc)
    now = datetime(2026, 9, 10, 19, 23, tzinfo=timezone.utc)

    assert snapshot_cleanup.complete_expired_days(oldest, now) == [
        date(2026, 9, 1),
    ]


def test_complete_expired_days_handles_empty_database():
    assert snapshot_cleanup.complete_expired_days(None, FIXED_NOW) == []
```

Here the eight-day boundary is `2026-09-02 19:23 UTC`. The implementation floors that boundary to `2026-09-02 00:00 UTC`, so only September 1 is a complete eligible UTC day.

- [ ] **Step 2: Run boundary tests and verify RED**

Run:

```powershell
python -m pytest -q tests/test_snapshot_cleanup.py -k "complete_expired_days"
```

Expected: FAIL because `complete_expired_days` is absent.

- [ ] **Step 3: Implement complete UTC-day selection and archive configuration**

Add to `config.py`:

```python
SNAPSHOT_ARCHIVE_DIR = os.getenv(
    "SNAPSHOT_ARCHIVE_DIR", "/opt/parking-archives")
```

Add `SNAPSHOT_ARCHIVE_DIR=/opt/parking-archives` to `.env.example`; it is a non-secret path and matches the production default.

Implement in `snapshot_cleanup.py`:

```python
def complete_expired_days(oldest, now, retention_days=8):
    """列出截止日以前已完整結束的 UTC 日期。"""
    if oldest is None:
        return []
    boundary_day = (now - timedelta(days=retention_days)).date()
    day = oldest.date()
    return list(iter_days(day, boundary_day))
```

`iter_days(start, stop)` yields dates from `start` inclusive to `stop` exclusive. Normalize naive MySQL datetimes as UTC and convert aware inputs to UTC before taking `.date()`.

- [ ] **Step 4: Write failing orchestration tests for archive-before-delete**

Use fakes at the database/file boundaries and assert observable ordering:

```python
def test_cleanup_archives_each_day_before_deleting_it(monkeypatch, tmp_path):
    events = []
    # Oldest 2026-09-01 and now 2026-09-10 selects exactly 2026-09-01.
    # archive_day appends ("archive", day) and returns two rows.
    # delete range returns [2, 0], appending ("delete", start, end) each time.
    result = snapshot_cleanup.run_cleanup(
        now=datetime(2026, 9, 10, 19, 23, tzinfo=timezone.utc),
        batch_size=2, archive_root=tmp_path)
    assert events[0] == ("archive", date(2026, 9, 1))
    assert events[1][0] == "delete"
    assert result == {
        "archived_files": 1,
        "archived_rows": 2,
        "deleted_snapshots": 2,
    }


def test_cleanup_archive_failure_never_calls_delete(monkeypatch, tmp_path):
    # archive_day raises OSError; delete fake raises AssertionError if invoked.
    # Expect the original OSError and no commit.
```

Also cover a valid existing archive (`created=False`) followed by deletion, a delete failure that rolls back the current batch, empty database, and non-positive batch size rejected before opening a connection.

- [ ] **Step 5: Run orchestration tests and verify RED**

Run:

```powershell
python -m pytest -q tests/test_snapshot_cleanup.py
```

Expected: FAIL because the old cleanup deletes directly and returns an integer.

- [ ] **Step 6: Implement archive-before-delete orchestration**

For each `complete_expired_days(...)` result:

```python
archive = archive_day(connection, day, archive_root)
if archive["rows"] == 0:
    continue
start = datetime.combine(day, time.min, tzinfo=timezone.utc)
end = start + timedelta(days=1)
while True:
    removed = delete_snapshot_range_batch(
        connection, start, end, batch_size)
    connection.commit()
    deleted_total += removed
    if removed < batch_size:
        break
```

Count `archived_files` for either newly created or valid existing files used as deletion proof. Count `archived_rows` from the validated archive. On any exception, roll back the current database transaction, close the connection, and re-raise.

- [ ] **Step 7: Remove obsolete cleanup API and run focused tests**

Remove `delete_expired_snapshots_batch` from `database.py` and all imports/tests referencing it.

Run:

```powershell
python -m pytest -q tests/test_snapshot_cleanup.py tests/test_snapshot_archive.py tests/test_database_collector.py
```

Expected: all tests PASS.

- [ ] **Step 8: Commit orchestration and configuration**

```powershell
git add snapshot_cleanup.py database.py config.py .env.example tests/test_snapshot_cleanup.py
git commit -m "feat: archive expired snapshots before deletion"
```

---

### Task 4: CI, Operations Documentation, QA, Push and Reversible Deployment

**Files:**
- Modify: `.github/workflows/ci.yml`
- Modify: `tests/test_ci_contract.py`
- Modify: `README.md`
- Deployment-only: `/opt/parking-archives`, parking user's crontab, atomic app release directory

**Interfaces:**
- CI must compile `snapshot_archive.py` and `snapshot_cleanup.py`.
- Production cron keeps one `snapshot_cleanup.py` line at `23 19 * * *`, which is 03:23 Asia/Taipei on a UTC VM.

- [ ] **Step 1: Write a failing CI coverage test**

Update the existing compile-contract test to require both modules:

```python
for module in (
    "analytics_service.py", "analytics_database.py", "status_service.py",
    "analytics_cleanup.py", "snapshot_archive.py", "snapshot_cleanup.py",
):
    assert module in compileall_line
```

Run:

```powershell
python -m pytest -q tests/test_ci_contract.py::test_ci_compiles_analytics_modules_and_checks_admin_js
```

Expected: FAIL until `snapshot_archive.py` is added to the workflow compile line.

- [ ] **Step 2: Update CI and concise README operations note**

Add `snapshot_archive.py` to the Python compile command. Add a short README paragraph stating:

```markdown
停車快照在 MySQL 保留近期資料；每日清理會先將完整 UTC 日期封存為
`/opt/parking-archives/YYYY/MM/parking-snapshots-YYYY-MM-DD.csv.gz`，
驗證成功後才分批刪除，網站查詢不讀取封存檔。
```

Do not add bucket, machine-learning implementation, or detailed restore commands.

- [ ] **Step 3: Run all local quality gates**

```powershell
python -m pytest -q tests
python -m compileall -q app.py ai_service.py analysis.py analytics_capture.py analytics_cleanup.py analytics_database.py analytics_service.py calendar_service.py collector.py config.py database.py fee_service.py geocoder.py parking_metadata.py snapshot_archive.py snapshot_cleanup.py status_service.py walking_service.py tests
node --check static/app.js
node --check static/admin_analytics.js
node --check static/sw.js
git diff --check
```

Expected: every command exits 0 with no failed tests or syntax errors.

- [ ] **Step 4: Review the complete branch diff and commit final integration**

Confirm the diff contains no web-query changes except imports needed by offline cleanup, no New Taipei code, no credentials and no GCS dependency.

```powershell
git diff --check master...HEAD
git status --short
git add .github/workflows/ci.yml tests/test_ci_contract.py README.md
git commit -m "docs: document local snapshot archives"
```

- [ ] **Step 5: Push and require green GitHub Actions**

```powershell
git push -u origin codex/local-snapshot-archive
gh run list --repo hh123456tw/parking-radar --branch codex/local-snapshot-archive --limit 3
```

Wait for the matching head SHA and require conclusion `success` before merging.

- [ ] **Step 6: Back up and stage a reversible production release**

Before mutation:

1. Archive the approved Git commit and upload it to `/tmp`.
2. Save `/opt/parking-hell` as a timestamped rollback directory by atomic rename during the switch.
3. Run `mysqldump --single-transaction --quick --databases parking_hell | gzip` into `/opt/parking-backups` and verify it with `gzip -t`.
4. Save `crontab -u parking -l` under `/tmp`.
5. Create `/opt/parking-archives` with owner `parking:parking` and mode `750`.
6. Set `SNAPSHOT_ARCHIVE_DIR=/opt/parking-archives` in the deployed `.env`; do not print any secret values.

- [ ] **Step 7: Prove archive safety in an isolated production directory**

Use the deployed virtual environment and production database read access, but pass a temporary archive directory and a `now` value selecting at most one complete expired date. Do not call deletion in this probe; call `snapshot_archive.archive_day(...)` only. Verify:

```bash
gzip -t /opt/parking-archive-smoke/YYYY/MM/parking-snapshots-YYYY-MM-DD.csv.gz
zcat /opt/parking-archive-smoke/YYYY/MM/parking-snapshots-YYYY-MM-DD.csv.gz | head -n 2
```

The header must equal the seven fixed fields and the first data row must decode as UTF-8. Remove only the explicitly verified smoke directory after validation.

- [ ] **Step 8: Switch the app, run production cleanup and verify archives**

1. Atomically switch the staged release into `/opt/parking-hell` and restart `parking-radar`.
2. Require `curl -fsS http://127.0.0.1:8000/health` within 30 seconds; otherwise restore the old app and crontab.
3. Keep exactly one cleanup cron line at `23 19 * * *`.
4. Run `sudo -u parking .../python snapshot_cleanup.py` once.
5. For every file created by that run, require `gzip -t`, exact CSV header and at least one data row.
6. Query `COUNT(*)`, `MIN(captured_at)`, `MAX(captured_at)` and confirm no rows remain for each archived day.

- [ ] **Step 9: Run live acceptance and performance checks**

From outside the VM:

```text
GET /health → 200
POST /api/query manual 中正區 → 200, three recommendations, fresh data, <20 s
POST /api/query chat 我要去台北車站 → 200, correct destination, three recommendations, <20 s
Homepage contains Taipei and does not contain New Taipei
```

On the VM require `parking-radar`, `nginx`, and `parking-metadata-refresh.timer` active; inspect the last application and cleanup logs for tracebacks.

- [ ] **Step 10: Merge, push master and verify master CI**

Fast-forward merge only after branch CI and live checks pass:

```powershell
git switch master
git merge --ff-only codex/local-snapshot-archive
git push origin master
```

Wait for the matching master GitHub Actions run and require conclusion `success`. Record the deployed SHA, rollback application path, SQL backup path, archive directory, archived row count and live timings in the final handoff.
