"""安全強化回歸測試：秘密金鑰、Cookie、安全標頭與輸入長度。"""

import pytest

import app as app_module


def test_production_app_refuses_to_start_without_secret_key():
    """正式模式缺少或使用公開預設金鑰時必須拒絕啟動，避免 session 被偽造。"""
    for key in (None, "", "dev-only-change-me"):
        with pytest.raises(RuntimeError, match="FLASK_SECRET_KEY"):
            app_module.create_app({"TESTING": False, "SECRET_KEY": key})


def test_session_cookie_is_secure_and_same_site_lax():
    app = app_module.create_app({"TESTING": True, "SECRET_KEY": "test"})

    assert app.config["SESSION_COOKIE_SECURE"] is True
    assert app.config["SESSION_COOKIE_SAMESITE"] == "Lax"
    assert app.config["SESSION_COOKIE_HTTPONLY"] is True


def test_html_pages_send_csp_and_security_headers():
    client = app_module.create_app({"TESTING": True, "SECRET_KEY": "test"}).test_client()

    response = client.get("/")

    csp = response.headers["Content-Security-Policy"]
    assert "script-src 'self' https://static.cloudflareinsights.com" in csp
    assert "frame-ancestors 'none'" in csp
    assert "unsafe-eval" not in csp
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["Strict-Transport-Security"].startswith("max-age=")


def test_json_responses_get_base_headers_without_csp():
    client = app_module.create_app({"TESTING": True, "SECRET_KEY": "test"}).test_client()

    response = client.get("/health")

    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert "Content-Security-Policy" not in response.headers


def test_overlong_chat_message_is_rejected_before_gemini(monkeypatch):
    """超長聊天內容直接回 400，不呼叫 Gemini 消耗額度。"""
    called = []
    monkeypatch.setattr(app_module, "parse_parking_query",
                        lambda *args: called.append(args))
    client = app_module.create_app({"TESTING": True, "SECRET_KEY": "test"}).test_client()

    response = client.post("/api/query", json={"mode": "chat", "message": "台" * 201})

    assert response.status_code == 400
    assert "200 字" in response.get_json()["error"]
    assert called == []


def test_oversized_request_body_is_rejected():
    client = app_module.create_app({"TESTING": True, "SECRET_KEY": "test"}).test_client()

    response = client.post("/api/query", data="x" * (17 * 1024),
                           content_type="application/json")

    assert response.status_code == 413


def test_nginx_rate_limits_paid_query_api_by_real_client_ip():
    """查詢端點以 Cloudflare 提供的真實 IP 限流，超過時回傳前端可讀的 JSON。"""
    from pathlib import Path

    site = Path("deploy/nginx-parking-radar.conf").read_text(encoding="utf-8")
    http = Path("deploy/nginx-parking-radar-log-format.conf").read_text(encoding="utf-8")
    query_block = site.split("location = /api/query", 1)[1].split("}", 1)[0]

    assert "limit_req zone=parking_query" in query_block
    assert "limit_req_zone $binary_remote_addr zone=parking_query" in http
    assert "real_ip_header CF-Connecting-IP;" in http
    assert http.count("set_real_ip_from") >= 15
    assert "return 429 '{\"error\":" in site
