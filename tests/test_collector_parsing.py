"""臺北市官方資料解析測試：固定 JSON fixture，不呼叫真實網路。"""

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

import collector

FIXTURES = Path(__file__).parent / "fixtures"


class JsonResponse:
    """提供 collector.fetch_json 所需的最小 HTTP response。"""

    def __init__(self, payload):
        self.payload = payload
        self.status_checked = False

    def raise_for_status(self):
        self.status_checked = True

    def json(self):
        return self.payload


def load_fixture(name):
    """讀取固定官方格式，避免測試依賴即時資料變化。"""
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_static_parser_uses_exact_id_and_valid_wgs84_entrance():
    """靜態資料需保留官方 ID、業者類型、座標與即時支援狀態。"""
    lots = collector.parse_static(load_fixture("taipei_static.json"), {"TPE0001"})

    assert lots[0]["lot_id"] == "TPE0001"
    assert lots[0]["operator_type"] == "民營停車場"
    assert lots[0]["latitude"] == 25.0552
    assert lots[0]["longitude"] == 121.5242
    assert lots[0]["supports_realtime"] is True
    assert lots[1]["latitude"] is None
    assert lots[1]["longitude"] is None
    assert lots[1]["supports_realtime"] is False


def test_static_parser_preserves_raw_fare_rules_as_utf8_json():
    """原始 FareInfo 必須完整保存，中文不得被 ASCII 跳脫。"""
    lot = collector.parse_static(
        load_fixture("taipei_static.json"), {"TPE0001"})[0]

    assert json.loads(lot["fare_rules_json"])["FareRule"][0] == {
        "ParkingType": "C", "RateType": "1",
        "ChargeableSTime": "0800", "ChargeableETime": "2200",
        "ParkingRates": "60",
    }
    assert "\\u" not in lot["fare_rules_json"]


def test_static_parser_rejects_malformed_and_out_of_taipei_coordinates():
    """格式錯誤或超出臺北範圍的入口座標不得參與附近推薦。"""
    payload = load_fixture("taipei_static.json")
    malformed = payload["data"]["park"][0]
    malformed["EntranceCoord"] = {"EntrancecoordInfo": [
        {"Xcod": "not-a-number", "Ycod": "121.5"}]}
    out_of_range = dict(malformed, id="TPE-X")
    out_of_range["EntranceCoord"] = {"EntrancecoordInfo": [
        {"Xcod": "23.5", "Ycod": "121.5"}]}
    payload["data"]["park"] = [malformed, out_of_range]

    lots = collector.parse_static(payload, set())

    assert [(lot["latitude"], lot["longitude"]) for lot in lots] == [
        (None, None), (None, None)]


def test_static_parser_derives_facility_type_from_name_and_summary():
    """官方名稱與說明中的明確關鍵字必須寫入 facility_type 與 facility_source。"""
    payload = load_fixture("taipei_static.json")
    payload["data"]["park"][0]["name"] = "市民大道地下停車場"
    payload["data"]["park"][0]["summary"] = "地下四層結構"
    lots = collector.parse_static(payload, {"TPE0001"})

    assert lots[0]["facility_type"] == "underground"
    assert lots[0]["facility_source"] == "official"


def test_static_parser_defaults_facility_to_unknown_without_keywords():
    """名稱與說明皆無關鍵字時，型態維持 unknown，方便後續 OSM 補足。"""
    lots = collector.parse_static(load_fixture("taipei_static.json"), set())

    assert all(lot["facility_type"] == "unknown" for lot in lots)
    assert all(lot["facility_source"] == "unknown" for lot in lots)


def test_dynamic_parser_keeps_nonnegative_values_for_join_validation():
    """動態 parser 先排除負數，超額值留給合併總格數後判斷。"""
    captured = datetime(2026, 8, 3, 10, 1, tzinfo=timezone.utc)
    snapshots = collector.parse_dynamic(load_fixture("taipei_dynamic.json"), captured)

    assert [row["lot_id"] for row in snapshots] == ["TPE0001", "TPE0003"]
    assert snapshots[0]["available_spaces"] == 8
    assert snapshots[1]["available_spaces"] == 999
    assert snapshots[0]["source_updated_at"] == datetime(
        2026, 8, 3, 10, 0, tzinfo=timezone.utc)


