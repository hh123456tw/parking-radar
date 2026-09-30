"""HTTP 路由；依對外用途分成頁面、停車查詢、瀏覽分析與管理四個 Blueprint。"""

from routes.admin import bp as admin_bp
from routes.analytics import bp as analytics_bp
from routes.pages import bp as pages_bp
from routes.parking import bp as parking_bp

BLUEPRINTS = (pages_bp, parking_bp, analytics_bp, admin_bp)
