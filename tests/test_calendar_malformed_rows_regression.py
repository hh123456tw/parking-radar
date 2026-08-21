"""回歸測試：損壞的行事曆資料不得中斷停車查詢。"""

import json
from datetime import datetime
from zoneinfo import ZoneInfo

from calendar_service import classify_arrival_day


def test_malformed_calendar_rows_fall_back_instead_of_raising(tmp_path):
    """缺少必要欄位的資料列應視為不可用，改以星期規則降級。"""
    (tmp_path / "2026.json").write_text(
        json.dumps([{"week": "五", "isHoliday": False}, "broken"]),
        encoding="utf-8",
    )
    arrival = datetime(2026, 8, 21, 18, 0, tzinfo=ZoneInfo("Asia/Taipei"))

    result = classify_arrival_day(arrival, tmp_path)

    assert result == {
        "kind": "weekday",
        "label": "平日",
        "is_holiday": False,
        "source": "weekday_fallback",
    }
