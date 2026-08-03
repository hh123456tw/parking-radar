"""集中讀取環境變數與專題固定規則，避免設定散落在各模組。"""

import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    """提供 Flask、MySQL、Gemini、地址搜尋與分析所需設定。"""

    SECRET_KEY = os.getenv("FLASK_SECRET_KEY", "dev-only-change-me")
    MYSQL_HOST = os.getenv("MYSQL_HOST", "127.0.0.1")
    MYSQL_PORT = int(os.getenv("MYSQL_PORT", "3306"))
    MYSQL_USER = os.getenv("MYSQL_USER", "parking_app")
    MYSQL_PASSWORD = os.getenv("MYSQL_PASSWORD", "")
    MYSQL_DATABASE = os.getenv("MYSQL_DATABASE", "parking_hell")
    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
    GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash")
    NOMINATIM_USER_AGENT = os.getenv(
        "NOMINATIM_USER_AGENT", "parking-hell-radar-student-project/1.0"
    )
    SEARCH_RADIUS_M = 1500
    FRESHNESS_MINUTES = 45
    MIN_HISTORY_SAMPLES = 3
    HISTORY_LOOKBACK_DAYS = 30
