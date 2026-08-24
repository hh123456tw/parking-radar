"""部署合約與一次性清理 CLI 測試：管理端保護、無 IP 日誌與交易行為。"""

from datetime import datetime, timezone
from pathlib import Path

import analytics_cleanup


SITE = Path("deploy/nginx-parking-radar.conf")
LOGGING = Path("deploy/nginx-parking-radar-log-format.conf")
ENV_EXAMPLE = Path(".env.example")
README = Path("README.md")
FIXED_NOW = datetime(2026, 8, 23, 12, 0, tzinfo=timezone.utc)
# 90 天前：2026-05-25T12:00Z（手算字面值，不經由被測程式推導）。
EXPECTED_CUTOFF = datetime(2026, 5, 25, 12, 0, tzinfo=timezone.utc)
# 14 天前：2026-08-09T12:00Z（手算字面值，不經由被測程式推導）。
EXPECTED_SCRUB_CUTOFF = datetime(2026, 8, 9, 12, 0, tzinfo=timezone.utc)
SCHEMA = Path("schema.sql")
MIGRATION = Path("migrations/20260824_add_analytics_insights.sql")


def test_nginx_protects_admin_and_does_not_log_ip():
    site = SITE.read_text(encoding="utf-8")
    logging = LOGGING.read_text(encoding="utf-8")
    assert "location /admin/" in site
    assert "auth_basic" in site and "auth_basic_user_file" in site
    assert "limit_req zone=parking_admin_login" in site
    assert "$remote_addr" not in logging
    assert "$http_x_forwarded_for" not in logging


def test_nginx_admin_location_matches_app_proxy_and_no_store():
    site = SITE.read_text(encoding="utf-8")
    admin_block = site.split("location /admin/", 1)[1].split("}", 1)[0]
    assert "auth_basic_user_file /etc/nginx/.htpasswd-parking-radar" in admin_block
    assert "proxy_pass http://127.0.0.1:8000;" in admin_block
    for header in (
        "proxy_set_header Host $host;",
        "proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;",
        "proxy_set_header X-Forwarded-Proto $scheme;",
    ):
        assert header in admin_block
    assert 'add_header Cache-Control "no-store" always;' in admin_block


def test_access_log_format_omits_client_identity_and_query_string():
    logging_text = LOGGING.read_text(encoding="utf-8")
    log_format_body = logging_text.split("log_format", 1)[1].split(";", 1)[0]
    assert "$time_iso8601" in log_format_body
    assert '"$request_method $uri $server_protocol"' in log_format_body
    assert "$status $body_bytes_sent $request_time" in log_format_body
    for identity in ("$remote_addr", "$http_x_forwarded_for",
                     "$binary_remote_addr", "$request_uri"):
        assert identity not in log_format_body
    assert (
        "access_log /var/log/nginx/parking-radar.access.log parking_no_ip;"
        in SITE.read_text(encoding="utf-8"))


def test_example_env_has_names_but_no_real_secrets():
    text = ENV_EXAMPLE.read_text(encoding="utf-8")
    assert "ANALYTICS_HMAC_SECRET=" in text
    assert "ANALYTICS_ENABLED=1" in text
    assert "ANALYTICS_REQUIRE_CONSENT=1" in text
    assert "DEPLOY_VERSION=" in text
    assert "dev-only-change-me" not in text


def test_access_log_format_omits_referrer_and_user_agent():
    """日誌格式不得引用 referrer 或 User-Agent 變數。"""
    logging_text = LOGGING.read_text(encoding="utf-8")
    log_format_body = logging_text.split("log_format", 1)[1].split(";", 1)[0]
    for variable in ("$http_referer", "$http_user_agent"):
        assert variable not in log_format_body


def test_exact_admin_path_redirects_into_protected_prefix():
    """沒有斜線的 /admin 必須導向受保護的 /admin/，不能落到公開 proxy。"""
    site = SITE.read_text(encoding="utf-8")
    exact_block = site.split("location = /admin", 1)[1].split("}", 1)[0]
    assert "return 301 /admin/;" in exact_block
    assert "proxy_pass" not in exact_block
    assert site.index("location = /admin") < site.index("location /admin/")


def test_readme_applies_analytics_migration_before_restart_and_reload():
    text = README.read_text(encoding="utf-8")
    flow = text.split("### 部署補充：分析儀表板管理端保護與清理", 1)[1]
    flow = flow.split("#### 回滾", 1)[0]
    assert flow.index("migrations/20260823_add_analytics_events.sql") < \
        flow.index("systemctl restart parking-radar")
    assert flow.index("migrations/20260823_add_analytics_events.sql") < \
        flow.index("sudo nginx -t && sudo systemctl reload nginx")


def table_block(text, table):
    """切出 CREATE TABLE 表頭到 ENGINE 收尾句之間的定義區塊。"""
    header = "CREATE TABLE IF NOT EXISTS {} (".format(table)
    start = text.index(header)
    rest = text[start + len(header):]
    end_marker = ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;"
    end = rest.index(end_marker)
    return rest[:end + len(end_marker)].rstrip()


class FakeConnection:
    """記錄 commit / rollback / close 的假連線。"""

    def __init__(self):
        self.committed = False
        self.rolled_back = False
        self.closed = False

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True


