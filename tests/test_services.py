"""外部服務契約測試：以 fake 隔離 Gemini 與 Nominatim 真實網路。"""

import json

import pytest

import ai_service
import geocoder
from ai_service import IntentServiceError, ParkingIntent, parse_parking_query
from config import Config


class JsonResponse:
    """提供 Nominatim 測試所需的最小 HTTP response。"""

    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class CommitConnection:
    """記錄地址成功寫入快取後是否提交交易。"""

    def __init__(self):
        self.commits = 0

    def commit(self):
        self.commits += 1


def valid_intent_json():
    """回傳完整的 Gemini 結構化意圖 fixture。"""
    return json.dumps({
        "intent": "compare",
        "original_destination": "臺北市政府",
        "address": "臺北市信義區市府路1號",
        "district": "信義區",
        "arrival_time": "2026-08-08T18:00:00+08:00",
        "missing_fields": [],
    }, ensure_ascii=False)


def test_intent_schema_rejects_non_taipei_district():
    """Gemini 即使輸出合法 JSON，也不能把新北行政區交給後端。"""
    with pytest.raises(ValueError, match="只支援臺北市十二行政區"):
        ParkingIntent(
            intent="recommend", original_destination="板橋車站",
            address=None, district="板橋區", arrival_time=None, missing_fields=[])


def test_gemini_request_includes_context_model_and_json_schema():
    """移除上一輪狀態、模型或 schema 時，此契約測試必須失敗。"""
    captured = {}
    response = type("Response", (), {"text": valid_intent_json()})()

    class RecordingModels:
        def generate_content(self, **kwargs):
            captured.update(kwargs)
            return response

    client = type("Client", (), {"models": RecordingModels()})()

    result = parse_parking_query(
        "那週末呢？", {"destination": "臺北市政府", "district": "信義區"}, client)

    assert result.intent == "compare"
    assert captured["model"] == Config.GEMINI_MODEL
    assert "臺北市政府" in captured["contents"]
    assert "上一輪狀態" in captured["contents"]
    assert captured["config"].response_mime_type == "application/json"
    schema = captured["config"].response_json_schema
    assert {"intent", "address", "district", "arrival_time"} <= set(schema["properties"])


def test_default_gemini_client_has_bounded_request_timeout(monkeypatch):
    """外部模型未回應時，SDK 必須在固定時間內結束等待。"""
    captured = {}
    response = type("Response", (), {"text": valid_intent_json()})()
    models = type(
        "Models", (), {"generate_content": lambda self, **_kwargs: response})()
    fake_client = type("Client", (), {"models": models})()

    def fake_client_factory(**kwargs):
        captured.update(kwargs)
        return fake_client

    monkeypatch.setattr(ai_service.genai, "Client", fake_client_factory)
    monkeypatch.setattr(ai_service.Config, "GEMINI_API_KEY", "test-key")

    result = parse_parking_query("我要去信義區")

    assert result.district == "信義區"
    assert captured["http_options"].timeout == 12_000
    assert captured["http_options"].retry_options.attempts == 1


def test_gemini_without_api_key_uses_manual_fallback(monkeypatch):
    """未設定 API key 時不得建立 client，應立即回傳可理解的錯誤。"""
    monkeypatch.setattr(ai_service.Config, "GEMINI_API_KEY", "")

    with pytest.raises(IntentServiceError, match="Gemini 尚未設定"):
        parse_parking_query("我要去市政府")


def test_gemini_malformed_output_becomes_service_error():
    """模型回傳非 JSON 時，不得讓 Pydantic 例外洩漏到 Flask。"""
    response = type("Response", (), {"text": "not-json"})()
    models = type(
        "Models", (), {"generate_content": lambda self, **_kwargs: response})()
    client = type("Client", (), {"models": models})()

    with pytest.raises(IntentServiceError, match="目前無法理解問題"):
        parse_parking_query("我要去市政府", client=client)


def test_gemini_high_demand_has_clear_retry_message():
    """模型高流量 503 應說明服務忙碌，不可誤導成使用者問法錯誤。"""
    def raise_busy(**_kwargs):
        raise ai_service.errors.ServerError(
            503, {"error": {"message": "high demand"}})

    models = type("Models", (), {"generate_content": lambda self, **kwargs:
                  raise_busy(**kwargs)})()
    client = type("Client", (), {"models": models})()

    with pytest.raises(IntentServiceError, match="Gemini目前忙碌"):
        parse_parking_query("我要去信義區", client=client)


