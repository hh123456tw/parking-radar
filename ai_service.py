"""Gemini 僅把停車問題轉成固定結構，不產生 SQL、格數或推薦。"""

import json
from datetime import datetime
from typing import Literal
from zoneinfo import ZoneInfo
import httpx
from google import genai
from google.genai import errors, types
from pydantic import BaseModel, Field, field_validator, model_validator
from config import Config
from city_config import CITIES, normalize_city, validate_city_district

# app.py 的手動解析器在 Task 6 前仍使用這個相容別名；來源統一為 city_config。
TAIPEI_DISTRICTS = set(CITIES["taipei"].districts)


def _infer_city_for_district(district):
    """行政區唯一屬於某一城市時回傳其代碼；跨市同名或未知時拋錯。"""
    owners = [code for code, definition in CITIES.items()
              if district in definition.districts]
    if len(owners) == 1:
        return owners[0]
    if not owners:
        raise ValueError(f"不支援行政區 {district}")
    raise ValueError(f"{district} 同時存在於多個城市，請一併提供 city")


def _validate_city_district_pair(city, district):
    """city 與 district 一起驗證；只有 district 時可推論唯一擁有城市。"""
    if district:
        district = str(district).strip()
        if not district:
            district = None
    if not district:
        return city, None
    if city:
        city = normalize_city(city)
        validate_city_district(city, district)
    else:
        city = _infer_city_for_district(district)
    return city, district


class IntentServiceError(RuntimeError):
    """表示 Gemini 未設定、逾時、回應無效或服務失敗。"""


class LocationCandidate(BaseModel):
    """Gemini 提出的雙北地點候選；地址仍須經 Nominatim 驗證。"""
    name: str
    address: str
    city: str | None = None
    district: str | None = None

    @field_validator("city")
    @classmethod
    def validate_city(cls, value):
        return normalize_city(value)

    @model_validator(mode="after")
    def validate_city_district(self):
        self.city, self.district = _validate_city_district_pair(
            self.city, self.district)
        return self


class ParkingIntent(BaseModel):
    """限定 Gemini 能交給後端的欄位與三種停車意圖。"""
    intent: Literal["recommend", "history", "compare"]
    original_destination: str | None
    address: str | None
    city: str | None = None
    district: str | None
    arrival_time: datetime | None
    missing_fields: list[str]
    location_candidates: list[LocationCandidate] = Field(default_factory=list)

    @field_validator("city")
    @classmethod
    def validate_city(cls, value):
        return normalize_city(value)

    @model_validator(mode="after")
    def validate_city_district(self):
        self.city, self.district = _validate_city_district_pair(
            self.city, self.district)
        return self


def _prompt(message, context):
    """建立窄範圍指令，明確禁止模型虛構停車資料。"""
    now = datetime.now(ZoneInfo("Asia/Taipei")).isoformat()
    return f"""你是停車查詢欄位解析器，只能判斷 recommend、history、compare。
目前臺北時間：{now}。只接受臺北市與新北市地址；city 必須是 taipei 或 new_taipei。
不得提供停車場、空位、距離、分數、SQL 或一般聊天答案。
必要資訊不足時列入 missing_fields，不得猜測。
original_destination 只是保留使用者原話的選填欄位，不得列入 missing_fields。
若使用者提供模糊、口語、分店或多據點地標，請保留 original_destination，
address 可保留原地標名稱。只要原話沒有完整門牌，就必須在
location_candidates 盡量列出最多 3 個不同實體據點，不得自行假設某處是總部。
連鎖商店、百貨、學校、機構或組織未指定分館／分店／院所／校區時，尤其要
分別列出可能據點。每個候選必須包含可供地址服務驗證的地面完整門牌與行政區，
地址不要包含樓層。只有確定臺北市僅有一處時才可只提供 1 個；純行政區查詢
則回傳空陣列。
地標不唯一時列出最多 3 個候選，不得虛構停車資料或座標。
若能辨識地標所在行政區，district 必須填入，協助地址服務排除同名地點。
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
        models = [Config.GEMINI_MODEL]
        if Config.GEMINI_FALLBACK_MODEL not in models:
            models.append(Config.GEMINI_FALLBACK_MODEL)

        last_busy_error = None
        for model in models:
            try:
                response = client.models.generate_content(
                    model=model,
                    contents=_prompt(message, context),
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        response_json_schema=ParkingIntent.model_json_schema(),
                    ),
                )
                return ParkingIntent.model_validate_json(response.text)
            except (errors.ServerError, httpx.TimeoutException) as exc:
                # 服務端高流量或讀取逾時才切換模型；格式或使用者輸入
                # 錯誤不隱藏，避免把真正的資料契約問題誤判成忙碌。
                last_busy_error = exc
        raise IntentServiceError(
            "Gemini目前忙碌，請稍後重試或改用手動查詢"
        ) from last_busy_error
    except IntentServiceError:
        raise
    except Exception as exc:
        raise IntentServiceError("目前無法理解問題，請改用手動查詢") from exc
