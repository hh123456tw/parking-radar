"""本地臺灣行事曆契約測試：所有測試不需真實網路。"""

import json
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

import calendar_service
from calendar_service import classify_arrival_day

TAIPEI = ZoneInfo("Asia/Taipei")


def write_calendar(tmp_path):
    rows = [
        {"date": "20261010", "week": "六", "isHoliday": True,
         "description": "國慶日"},
        {"date": "20260926", "week": "六", "isHoliday": False,
         "description": "補行上班日"},
        {"date": "20260823", "week": "日", "isHoliday": True,
         "description": ""},
    ]
    (tmp_path / "2026.json").write_text(
        json.dumps(rows, ensure_ascii=False), encoding="utf-8")


@pytest.mark.parametrize(("date", "kind", "label"), [
    ("2026-10-10T18:00:00+08:00", "holiday", "國定假日｜國慶日"),
    ("2026-09-26T18:00:00+08:00", "makeup_workday", "補班日"),
    ("2026-08-23T18:00:00+08:00", "weekend", "週末"),
    ("2026-08-24T18:00:00+08:00", "weekday", "平日"),
])
def test_classify_arrival_day_from_local_calendar(tmp_path, date, kind, label):
    write_calendar(tmp_path)

    result = classify_arrival_day(datetime.fromisoformat(date), tmp_path)

    assert (result["kind"], result["label"]) == (kind, label)
    assert result["source"] == "taiwan_calendar"


def test_missing_calendar_falls_back_to_weekday_or_weekend(tmp_path):
    """年度檔案不存在時，六日回傳週末、週一回傳平日，並標記 weekday_fallback。"""
    saturday = datetime(2026, 8, 22, 18, 0, tzinfo=TAIPEI)
    monday = datetime(2026, 8, 24, 18, 0, tzinfo=TAIPEI)

    weekend = classify_arrival_day(saturday, tmp_path)
    weekday = classify_arrival_day(monday, tmp_path)

    assert (weekend["kind"], weekend["label"]) == ("weekend", "週末")
    assert weekend["source"] == "weekday_fallback"
    assert (weekday["kind"], weekday["label"]) == ("weekday", "平日")
    assert weekday["source"] == "weekday_fallback"


def test_classify_arrival_day_requires_timezone(tmp_path):
    """缺少時區的抵達時間必須明確拒絕，避免跨日誤判。"""
    with pytest.raises(ValueError, match="抵達時間必須包含時區"):
        classify_arrival_day(datetime(2026, 10, 10, 18, 0), tmp_path)


class FakeResponse:
    """紀錄 raise_for_status 次數的假 HTTP 回應。"""

    def __init__(self, payload):
        self.payload = payload
        self.raise_calls = 0

    def raise_for_status(self):
        self.raise_calls += 1

    def json(self):
        return self.payload


def test_sync_calendars_requests_exact_url_and_atomically_writes(tmp_path, monkeypatch):
    """sync 只抓指定年度、帶逾時、檢查 HTTP 狀態並以暫存檔原子換檔。"""
    captured = {}
    payload = [
        {"date": "20261010", "week": "六", "isHoliday": True,
         "description": "國慶日"},
    ]
    response = FakeResponse(payload)

    def fake_get(url, **kwargs):
        captured["url"] = url
        captured["timeout"] = kwargs.get("timeout")
        return response

    monkeypatch.setattr(calendar_service.requests, "get", fake_get)

    written = calendar_service.sync_calendars([2026], tmp_path, timeout=7)

    assert captured["url"] == \
        "https://cdn.jsdelivr.net/gh/ruyut/TaiwanCalendar/data/2026.json"
    assert captured["timeout"] == 7
    assert response.raise_calls == 1
    assert (tmp_path / "2026.json").read_text(encoding="utf-8") == \
        json.dumps(payload, ensure_ascii=False)
    assert not (tmp_path / "2026.json.tmp").exists()
    assert written == [tmp_path / "2026.json"]


def test_sync_calendars_rejects_non_list_payload(tmp_path, monkeypatch):
    """行事曆 JSON 不是陣列時必須明確拋錯，不得寫入壞檔。"""
    monkeypatch.setattr(
        calendar_service.requests, "get",
        lambda *_args, **_kwargs: FakeResponse({"not": "a list"}))

    with pytest.raises(ValueError, match="必須是陣列"):
        calendar_service.sync_calendars([2026], tmp_path)


class FixedNow(datetime):
    """固定系統時間的 datetime 子類別，讓預設年度測試不受執行日影響。"""

    @classmethod
    def now(cls, tz=None):
        return datetime(2026, 5, 1, tzinfo=tz)


def test_sync_calendars_defaults_to_current_and_next_taipei_year(tmp_path, monkeypatch):
    """省略年度時，預設下載台北時區的今年與明年行事曆。"""
    requested = []
    monkeypatch.setattr(calendar_service, "datetime", FixedNow)

    def fake_get(url, **_kwargs):
        requested.append(url)
        return FakeResponse([])

    monkeypatch.setattr(calendar_service.requests, "get", fake_get)

    written = calendar_service.sync_calendars(calendar_dir=tmp_path)

    base = "https://cdn.jsdelivr.net/gh/ruyut/TaiwanCalendar/data"
    assert requested == [f"{base}/2026.json", f"{base}/2027.json"]
    assert written == [tmp_path / "2026.json", tmp_path / "2027.json"]


def test_empty_calendar_file_falls_back(tmp_path):
    """行事曆檔案存在但內容為空陣列時，視同資料不可用並沿用 fallback。"""
    (tmp_path / "2026.json").write_text("[]", encoding="utf-8")

    saturday = classify_arrival_day(
        datetime(2026, 8, 22, 18, 0, tzinfo=TAIPEI), tmp_path)
    monday = classify_arrival_day(
        datetime(2026, 8, 24, 18, 0, tzinfo=TAIPEI), tmp_path)

    assert (saturday["kind"], saturday["label"], saturday["source"]) == \
        ("weekend", "週末", "weekday_fallback")
    assert (monday["kind"], monday["label"], monday["source"]) == \
        ("weekday", "平日", "weekday_fallback")


def test_missing_saturday_row_is_weekend_not_weekday(tmp_path):
    """檔案存在但缺少抵達日資料列時，六日比照 fallback 判為週末、週一仍為平日。"""
    write_calendar(tmp_path)

    saturday = classify_arrival_day(
        datetime(2026, 8, 22, 18, 0, tzinfo=TAIPEI), tmp_path)
    monday = classify_arrival_day(
        datetime(2026, 8, 24, 18, 0, tzinfo=TAIPEI), tmp_path)

    assert (saturday["kind"], saturday["label"], saturday["source"]) == \
        ("weekend", "週末", "weekday_fallback")
    assert (monday["kind"], monday["label"], monday["source"]) == \
        ("weekday", "平日", "taiwan_calendar")