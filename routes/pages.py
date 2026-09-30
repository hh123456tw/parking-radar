"""公開頁面：健康檢查、主頁與 Service Worker。"""

from flask import Blueprint, current_app, jsonify, make_response, render_template

bp = Blueprint("pages", __name__)


@bp.get("/health")
def health():
    """回傳不依賴外部服務的程序健康狀態。"""
    return jsonify(status="ok")


@bp.get("/")
def index():
    """顯示唯一主頁，資料由前端呼叫 JSON API 載入。"""
    return render_template(
        "index.html",
        analytics_require_consent=current_app.config.get(
            "ANALYTICS_REQUIRE_CONSENT", True),
    )


@bp.get("/sw.js")
def service_worker():
    """由根路徑提供服務器腳本並帶入內容版本；不可快取，讓瀏覽器每次檢查更新。"""
    response = make_response(render_template("sw.js"))
    response.headers["Content-Type"] = "application/javascript; charset=utf-8"
    response.headers["Cache-Control"] = "no-cache"
    return response
