"""Flask 入口：組裝設定、安全標頭與 Blueprint；查詢流程在 query_service。"""

import hashlib
import logging
from pathlib import Path

from flask import Flask, request

import analytics_recorder
from config import Config
from routes import BLUEPRINTS

APP_ROOT = Path(__file__).resolve().parent
# 前端外殼檔案；任何一個內容改變，版本號就跟著改變，手機 PWA 會自動換新快取。
VERSIONED_ASSETS = (
    "static/app.js", "static/style.css", "templates/index.html",
    "templates/sw.js", "static/manifest.webmanifest",
    "static/vendor/leaflet/leaflet.js", "static/vendor/leaflet/leaflet.css",
    "static/vendor/chart.umd.min.js", "static/admin_analytics.js",
    "static/admin_analytics.css",
)


INSECURE_SECRET_KEYS = {None, "", "dev-only-change-me"}
# 只允許本站腳本；Cloudflare Web Analytics 由邊緣注入，需額外放行。
# 空位比例條使用 inline style 寬度，因此 style-src 保留 'unsafe-inline'。
CONTENT_SECURITY_POLICY = "; ".join((
    "default-src 'self'",
    "script-src 'self' https://static.cloudflareinsights.com",
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' data: https://*.tile.openstreetmap.org",
    "connect-src 'self' https://cloudflareinsights.com",
    "worker-src 'self'",
    "manifest-src 'self'",
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "frame-ancestors 'none'",
))
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "camera=(), geolocation=(self), microphone=(self)",
    "Strict-Transport-Security": "max-age=31536000",
}


def compute_asset_version(root=APP_ROOT, paths=VERSIONED_ASSETS):
    """以前端檔案內容雜湊產生版本號，取代手動維護的版本字串。"""
    digest = hashlib.sha256()
    for relative in paths:
        digest.update(relative.encode("utf-8"))
        digest.update((Path(root) / relative).read_bytes())
    return digest.hexdigest()[:12]


def create_app(test_config=None):
    """建立 Flask 應用，允許測試覆寫設定並回傳 app。"""
    app = Flask(__name__)
    app.logger.setLevel(logging.INFO)
    app.config.from_object(Config)
    if test_config:
        app.config.update(test_config)
    if not app.testing and app.config.get("SECRET_KEY") in INSECURE_SECRET_KEYS:
        raise RuntimeError("FLASK_SECRET_KEY 未設定；請在 .env 設定隨機長字串後再啟動")
    asset_version = compute_asset_version()

    @app.context_processor
    def inject_asset_version():
        return {"asset_version": asset_version}

    analytics_recorder.register_writers(app)

    @app.after_request
    def apply_security_and_admin_headers(response):
        """全站加上基本安全標頭；HTML 另加 CSP；/admin/ 回應保持唯讀且不可快取。"""
        for name, value in SECURITY_HEADERS.items():
            response.headers.setdefault(name, value)
        if response.mimetype == "text/html":
            response.headers.setdefault(
                "Content-Security-Policy", CONTENT_SECURITY_POLICY)
        if request.path.startswith("/admin/"):
            response.headers["Cache-Control"] = "no-store"
            response.headers["X-Robots-Tag"] = "noindex"
        return response

    for blueprint in BLUEPRINTS:
        app.register_blueprint(blueprint)
    return app


app = create_app()
