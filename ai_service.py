"""Gemini 僅把停車問題轉成固定結構，不產生 SQL、格數或推薦。"""

import json
from datetime import datetime
from typing import Literal
from zoneinfo import ZoneInfo
from google import genai
from google.genai import errors, types
from pydantic import BaseModel, field_validator
from config import Config

TAIPEI_DISTRICTS = {
    "松山區", "信義區", "大安區", "中山區", "中正區", "大同區",
    "萬華區", "文山區", "南港區", "內湖區", "士林區", "北投區",
}


class IntentServiceError(RuntimeError):
    """表示 Gemini 未設定、逾時、回應無效或服務失敗。"""


class ParkingIntent(BaseModel):
    """限定 Gemini 能交給後端的欄位與三種停車意圖。"""
    intent: Literal["recommend", "history", "compare"]
    original_destination: str | None
    address: str | None
    district: str | None
    arrival_time: datetime | None
    missing_fields: list[str]

    @field_validator("district")
    @classmethod
    def validate_district(cls, value):
        if value is not None and value not in TAIPEI_DISTRICTS:
            raise ValueError("只支援臺北市十二行政區")
        return value


def _prompt(message, context):
    """建立窄範圍指令，明確禁止模型虛構停車資料。"""
    now = datetime.now(ZoneInfo("Asia/Taipei")).isoformat()
    return f"""你是停車查詢欄位解析器，只能判斷 recommend、history、compare。
目前臺北時間：{now}。只接受臺北市地址與十二行政區。
不得提供停車場、空位、距離、分數、SQL 或一般聊天答案。
必要資訊不足時列入 missing_fields，不得猜測。
original_destination 只是保留使用者原話的選填欄位，不得列入 missing_fields。
若使用者提供臺北市地標但沒有完整地址，請保留 original_destination，
address 可回傳該地標名稱，讓後端地址服務解析。
若使用者沒有提抵達時間，arrival_time 請回傳 null，且不要把 arrival_time
列入 missing_fields；後端會自動使用 Asia/Taipei 的現在時間。
上一輪狀態：{json.dumps(context or {}, ensure_ascii=False, default=str)}
使用者：{message}"""


def parse_parking_query(message, context=None, client=None):
    """呼叫 Gemini 結構化輸出並驗證為 ParkingIntent。"""
    if client is None and not Config.GEMINI_API_KEY:
        raise IntentServiceError("Gemini 尚未設定")
    try:
        client = client or genai.Client(
            api_key=Config.GEMINI_API_KEY,
            http_options=types.HttpOptions(
                timeout=Config.GEMINI_TIMEOUT_MS,
                retry_options=types.HttpRetryOptions(attempts=1),
            ),
        )
        response = client.models.generate_content(
            model=Config.GEMINI_MODEL,
            contents=_prompt(message, context),
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_json_schema=ParkingIntent.model_json_schema(),
            ),
        )
        return ParkingIntent.model_validate_json(response.text)
    except errors.ServerError as exc:
        raise IntentServiceError(
            "Gemini目前忙碌，請稍後重試或改用手動查詢") from exc
    except Exception as exc:
        raise IntentServiceError("目前無法理解問題，請改用手動查詢") from exc
