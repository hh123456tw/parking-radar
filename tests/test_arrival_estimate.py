"""抵達時段空位預估：同日別、抵達時刻前後 30 分鐘的歷史中位數與常見範圍。"""

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

import database
import query_service
from analysis import estimate_arrival_availability

TAIPEI = ZoneInfo("Asia/Taipei")
# 2026-09-21 ~ 25 為週一到週五，26、27 為週末。
WEEKDAYS = [date(2026, 9, day) for day in range(21, 26)]
WEEKEND = [date(2026, 9, 26), date(2026, 9, 27)]


def weekday_group(day):
    return "weekday" if day.weekday() < 5 else "weekend"


def snapshot(day, hour, minute, available, total=100):
    """以臺北時間建立快照，再轉成資料庫使用的 naive UTC。"""
    local = datetime(day.year, day.month, day.day, hour, minute, tzinfo=TAIPEI)
    captured = local.astimezone(timezone.utc).replace(tzinfo=None)
    return {"lot_id": "TPE1", "available_spaces": available,
            "captured_at": captured, "total_spaces": total}


def arrival(day, hour, minute=0):
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=TAIPEI)


def test_uses_same_day_group_within_thirty_minutes():
    """只採用同日別、抵達時刻前後 30 分鐘的樣本，其他時段與週末不混入。"""
    rows = []
    for offset, day in enumerate(WEEKDAYS):
        for minute in (40, 50):
            rows.append(snapshot(day, 17, minute, 30 + offset))
        rows.append(snapshot(day, 18, 15, 30 + offset))
        rows.append(snapshot(day, 19, 30, 99))  # 超出 30 分鐘
    for day in WEEKEND:
        rows.append(snapshot(day, 18, 0, 5))  # 不同日別

    result = estimate_arrival_availability(
        rows, arrival(date(2026, 10, 2), 18), weekday_group)

    assert result["status"] == "ok"
    assert result["sample_count"] == 15
    assert result["day_count"] == 5
    assert result["median"] == 32
    assert (result["low"], result["high"]) == (31, 33)
    assert result["day_group"] == "weekday"
    assert result["time_label"] == "18:00"


def test_window_wraps_around_midnight():
    """00:10 抵達要納入前一晚 23:50 的樣本。"""
    rows = [snapshot(day, 23, 50, 40) for day in WEEKDAYS[:4]]
    rows += [snapshot(day, 0, 20, 44) for day in WEEKDAYS[:4]]

    result = estimate_arrival_availability(
        rows, arrival(date(2026, 10, 1), 0, 10), weekday_group)

    assert result["status"] == "ok"
    assert result["sample_count"] == 8


def test_holiday_dates_follow_the_classifier_not_the_weekday():
    """國定假日即使落在週一，也依分類器歸入假日組，不污染平日統計。"""
    holiday = date(2026, 9, 28)  # 週一，教師節

    def calendar_group(day):
        return "weekend" if day == holiday or day.weekday() >= 5 else "weekday"

    rows = [snapshot(day, 18, 0, 50) for day in WEEKDAYS]
    rows += [snapshot(day, 18, 15, 50) for day in WEEKDAYS]
    rows += [snapshot(holiday, 18, 0, 2), snapshot(holiday, 18, 15, 2)]

    result = estimate_arrival_availability(
        rows, arrival(date(2026, 10, 1), 18), calendar_group)

    assert result["median"] == 50
    assert result["sample_count"] == 10


@pytest.mark.parametrize("days, per_day", [(WEEKDAYS[:1], 10), (WEEKDAYS, 1)])
def test_too_few_samples_or_days_is_insufficient(days, per_day):
    """少於 8 筆或少於 2 天時不給數字，避免用單一天的偶然狀況誤導使用者。"""
    rows = [snapshot(day, 18, minute, 20)
            for day in days for minute in range(0, per_day * 3, 3)]

    result = estimate_arrival_availability(
        rows, arrival(date(2026, 10, 1), 18), weekday_group)

    assert result["status"] == "insufficient"
    assert "median" not in result


def test_invalid_official_values_are_ignored():
    rows = [snapshot(day, 18, 0, 30) for day in WEEKDAYS]
    rows += [snapshot(day, 18, 15, 30) for day in WEEKDAYS]
    rows += [snapshot(day, 18, 5, -9) for day in WEEKDAYS]

    result = estimate_arrival_availability(
        rows, arrival(date(2026, 10, 1), 18), weekday_group)

    assert result["sample_count"] == 10