def test_normalize_address_and_cache_hit_avoid_http(monkeypatch):
    """地址先正規化；快取命中時不得再消耗公共 Nominatim 請求。"""
    assert geocoder.normalize_address(" 信義區 忠孝東路五段 7 號 ") == \
        "臺北市信義區忠孝東路五段7號"
    assert geocoder.normalize_address("台北市中山區長春路17號") == \
        "臺北市中山區長春路17號"
    cached = {
        "normalized_address": "臺北市信義區市府路1號", "display_address": "臺北市政府",
        "latitude": 25.0375, "longitude": 121.5637,
    }
    monkeypatch.setattr(geocoder, "get_cached_geocode", lambda *_args: cached)

    result = geocoder.geocode_address(
        "市府路1號", object(),
        http_get=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("快取命中不應呼叫 HTTP")),
    )

    assert result == cached


def test_nominatim_request_uses_policy_headers_and_saves_cache(monkeypatch):
    """Nominatim 請求必須限制一筆、臺灣範圍、逾時並帶可辨識 User-Agent。"""
    captured = {}
    saved = []
    connection = CommitConnection()
    monkeypatch.setattr(geocoder, "get_cached_geocode", lambda *_args: None)
    monkeypatch.setattr(geocoder, "save_cached_geocode",
                        lambda _connection, row: saved.append(row))
    monkeypatch.setattr(geocoder, "_respect_rate_limit", lambda: None)

    def fake_get(url, **kwargs):
        captured.update(url=url, **kwargs)
        return JsonResponse([{
            "display_name": "臺北市政府, 信義區, 臺北市",
            "lat": "25.0375", "lon": "121.5637",
        }])

    result = geocoder.geocode_address("市府路1號", connection, http_get=fake_get)

    assert captured["url"] == geocoder.NOMINATIM_URL
    assert captured["params"] == {
        "q": "臺北市市府路1號", "format": "jsonv2", "limit": 1, "countrycodes": "tw"}
    assert captured["headers"] == {"User-Agent": Config.NOMINATIM_USER_AGENT}
    assert captured["timeout"] == 8
    assert result["latitude"] == 25.0375
    assert saved[0]["normalized_address"] == "臺北市市府路1號"
    assert connection.commits == 1


@pytest.mark.parametrize("payload", [
    [],
    [{"display_name": "新北市板橋區", "lat": "25.01", "lon": "121.46"}],
])
def test_nominatim_empty_or_non_taipei_result_returns_none(monkeypatch, payload):
    """查無資料或結果不在臺北市時，不得寫入地址快取。"""
    monkeypatch.setattr(geocoder, "get_cached_geocode", lambda *_args: None)
    monkeypatch.setattr(geocoder, "_respect_rate_limit", lambda: None)
    monkeypatch.setattr(
        geocoder, "save_cached_geocode",
        lambda *_args: (_ for _ in ()).throw(AssertionError("不應寫入快取")),
    )

    result = geocoder.geocode_address(
        "測試地址", object(), http_get=lambda *_args, **_kwargs: JsonResponse(payload))

    assert result is None


def test_nominatim_rate_limit_waits_for_remaining_second(monkeypatch):
    """前一次請求未滿一秒時，只等待剩餘時間而非額外等待一秒。"""
    monotonic_values = iter([100.25, 101.0])
    sleeps = []
    monkeypatch.setattr(geocoder, "_last_request_at", 100.0)
    monkeypatch.setattr(geocoder.time, "monotonic", lambda: next(monotonic_values))
    monkeypatch.setattr(geocoder.time, "sleep", lambda seconds: sleeps.append(seconds))

    geocoder._respect_rate_limit()

    assert sleeps == [0.75]
    assert geocoder._last_request_at == 101.0


def test_nominatim_rate_limit_does_not_sleep_after_one_second(monkeypatch):
    """距前次請求已超過一秒時不得產生不必要延遲。"""
    monotonic_values = iter([102.0, 102.1])
    sleeps = []
    monkeypatch.setattr(geocoder, "_last_request_at", 100.0)
    monkeypatch.setattr(geocoder.time, "monotonic", lambda: next(monotonic_values))
    monkeypatch.setattr(geocoder.time, "sleep", lambda seconds: sleeps.append(seconds))

    geocoder._respect_rate_limit()

    assert sleeps == []
    assert geocoder._last_request_at == 102.1