def test_source_time_without_offset_is_interpreted_as_taipei_time():
    """官方時間缺少 offset 時，依資料來源所在地補 Asia/Taipei。"""
    assert collector.parse_source_time("2026-08-03T18:00:00") == datetime(
        2026, 8, 3, 10, 0, tzinfo=timezone.utc)


def test_source_time_accepts_current_taipei_cst_format():
    """官方 API 的英文 CST 日期格式應視為臺北時間並轉成 UTC。"""
    assert collector.parse_source_time("Tue Aug 04 12:04:00 CST 2026") == datetime(
        2026, 8, 4, 4, 4, tzinfo=timezone.utc)


def test_fetch_json_uses_timeout_and_checks_http_status(monkeypatch):
    """下載官方資料必須設定 timeout 並在解析前檢查 HTTP 狀態。"""
    payload = {"data": {"park": []}}
    response = JsonResponse(payload)
    captured = {}

    def fake_get(url, timeout):
        captured.update(url=url, timeout=timeout)
        return response

    monkeypatch.setattr(collector.requests, "get", fake_get)

    assert collector.fetch_json("https://example.test/data.json", timeout=7) == payload
    assert captured == {"url": "https://example.test/data.json", "timeout": 7}
    assert response.status_checked is True


def test_malformed_numbers_skip_single_lot_instead_of_failing_batch():
    """官方單筆 N/A 或空白數值只略過該場站，其餘快照照常保存。"""
    captured_at = datetime(2026, 8, 3, 4, 0, tzinfo=timezone.utc)
    payload = {"data": {"UPDATETIME": "2026-08-03T12:00:00", "park": [
        {"id": "001", "availablecar": "N/A"},
        {"id": "002", "availablecar": " "},
        {"id": "003", "availablecar": "12"},
    ]}}

    rows = collector.parse_dynamic(payload, captured_at)

    assert [(row["lot_id"], row["available_spaces"]) for row in rows] == [("003", 12)]


def test_static_parser_treats_malformed_total_as_zero():
    payload = {"data": {"UPDATETIME": "2026-08-03T12:00:00", "park": [
        {"id": "001", "name": "測試停車場", "totalcar": "N/A"},
    ]}}

    lots = collector.parse_static(payload, {"001"})

    assert lots[0]["total_spaces"] == 0


def test_static_parser_truncates_text_to_schema_lengths():
    """官方文字剛好等於欄位長度時保留，超過時截斷，避免整批交易回滾。"""
    payload = load_fixture("taipei_static.json")
    exact = dict(payload["data"]["park"][0], name="名" * 120, area="區" * 20,
                 address="址" * 255, type2="型" * 40)
    over = dict(payload["data"]["park"][0], id="TPE9999", name="名" * 121,
                area="區" * 21, address="址" * 256, type2="型" * 41)
    payload["data"]["park"] = [exact, over]

    lots = collector.parse_static(payload, set())

    for lot in lots:
        assert lot["lot_name"] == "名" * 120
        assert lot["district"] == "區" * 20
        assert lot["address"] == "址" * 255
        assert lot["operator_type"] == "型" * 40


class XmlResponse:
    """提供 collector.fetch_xml 所需的最小 HTTP response。"""

    def __init__(self, content):
        self.content = content

    def raise_for_status(self):
        pass


def fixture_bytes(name):
    return (FIXTURES / name).read_bytes()


