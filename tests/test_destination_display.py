"""目的地顯示：把 OpenStreetMap 的「名稱, 門牌, 路, 里, 區, …, 國家」整理成名稱＋臺灣習慣地址。"""

from datetime import datetime, timezone

import pytest

import database
import query_service
from geocoder import format_place

# 以下字串取自 Nominatim 實際回傳（2026-10-03）。
REAL_RESULTS = [
    ("臺北車站, 3, 北平西路, 黎明里, 中正區, 台北站前商圈, 臺北市, 10041, 臺灣",
     "臺北車站", "臺北市中正區北平西路3號"),
    ("京站時尚廣場, 1, 承德路一段, 建明里, 大同區, 後車頭, 臺北市, 10351, 臺灣",
     "京站時尚廣場", "臺北市大同區承德路一段1號"),
    ("國立臺灣大學, 1, 羅斯福路四段, 水源里, 中正區, 公館, 臺北市, 106319, 臺灣",
     "國立臺灣大學", "臺北市中正區羅斯福路四段1號"),
    ("華山1914文化創意產業園區, 1, 八德路一段, 光華商場, 梅花里, 中正區, 華山, 臺北市, 10058, 臺灣",
     "華山1914文化創意產業園區", "臺北市中正區八德路一段1號"),
    ("臺北市政府, 1, 市府路, 西村里, 信義區, 信義商圈, 臺北市, 11008, 臺灣",
     "臺北市政府", "臺北市信義區市府路1號"),
    # 純路段查詢沒有地名：只顯示一行地址。
    ("基隆路二段, 芳和里, 大安區, 六張犁, 臺北市, 10679, 臺灣",
     None, "臺北市大安區基隆路二段"),
    ("忠孝東路五段, 興雅里, 信義區, 興雅, 臺北市, 11071, 臺灣",
     None, "臺北市信義區忠孝東路五段"),
]


@pytest.mark.parametrize("display, name, address", REAL_RESULTS)
def test_format_place_reorders_osm_address_to_taiwan_style(display, name, address):
    assert format_place(display) == {"name": name, "address": address}


def test_format_place_keeps_lane_and_hyphenated_number():
    display = "某某大樓, 12-1, 八德路四段138巷, 松山區, 臺北市, 105, 臺灣"

    assert format_place(display) == {
        "name": "某某大樓", "address": "臺北市松山區八德路四段138巷12-1號"}


def test_format_place_falls_back_to_first_part_when_unrecognised():
    """沒有行政區也沒有路名時不硬拼地址，只保留第一段名稱。"""
    assert format_place("某個地點, 臺灣") == {"name": "某個地點", "address": None}


@pytest.mark.parametrize("label, name, address", [
    ("臺北市政府（臺北市信義區市府路1號）", "臺北市政府", "臺北市信義區市府路1號"),
    ("目前位置", "目前位置", None),
])
def test_destination_names_split_our_own_labels(label, name, address):
    parsed = {"destination_label": label}
    destination = {"display_address": "raw, 臺灣"}

    assert query_service.destination_names(parsed, destination) == (name, address)


class FakeConnection:
    def close(self):
        pass


def test_query_api_returns_name_and_formatted_address(monkeypatch):
    """直接以地址服務查到的目的地，也要回傳整理後的名稱與地址。"""
    monkeypatch.setattr(database, "get_connection", FakeConnection)
    monkeypatch.setattr(query_service, "geocode_address", lambda *_args: {
        "display_address": REAL_RESULTS[4][0], "latitude": 25.0375, "longitude": 121.5637})
    monkeypatch.setattr(query_service, "fetch_current_lots", lambda *_args: [{
        "lot_id": "TPE1", "lot_name": "A場", "district": "信義區",
        "address": "市府路", "operator_type": "民營停車場",
        "total_spaces": 100, "available_spaces": 40,
        "fee_info": "每小時30元", "service_time": "24小時",
        "fare_rules_json": None, "facility_type": "underground",
        "facility_source": "official", "latitude": 25.0376, "longitude": 121.5638,
        "captured_at": datetime.now(timezone.utc),
    }])
    import app as app_module
    client = app_module.create_app({
        "TESTING": True, "SECRET_KEY": "test", "AUTO_REFRESH_ENABLED": False,
        "OPENROUTESERVICE_API_KEY": ""}).test_client()

    response = client.post("/api/query", json={
        "mode": "manual", "address": "臺北市信義區市府路1號",
        "arrival_time": "2026-08-04T18:00:00+08:00"})

    destination = response.get_json()["destination"]
    assert destination["name"] == "臺北市政府"
    assert destination["address"] == "臺北市信義區市府路1號"
    assert destination["display_address"] == "臺北市政府（臺北市信義區市府路1號）"
