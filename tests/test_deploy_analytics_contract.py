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
    assert "DEPLOY_VERSION=" in text
    assert "dev-only-change-me" not in text


def test_readme_applies_analytics_migration_before_restart_and_reload():
    text = README.read_text(encoding="utf-8")
    flow = text.split("### 部署補充：分析儀表板管理端保護與清理", 1)[1]
    flow = flow.split("#### 回滾", 1)[0]
    assert flow.index("migrations/20260823_add_analytics_events.sql") < \
        flow.index("systemctl restart parking-radar")
    assert flow.index("migrations/20260823_add_analytics_events.sql") < \
        flow.index("sudo nginx -t && sudo systemctl reload nginx")


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


def test_cleanup_main_commits_cutoff_and_closes(monkeypatch):
    connection = FakeConnection()
    calls = {}

    def fake_delete(conn, cutoff):
        calls["conn"] = conn
        calls["cutoff"] = cutoff
        return 12

    monkeypatch.setattr(analytics_cleanup, "get_connection", lambda: connection)
    monkeypatch.setattr(analytics_cleanup, "delete_expired_events", fake_delete)

    removed = analytics_cleanup.main(now=FIXED_NOW)

    assert removed == 12
    assert calls["conn"] is connection
    assert calls["cutoff"] == EXPECTED_CUTOFF
    assert connection.committed is True
    assert connection.rolled_back is False
    assert connection.closed is True


def test_cleanup_main_rolls_back_and_re_raises(monkeypatch):
    connection = FakeConnection()

    def failing_delete(_conn, _cutoff):
        raise RuntimeError("db down")

    monkeypatch.setattr(analytics_cleanup, "get_connection", lambda: connection)
    monkeypatch.setattr(analytics_cleanup, "delete_expired_events",
                        failing_delete)

    try:
        analytics_cleanup.main(now=FIXED_NOW)
        raise AssertionError("main 必須重新拋出資料庫例外")
    except RuntimeError as exc:
        assert str(exc) == "db down"

    assert connection.committed is False
    assert connection.rolled_back is True
    assert connection.closed is True


def test_cleanup_main_defaults_to_utc_now(monkeypatch):
    connection = FakeConnection()
    calls = {}

    class FixedUtcNow(datetime):
        @classmethod
        def now(cls, tz=None):
            return FIXED_NOW

    def fake_delete(conn, cutoff):
        calls["cutoff"] = cutoff
        return 0

    monkeypatch.setattr(analytics_cleanup, "get_connection", lambda: connection)
    monkeypatch.setattr(analytics_cleanup, "delete_expired_events", fake_delete)
    monkeypatch.setattr(analytics_cleanup, "datetime", FixedUtcNow)

    analytics_cleanup.main()

    assert calls["cutoff"] == EXPECTED_CUTOFF
    assert calls["cutoff"].tzinfo is timezone.utc