def test_xml_feed_converts_to_the_json_shape(monkeypatch):
    """官方 XML（真實樣本）轉成與 JSON 相同的鍵名與巢狀結構，後續解析共用。"""
    monkeypatch.setattr(collector.requests, "get",
                        lambda *_args, **_kwargs: XmlResponse(fixture_bytes("taipei_static.xml")))

    payload = collector.fetch_xml("https://example.test/TCMSV_alldesc.xml")

    park = next(p for p in payload["data"]["park"] if p["id"] == "TPE0002")
    assert payload["data"]["UPDATETIME"].endswith("CST 2026")
    assert {"name", "area", "address", "payex", "serviceTime", "totalcar"} <= set(park)
    assert park["EntranceCoord"]["EntrancecoordInfo"][0]["Xcod"] == "25.0331"
    # XML 的 FareInfo 是空的，費率改放在 MAXInfo/FareRule。
    rates = [rule["ParkingRates"] for rule in park["FareInfo"]["FareRule"]]
    assert "100" in rates

    lots = collector.parse_static(payload, {"TPE0002"})
    lot = next(row for row in lots if row["lot_id"] == "TPE0002")
    assert (lot["latitude"], lot["longitude"]) == (25.0331, 121.5622)
    assert '"ParkingRates":"100"' in lot["fare_rules_json"]


def test_xml_dynamic_feed_feeds_parse_dynamic(monkeypatch):
    monkeypatch.setattr(collector.requests, "get",
                        lambda *_args, **_kwargs: XmlResponse(fixture_bytes("taipei_dynamic.xml")))

    payload = collector.fetch_xml("https://example.test/TCMSV_allavailable.xml")
    rows = collector.parse_dynamic(payload, datetime(2026, 11, 13, 6, 30, tzinfo=timezone.utc))

    assert {row["lot_id"] for row in rows} <= {"TPE0001", "TPE0002"}
    assert rows and all(isinstance(row["available_spaces"], int) for row in rows)


def test_xml_without_updatetime_is_an_error(monkeypatch):
    """沒有官方時間就無法判斷新舊，不可當成有效資料。"""
    body = b"<DATA><PARK><ID>X</ID></PARK></DATA>"
    monkeypatch.setattr(collector.requests, "get", lambda *_args, **_kwargs: XmlResponse(body))

    with pytest.raises(ValueError, match="UPDATETIME"):
        collector.fetch_xml("https://example.test/bad.xml")


def feed(time_text):
    return {"data": {"UPDATETIME": time_text, "park": []}}


def test_fetch_feed_picks_the_newer_official_time(monkeypatch):
    """JSON 凍結在 10/19、XML 是新的時用 XML；反過來時用 JSON。"""
    old, new = "Mon Oct 19 10:26:00 CST 2026", "Fri Nov 13 14:25:00 CST 2026"
    monkeypatch.setattr(collector, "fetch_json", lambda *_a, **_k: feed(old))
    monkeypatch.setattr(collector, "fetch_xml", lambda *_a, **_k: feed(new))
    assert collector.fetch_feed("j", "x")["data"]["UPDATETIME"] == new

    monkeypatch.setattr(collector, "fetch_json", lambda *_a, **_k: feed(new))
    monkeypatch.setattr(collector, "fetch_xml", lambda *_a, **_k: feed(old))
    assert collector.fetch_feed("j", "x")["data"]["UPDATETIME"] == new


def test_fetch_feed_falls_back_when_one_format_fails(monkeypatch):
    def broken(*_args, **_kwargs):
        raise ConnectionError("down")

    new, old = "Fri Nov 13 14:25:00 CST 2026", "Mon Oct 19 10:26:00 CST 2026"
    monkeypatch.setattr(collector, "fetch_json", broken)
    monkeypatch.setattr(collector, "fetch_xml", lambda *_a, **_k: feed(new))
    assert collector.fetch_feed("j", "x")["data"]["UPDATETIME"].startswith("Fri Nov 13")

    monkeypatch.setattr(collector, "fetch_json", lambda *_a, **_k: feed(old))
    monkeypatch.setattr(collector, "fetch_xml", broken)
    assert collector.fetch_feed("j", "x")["data"]["UPDATETIME"].startswith("Mon Oct 19")


def test_fetch_feed_raises_when_both_formats_fail(monkeypatch):
    """兩種格式都拿不到時必須報錯，不可回傳空資料假裝成功。"""
    def broken(*_args, **_kwargs):
        raise ConnectionError("down")

    monkeypatch.setattr(collector, "fetch_json", broken)
    monkeypatch.setattr(collector, "fetch_xml", broken)
    with pytest.raises(RuntimeError, match="JSON 與 XML"):
        collector.fetch_feed("j", "x")