def test_analytics_insights_migration_matches_schema_contract():
    """兩張新表的 schema 與 migration 定義必須逐字一致且只有兩張。"""
    schema = SCHEMA.read_text(encoding="utf-8")
    migration = MIGRATION.read_text(encoding="utf-8")
    for table in ("analytics_query_details", "analytics_recommendations"):
        assert table_block(schema, table) == table_block(migration, table)
        assert "CREATE TABLE IF NOT EXISTS {} (".format(table) in schema
        assert "CREATE TABLE IF NOT EXISTS {} (".format(table) in migration
    assert "request_id CHAR(36) PRIMARY KEY" in migration
    assert "PRIMARY KEY (request_id, rank_position)" in migration
    assert "idx_query_details_occurred (occurred_at)" in migration
    assert "idx_recommendations_occurred (occurred_at)" in migration
    assert "raw_query_text VARCHAR(500) NULL" in migration
    # 只允許既有 events 加新兩張，不增加第三張 analytics 表。
    assert migration.count("CREATE TABLE IF NOT EXISTS analytics_") == 2
    assert schema.count("CREATE TABLE IF NOT EXISTS analytics_") == 3


def test_cleanup_run_cleanup_commits_cutoffs_and_closes(monkeypatch):
    connection = FakeConnection()
    calls = {}

    def fake_scrub(conn, cutoff):
        calls["conn"] = conn
        calls["scrub_cutoff"] = cutoff
        return 3

    def fake_delete_insights(conn, cutoff):
        calls["insights_cutoff"] = cutoff
        return {"recommendations": 4, "query_details": 6}

    def fake_delete(conn, cutoff):
        calls["events_cutoff"] = cutoff
        return 12

    monkeypatch.setattr(analytics_cleanup, "get_connection", lambda: connection)
    monkeypatch.setattr(analytics_cleanup, "scrub_expired_query_text",
                        fake_scrub)
    monkeypatch.setattr(analytics_cleanup, "delete_expired_insights",
                        fake_delete_insights)
    monkeypatch.setattr(analytics_cleanup, "delete_expired_events", fake_delete)

    removed = analytics_cleanup.run_cleanup(now=FIXED_NOW)

    assert removed == {
        "scrubbed_query_text": 3,
        "recommendations": 4,
        "query_details": 6,
        "events": 12,
    }
    assert calls["conn"] is connection
    assert calls["scrub_cutoff"] == EXPECTED_SCRUB_CUTOFF
    assert calls["insights_cutoff"] == EXPECTED_CUTOFF
    assert calls["events_cutoff"] == EXPECTED_CUTOFF
    assert connection.committed is True
    assert connection.rolled_back is False
    assert connection.closed is True


def test_cleanup_run_cleanup_rolls_back_and_re_raises(monkeypatch):
    connection = FakeConnection()

    monkeypatch.setattr(analytics_cleanup, "get_connection", lambda: connection)
    monkeypatch.setattr(analytics_cleanup, "scrub_expired_query_text",
                        lambda _conn, _cutoff: 0)
    monkeypatch.setattr(analytics_cleanup, "delete_expired_insights",
                        lambda _conn, _cutoff: {"recommendations": 0,
                                                "query_details": 0})

    def failing_delete(_conn, _cutoff):
        raise RuntimeError("db down")

    monkeypatch.setattr(analytics_cleanup, "delete_expired_events",
                        failing_delete)

    try:
        analytics_cleanup.run_cleanup(now=FIXED_NOW)
        raise AssertionError("run_cleanup 必須重新拋出資料庫例外")
    except RuntimeError as exc:
        assert str(exc) == "db down"

    assert connection.committed is False
    assert connection.rolled_back is True
    assert connection.closed is True


def test_cleanup_run_cleanup_defaults_to_utc_now(monkeypatch):
    connection = FakeConnection()
    calls = {}

    class FixedUtcNow(datetime):
        @classmethod
        def now(cls, tz=None):
            return FIXED_NOW

    def fake_scrub(conn, cutoff):
        calls["scrub_cutoff"] = cutoff
        return 0

    def fake_delete_insights(conn, cutoff):
        calls["insights_cutoff"] = cutoff
        return {"recommendations": 0, "query_details": 0}

    def fake_delete(conn, cutoff):
        calls["events_cutoff"] = cutoff
        return 0

    monkeypatch.setattr(analytics_cleanup, "get_connection", lambda: connection)
    monkeypatch.setattr(analytics_cleanup, "scrub_expired_query_text",
                        fake_scrub)
    monkeypatch.setattr(analytics_cleanup, "delete_expired_insights",
                        fake_delete_insights)
    monkeypatch.setattr(analytics_cleanup, "delete_expired_events", fake_delete)
    monkeypatch.setattr(analytics_cleanup, "datetime", FixedUtcNow)

    analytics_cleanup.run_cleanup()

    assert calls["scrub_cutoff"] == EXPECTED_SCRUB_CUTOFF
    assert calls["scrub_cutoff"].tzinfo is timezone.utc
    assert calls["insights_cutoff"] == EXPECTED_CUTOFF
    assert calls["insights_cutoff"].tzinfo is timezone.utc
    assert calls["events_cutoff"] == EXPECTED_CUTOFF
    assert calls["events_cutoff"].tzinfo is timezone.utc
