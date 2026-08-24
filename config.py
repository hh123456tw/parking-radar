"""集中讀取環境變數與專題固定規則，避免設定散落在各模組。"""

import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    """提供 Flask、MySQL、Gemini、地址搜尋與分析所需設定。"""

    SECRET_KEY = os.getenv("FLASK_SECRET_KEY", "dev-only-change-me")
    MYSQL_HOST = os.getenv("MYSQL_HOST")
    MYSQL_PORT = int(os.getenv("MYSQL_PORT", "3306"))
    MYSQL_USER = os.getenv("MYSQL_USER")
    MYSQL_PASSWORD = os.getenv("MYSQL_PASSWORD")
    MYSQL_DATABASE = os.getenv("MYSQL_DATABASE")
    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
    GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")
    # 主模型高流量時使用同為 Lite 的免費備援，避免整個對話入口失效。
    GEMINI_FALLBACK_MODEL = "gemini-3.1-flash-lite"
    GEMINI_TIMEOUT_MS = 12_000
    NOMINATIM_USER_AGENT = os.getenv(
        "NOMINATIM_USER_AGENT", "parking-hell-radar-student-project/1.0"
    )
    GEOCODER_TIMEOUT_SECONDS = 4
    OPENROUTESERVICE_API_KEY = os.getenv("OPENROUTESERVICE_API_KEY", "")
    WALKING_ROUTE_TIMEOUT_SECONDS = 3
    WALKING_ROUTE_CANDIDATE_LIMIT = 15
    SEARCH_RADIUS_M = 1500
    FRESHNESS_MINUTES = 45
    ON_DEMAND_FETCH_TIMEOUT_SECONDS = 5
    AUTO_REFRESH_ENABLED = True
    MIN_HISTORY_SAMPLES = 3
    HISTORY_LOOKBACK_DAYS = 7
    # 匿名分析：開關、HMAC 秘密、保留天數與樣本下限。
    ANALYTICS_ENABLED = os.getenv("ANALYTICS_ENABLED", "1") == "1"
    # 團隊測試可設為 0 自動啟用；正式公開時保留原本的明確同意介面。
    ANALYTICS_REQUIRE_CONSENT = \
        os.getenv("ANALYTICS_REQUIRE_CONSENT", "1") == "1"
    ANALYTICS_HMAC_SECRET = os.getenv("ANALYTICS_HMAC_SECRET", "")
    ANALYTICS_RETENTION_DAYS = 90
    ANALYTICS_SEGMENT_MIN_DEVICES = 5
    DEPLOY_VERSION = os.getenv("DEPLOY_VERSION", "unknown")
