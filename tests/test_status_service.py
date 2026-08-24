"""狀態服務測試：固定門檻、本機資源讀取與各元件獨立降級。"""

import os
from datetime import datetime, timedelta, timezone

import status_service
from status_service import (
    app_uptime_minutes, classify_data_age, classify_disk, classify_load,
    classify_memory, classify_mysql_latency, read_linux_status,
)

NOW_UTC = datetime(2026, 8, 23, 8, 0, tzinfo=timezone.utc)
FIXED_SYSTEM = {
    "memory_percent": 75.0, "load_5m": 0.3, "disk_remaining_percent": 40.0,
}


def test_status_thresholds_and_unknown_values_are_honest():
    assert classify_data_age(20)["tone"] == "green"
    assert classify_data_age(45)["tone"] == "yellow"
    assert classify_data_age(61)["tone"] == "red"
    assert classify_data_age(None)["tone"] == "gray"
    assert classify_memory(None)["tone"] == "gray"
    assert classify_disk(9.9)["tone"] == "red"


def test_classify_threshold_boundaries():
    assert classify_data_age(30)["tone"] == "green"
    assert classify_data_age(31)["tone"] == "yellow"
    assert classify_data_age(60)["tone"] == "yellow"
    assert classify_memory(79.9)["tone"] == "green"
    assert classify_memory(80)["tone"] == "yellow"
    assert classify_memory(90)["tone"] == "yellow"
    assert classify_memory(90.1)["tone"] == "red"
    assert classify_disk(20.1)["tone"] == "green"
    assert classify_disk(20)["tone"] == "yellow"
    assert classify_disk(10)["tone"] == "yellow"
    assert classify_load(0.24)["tone"] == "green"
    assert classify_load(0.25)["tone"] == "yellow"
    assert classify_load(0.6)["tone"] == "yellow"
    assert classify_load(0.61)["tone"] == "red"
    assert classify_load(None)["tone"] == "gray"
    assert classify_mysql_latency(99.9)["tone"] == "green"
    assert classify_mysql_latency(100)["tone"] == "yellow"
    assert classify_mysql_latency(500)["tone"] == "yellow"
    assert classify_mysql_latency(500.1)["tone"] == "red"
    assert classify_mysql_latency(None)["tone"] == "gray"


def test_metadata_health_thresholds_match_monthly_timer():
    """月度同步保留四天緩衝，逾期先黃燈，超過 45 天才紅燈。"""
    classifier = getattr(status_service, "classify_metadata_age", None)
    assert classifier is not None
    assert classifier(35 * 24 * 60)["tone"] == "green"
    assert classifier(35 * 24 * 60 + 1)["tone"] == "yellow"
    assert classifier(45 * 24 * 60)["tone"] == "yellow"
    assert classifier(45 * 24 * 60 + 1)["tone"] == "red"
    assert classifier(None)["tone"] == "gray"


def test_linux_status_parses_memory_and_uses_five_minute_load(
        tmp_path, monkeypatch):
    meminfo = tmp_path / "meminfo"
    meminfo.write_text(
        "MemTotal: 1000 kB\nMemAvailable: 250 kB\n", encoding="utf-8")
    monkeypatch.setattr(
        os, "getloadavg", lambda: (0.1, 0.3, 0.4), raising=False)

    status = read_linux_status(meminfo_path=meminfo, disk_path=tmp_path)

    assert status["memory_percent"] == 75.0
    assert status["load_5m"] == 0.3


def test_linux_status_missing_meminfo_is_gray(tmp_path, monkeypatch):
    monkeypatch.setattr(
        os, "getloadavg", lambda: (0.1, 0.3, 0.4), raising=False)

    status = read_linux_status(
        meminfo_path=tmp_path / "no-such-meminfo", disk_path=tmp_path)

    assert status["memory_percent"] is None
    assert status["load_5m"] == 0.3


def test_linux_status_meminfo_empty_token_is_gray(tmp_path, monkeypatch):
    """MemAvailable 缺值時不得以 IndexError 擊垮狀態讀取，要降級為灰色。"""
    meminfo = tmp_path / "meminfo"
    meminfo.write_text(
        "MemTotal: 1000 kB\nMemAvailable:\n", encoding="utf-8")
    monkeypatch.setattr(
        os, "getloadavg", lambda: (0.1, 0.3, 0.4), raising=False)

    status = read_linux_status(meminfo_path=meminfo, disk_path=tmp_path)

    assert status["memory_percent"] is None
    assert status["load_5m"] == 0.3


def test_linux_status_unsupported_load_and_disk_are_gray(
        tmp_path, monkeypatch):
    meminfo = tmp_path / "meminfo"
    meminfo.write_text(
        "MemTotal: 1000 kB\nMemAvailable: 250 kB\n", encoding="utf-8")
    monkeypatch.setattr(
        os, "getloadavg",
        lambda: (_ for _ in ()).throw(OSError("not supported")),
        raising=False,
    )

    status = read_linux_status(
        meminfo_path=meminfo, disk_path=tmp_path / "no-such-dir")

    assert status["memory_percent"] == 75.0
    assert status["load_5m"] is None
    assert status["disk_remaining_percent"] is None