class FakeConnection:
    def close(self):
        pass


def test_estimates_attach_only_for_arrivals_at_least_thirty_minutes_away(monkeypatch):
    """馬上出發時即時空位就是最好的答案；只有之後才抵達才查歷史並附上預估。"""
    now = datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc)
    calls = []

    def history(_connection, lot_ids, start_utc, end_utc):
        calls.append((tuple(lot_ids), start_utc, end_utc))
        rows = [snapshot(day, 18, 0, 25) for day in WEEKDAYS]
        return rows + [snapshot(day, 18, 15, 35) for day in WEEKDAYS]

    monkeypatch.setattr(query_service, "fetch_matching_history", history)
    soon = [{"lot_id": "TPE1"}]
    query_service.attach_arrival_estimates(
        FakeConnection(), soon, now + timedelta(minutes=20), now=now)
    assert calls == []
    assert "arrival_estimate" not in soon[0]

    later = [{"lot_id": "TPE1"}]
    query_service.attach_arrival_estimates(
        FakeConnection(), later, arrival(date(2026, 10, 1), 18), now=now)

    assert calls[0][0] == ("TPE1",)
    assert calls[0][2] - calls[0][1] == timedelta(days=7)
    assert later[0]["arrival_estimate"]["status"] == "ok"
    assert later[0]["arrival_estimate"]["median"] == 30


def test_query_api_returns_estimate_on_primary_cards(monkeypatch):
    """API 的首選卡片帶出預估欄位，前端不需另外呼叫。"""
    monkeypatch.setattr(database, "get_connection", FakeConnection)
    monkeypatch.setattr(query_service, "fetch_current_lots", lambda *_args: [{
        "lot_id": "TPE1", "lot_name": "A場", "district": "信義區",
        "address": "市府路", "operator_type": "民營停車場",
        "total_spaces": 100, "available_spaces": 40,
        "fee_info": "每小時30元", "service_time": "24小時",
        "fare_rules_json": None, "facility_type": "underground",
        "facility_source": "official", "latitude": 25.0376, "longitude": 121.5638,
        "captured_at": datetime.now(timezone.utc),
    }])
    monkeypatch.setattr(query_service, "fetch_matching_history", lambda *_args: [
        snapshot(day, 18, minute, 20) for day in WEEKDAYS for minute in (0, 15)])
    client = query_service_client()
    future = (datetime.now(TAIPEI) + timedelta(days=1)).replace(
        hour=18, minute=0, second=0, microsecond=0)

    response = client.post("/api/query", json={
        "mode": "manual", "district": "信義區", "arrival_time": future.isoformat()})

    estimate = response.get_json()["recommendations"][0]["arrival_estimate"]
    assert estimate["time_label"] == "18:00"
    assert estimate["status"] in {"ok", "insufficient"}


def query_service_client():
    import app as app_module
    return app_module.create_app({
        "TESTING": True, "SECRET_KEY": "test", "AUTO_REFRESH_ENABLED": False,
        "OPENROUTESERVICE_API_KEY": "",
    }).test_client()


def test_estimate_failure_never_breaks_the_query(monkeypatch):
    """預估只是附加資訊；歷史查詢出錯時照常回傳推薦，只是不附預估。"""
    monkeypatch.setattr(database, "get_connection", FakeConnection)
    monkeypatch.setattr(query_service, "fetch_current_lots", lambda *_args: [{
        "lot_id": "TPE1", "lot_name": "A場", "district": "信義區",
        "address": "市府路", "operator_type": "民營停車場",
        "total_spaces": 100, "available_spaces": 40,
        "fee_info": "每小時30元", "service_time": "24小時",
        "fare_rules_json": None, "facility_type": "underground",
        "facility_source": "official", "latitude": 25.0376, "longitude": 121.5638,
        "captured_at": datetime.now(timezone.utc),
    }])

    def broken_history(*_args):
        raise RuntimeError("history table unavailable")

    monkeypatch.setattr(query_service, "fetch_matching_history", broken_history)
    future = (datetime.now(TAIPEI) + timedelta(days=1)).replace(
        hour=18, minute=0, second=0, microsecond=0)

    response = query_service_client().post("/api/query", json={
        "mode": "manual", "district": "信義區", "arrival_time": future.isoformat()})

    assert response.status_code == 200
    assert response.get_json()["recommendations"][0]["arrival_estimate"] is None
