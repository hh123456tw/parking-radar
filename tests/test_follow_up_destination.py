"""追問沿用上一輪目的地：由後端規則決定，不依賴 Gemini 是否記得。"""

from datetime import datetime, timedelta, timezone

import pytest

import database
import query_service

NOW = datetime(2026, 10, 3, 12, 0, tzinfo=timezone.utc)
SESSION = {
    "destination": "臺北市信義區市府路1號",
    "district": "信義區",
    "destination_label": "臺北市政府（臺北市信義區市府路1號）",
    "destination_at": (NOW - timedelta(minutes=30)).isoformat(),
}


def empty_follow_up():
    """Gemini 解析「那晚上九點呢？」時沒有任何地點欄位的典型結果。"""
    return {"intent": "recommend", "original_destination": None, "address": None,
            "district": None, "arrival_time": None,
            "missing_fields": ["address"], "location_candidates": []}


def test_follow_up_without_location_reuses_recent_destination():
    parsed = query_service.apply_previous_destination(empty_follow_up(), SESSION, now=NOW)

    assert parsed["address"] == "臺北市信義區市府路1號"
    assert parsed["district"] == "信義區"
    assert parsed["destination_label"] == "臺北市政府（臺北市信義區市府路1號）"
    assert "address" not in parsed["missing_fields"]


def test_destination_older_than_two_hours_is_not_reused():
    """隔天或久之後的查詢不可默默套用舊目的地。"""
    stale = dict(SESSION, destination_at=(NOW - timedelta(hours=3)).isoformat())

    parsed = query_service.apply_previous_destination(empty_follow_up(), stale, now=NOW)

    assert parsed["address"] is None


@pytest.mark.parametrize("field, value", [
    ("address", "臺北市中正區北平西路3號"),
    ("district", "大安區"),
    ("original_destination", "京站"),
])
def test_new_location_in_message_always_wins(field, value):
    parsed = dict(empty_follow_up(), **{field: value})

    result = query_service.apply_previous_destination(parsed, SESSION, now=NOW)

    assert result.get(field) == value
    assert result.get("destination_label") is None


def test_no_previous_destination_leaves_query_untouched():
    parsed = query_service.apply_previous_destination(empty_follow_up(), {}, now=NOW)

    assert parsed["address"] is None
    assert parsed["missing_fields"] == ["address"]


@pytest.mark.parametrize("missing, message", [
    (["address"], "請說出目的地，例如：今晚九點去臺北市政府"),
    (["district"], "請說出目的地，例如：今晚九點去臺北市政府"),
    (["intent"], "還需要：查詢目的"),
    (["something_new"], "還需要：更多資訊"),
])
def test_missing_field_errors_are_chinese(missing, message):
    """錯誤訊息不可直接露出 address 等英文欄位名稱。"""
    parsed = {"missing_fields": missing, "address": None, "district": None,
              "arrival_time": None}

    with pytest.raises(ValueError) as error:
        query_service.validate_parsed_query(parsed, now=NOW)

    assert str(error.value) == message


class FakeConnection:
    def close(self):
        pass


def test_chat_follow_up_end_to_end_keeps_destination_and_saves_session(monkeypatch):
    """第一句成功後記下目的地與時間；追問時 Gemini 沒給地點也能沿用。"""
    monkeypatch.setattr(database, "get_connection", FakeConnection)
    monkeypatch.setattr(query_service, "geocode_address", lambda *_args: {
        "display_address": "臺北市政府, 1, 市府路, 西村里, 信義區, 臺北市, 11008, 臺灣",
        "latitude": 25.0375, "longitude": 121.5637})
    monkeypatch.setattr(query_service, "fetch_current_lots", lambda *_args: [{
        "lot_id": "TPE1", "lot_name": "A場", "district": "信義區",
        "address": "市府路", "operator_type": "民營停車場",
        "total_spaces": 100, "available_spaces": 40,
        "fee_info": "每小時30元", "service_time": "24小時",
        "fare_rules_json": None, "facility_type": "underground",
        "facility_source": "official", "latitude": 25.0376, "longitude": 121.5638,
        "captured_at": datetime.now(timezone.utc),
    }])
    replies = iter([
        dict(empty_follow_up(), address="臺北市信義區市府路1號", district="信義區",
             missing_fields=[]),
        empty_follow_up(),
    ])

    class Intent:
        def __init__(self, data):
            self.data = data

        def model_dump(self):
            return self.data

    monkeypatch.setattr(query_service, "parse_parking_query",
                        lambda *_args: Intent(next(replies)))
    import app as app_module
    client = app_module.create_app({
        "TESTING": True, "SECRET_KEY": "test", "AUTO_REFRESH_ENABLED": False,
        "OPENROUTESERVICE_API_KEY": ""}).test_client()

    first = client.post("/api/query", json={"mode": "chat", "message": "去臺北市政府"})
    follow_up = client.post("/api/query", json={"mode": "chat", "message": "那晚上九點呢？"})

    assert first.status_code == 200
    assert follow_up.status_code == 200
    assert follow_up.get_json()["destination"]["name"] == "臺北市政府"
    with client.session_transaction() as session:
        assert session["destination"] == "臺北市信義區市府路1號"
        assert "destination_at" in session