def test_app_uptime_minutes_uses_module_start(monkeypatch):
    monkeypatch.setattr(
        status_service, "APP_STARTED_AT",
        datetime(2026, 8, 23, 6, 0, tzinfo=timezone.utc),
    )
    assert app_uptime_minutes(
        datetime(2026, 8, 23, 6, 45, tzinfo=timezone.utc)) == 45


class FakeCursor:
    """記錄 SQL；資料庫錯誤時每次 execute 都失敗。"""

    def __init__(self, connection):
        self.connection = connection

    def execute(self, sql, params=None):
        if self.connection.database_error:
            raise RuntimeError("database down")
        self.connection.executions.append((sql, params))

    def fetchall(self):
        if not self.connection.row_sets:
            return []
        return self.connection.row_sets.pop(0)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class FakeConnection:
    """依查詢順序吐出結果列，並記錄 execute 與關閉狀態。"""

    def __init__(self, row_sets=None, database_error=False):
        self.row_sets = list(row_sets or [])
        self.database_error = database_error
        self.closed = False
        self.executions = []

    def cursor(self):
        return FakeCursor(self)

    def close(self):
        self.closed = True


def _status_connection():
    times_row = [{
        "official_data_at": datetime(2026, 8, 23, 7, 15, tzinfo=timezone.utc),
        "collector_at": datetime(2026, 8, 23, 7, 45, tzinfo=timezone.utc),
        "metadata_at": datetime(2026, 8, 23, 7, 50, tzinfo=timezone.utc),
    }]
    return FakeConnection([times_row, [{"1": 1}]])


def test_build_status_reports_all_components(monkeypatch):
    monkeypatch.setattr(
        status_service, "read_linux_status", lambda **_: dict(FIXED_SYSTEM))
    connection = _status_connection()

    status = status_service.build_status(
        connection, now_utc=NOW_UTC, deploy_version="abc1234",
        analytics_enabled=True,
    )

    assert status["application"]["tone"] == "green"
    assert status["database"]["tone"] == "green"
    assert status["official_data"]["tone"] == "yellow"  # 45 分鐘
    assert status["collector"]["tone"] == "green"  # 15 分鐘
    assert status["metadata"]["tone"] == "green"  # 10 分鐘
    assert status["load"]["tone"] == "yellow"  # 0.30
    assert status["memory"]["tone"] == "green"  # 75%
    assert status["disk"]["tone"] == "green"  # 40%
    assert status["deploy"] == {
        "label": "部署版本", "value": "abc1234", "tone": "green",
        "detail": "DEPLOY_VERSION 注入的 Git commit",
    }
    assert status["analytics"]["tone"] == "green"
    assert set(status["application"]) == {"label", "value", "tone", "detail"}


def test_build_status_reports_recent_monthly_metadata_as_green(monkeypatch):
    """三天前完成的月度後設同步應顯示綠色與天數，不得誤報紅燈。"""
    monkeypatch.setattr(
        status_service, "read_linux_status", lambda **_: dict(FIXED_SYSTEM))
    times_row = [{
        "official_data_at": NOW_UTC - timedelta(minutes=15),
        "collector_at": NOW_UTC - timedelta(minutes=10),
        "metadata_at": NOW_UTC - timedelta(days=3),
    }]

    status = status_service.build_status(
        FakeConnection([times_row, [{"1": 1}]]), now_utc=NOW_UTC)

    assert status["metadata"]["tone"] == "green"
    assert status["metadata"]["value"] == "3 天前"
    assert "每月更新" in status["metadata"]["detail"]


def test_build_status_degrades_database_and_data_independently(monkeypatch):
    monkeypatch.setattr(
        status_service, "read_linux_status", lambda **_: dict(FIXED_SYSTEM))
    connection = FakeConnection(database_error=True)

    status = status_service.build_status(
        connection, now_utc=NOW_UTC, deploy_version="unknown",
        analytics_enabled=False,
    )

    assert status["application"]["tone"] == "green"
    assert status["database"]["tone"] == "red"
    assert status["official_data"]["tone"] == "gray"
    assert status["collector"]["tone"] == "gray"
    assert status["metadata"]["tone"] == "gray"
    assert status["load"]["tone"] == "yellow"
    assert status["memory"]["tone"] == "green"
    assert status["deploy"]["tone"] == "gray"
    assert status["analytics"]["tone"] == "gray"


def test_build_status_without_connection_stays_gray_and_red(monkeypatch):
    monkeypatch.setattr(
        status_service, "read_linux_status", lambda **_: dict(FIXED_SYSTEM))

    status = status_service.build_status(
        None, now_utc=NOW_UTC, analytics_enabled=True)

    assert status["application"]["tone"] == "green"
    assert status["database"]["tone"] == "red"
    assert status["official_data"]["tone"] == "gray"
    assert status["collector"]["tone"] == "gray"
    assert status["metadata"]["tone"] == "gray"
