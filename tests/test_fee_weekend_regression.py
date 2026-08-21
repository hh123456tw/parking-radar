"""回歸測試：官方結構化費率缺少星期欄位時不得顯示錯誤單價。"""

import json
from datetime import datetime
from zoneinfo import ZoneInfo

from fee_service import build_fee_summary


def test_weekend_day_dependent_fee_is_shown_as_honest_range():
    """文字明列平假日不同價格時，不能只採用不含星期的結構化單價。"""
    rules = {"FareRule": [{
        "ParkingType": "CM",
        "RateType": "1",
        "ChargeableSTime": "0800",
        "ChargeableETime": "2200",
        "ParkingRates": 20,
    }]}
    fee_info = (
        "小型車週一至週五20元/時(08-22)、10元/時(22-08)，"
        "週六至週日20元/時(08-10)、30元/時(10-22)、10元/時(22-08)。"
    )
    arrival = datetime(2026, 8, 22, 11, 0, tzinfo=ZoneInfo("Asia/Taipei"))

    result = build_fee_summary(json.dumps(rules), fee_info, arrival, "weekend")

    assert result["hourly_fee_label"] == "10～30 元／時"
    assert result["fee_note"] == "依日期、活動或現場公告"

