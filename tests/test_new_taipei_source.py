"""新北市官方資料解析測試：固定 JSON fixture，不呼叫真實網路。"""

import json
import ssl
from datetime import datetime, timezone
from pathlib import Path

import pytest
import requests

import new_taipei_source

FIXTURES = Path(__file__).parent / "fixtures"
UPSERT_KEYS = {
    "lot_id", "city", "source", "source_lot_id",
    "lot_name", "district", "address", "operator_type",
    "total_spaces", "fee_info", "fare_rules_json",
    "facility_type", "facility_source", "metadata_checked_at",
    "service_time", "latitude", "longitude",
    "supports_realtime", "source_updated_at",
}


def load_fixture(name):
    """讀取固定官方格式，避免測試依賴即時資料變化。"""
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture
def captured_at():
    """固定收集時間，讓快照時間與 source_updated_at 可精確比對。"""
    return datetime(2026, 8, 26, 12, 0, tzinfo=timezone.utc)


class SequenceGet:
    """依序回傳預設 response 或拋出例外，並記錄呼叫參數。"""

    def __init__(self, responses):
        self.responses = list(responses)
        self.call_count = 0
        self.calls = []

    def __call__(self, url, *args, **kwargs):
        self.calls.append((url, args, kwargs))
        self.call_count += 1
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def page_response(page, total_pages):
    """回傳一頁兩筆迷你資料；page 等於 total_pages 時以空陣列結束分頁。"""

    class PageResponse:
        def raise_for_status(self):
            pass

        def json(self):
            if page >= total_pages:
                return []
            return [{"ID": f"{page:02d}-{index}", "NAME": f"測試場站{page}"}
                    for index in range(2)]

    return PageResponse()


def test_twd97_known_point_converts_to_wgs84():
    """官方已知 TWD97 點必須轉成 WGS84 且落在允許誤差內。"""
    latitude, longitude = new_taipei_source.twd97_to_wgs84(296882.0, 2767068.0)

    assert latitude == pytest.approx(25.0109252, abs=0.00001)
    assert longitude == pytest.approx(121.4644919, abs=0.00001)


def test_twd97_rejects_missing_and_out_of_bounds_coordinates():
    """缺值、非數字或超出雙北範圍的座標必須排除，不得參與推薦。"""
    assert new_taipei_source.twd97_to_wgs84(None, 2767068.0) == (None, None)
    assert new_taipei_source.twd97_to_wgs84("abc", 2767068.0) == (None, None)
    assert new_taipei_source.twd97_to_wgs84(121.0, 25.0) == (None, None)


def test_static_parser_emits_upsert_keys_and_prefixes_ids(captured_at):
    """輸出必須只含 database.upsert_parking_lots 接受的鍵且使用 NTP: 前綴。"""
    lots = new_taipei_source.parse_static(
        load_fixture("new_taipei_static.json"), {"010056"}, captured_at)

    assert set(lots[0]) == UPSERT_KEYS
    assert lots[0]["lot_id"] == "NTP:010056"
    assert lots[0]["city"] == "新北市"
    assert lots[0]["source"] == "new_taipei"
    assert lots[0]["source_lot_id"] == "010056"
    assert lots[0]["operator_type"] == "官方路外停車場"
    assert lots[0]["supports_realtime"] is True
    assert lots[0]["fare_rules_json"] is None
    assert lots[0]["source_updated_at"] == captured_at


def test_static_parser_converts_twd97_and_reads_official_fields(captured_at):
    """官方欄位必須對應到統一欄位，SUMMARY 關鍵字決定場站型態。"""
    lots = new_taipei_source.parse_static(
        load_fixture("new_taipei_static.json"), {"010056", "060040"}, captured_at)

    first = lots[0]
    assert first["lot_name"] == "遠東百貨停車場"
    assert first["district"] == "板橋區"
    assert first["address"] == "板橋區中山路一段152號"
    assert first["fee_info"] == "小型車計時60元;"
    assert first["service_time"] == "0~24時"
    assert first["total_spaces"] == 453
    assert first["latitude"] == pytest.approx(25.0109252, abs=0.00001)
    assert first["longitude"] == pytest.approx(121.4644919, abs=0.00001)
    assert first["facility_type"] == "multi_storey"
    assert first["facility_source"] == "official"

    second = lots[1]
    assert second["latitude"] == pytest.approx(24.9716752, abs=0.00001)
    assert second["longitude"] == pytest.approx(121.5394146, abs=0.00001)
    assert second["facility_type"] == "mixed"
    # 動態值為 -9 的場站仍屬於支援即時資料，只是本次不寫入快照。
    assert second["supports_realtime"] is True


def test_dynamic_skips_negative_and_prefixes_ids(captured_at):
    """負數與無法解析的剩餘格數不得寫入快照，ID 需加上 NTP: 前綴。"""
    rows = new_taipei_source.parse_dynamic(
        load_fixture("new_taipei_dynamic.json"), captured_at)

    assert [row["lot_id"] for row in rows] == ["NTP:010056", "NTP:999999"]
    assert rows[0]["available_spaces"] == 24
    assert rows[0]["source_updated_at"] == captured_at
    assert rows[0]["captured_at"] == captured_at


def test_fetch_pages_retries_one_timeout_then_completes(monkeypatch):
    """單頁 timeout 一次後必須重試成功，最後回傳全部列。"""
    http_get = SequenceGet(
        [requests.Timeout(), page_response(0, 1), page_response(1, 1)])

    rows = new_taipei_source.fetch_pages("dataset", 3, http_get=http_get)

    assert len(rows) == 2
    assert http_get.call_count == 3


def test_fetch_pages_uses_page_and_size_parameters():
    """官方分頁從第 0 頁開始，否則會漏掉前 1000 筆資料。"""
    http_get = SequenceGet([page_response(0, 1), page_response(1, 1)])

    new_taipei_source.fetch_pages("dataset", 3, http_get=http_get)

    url, _args, kwargs = http_get.calls[0]
    assert "dataset" in url
    assert kwargs == {"params": {"page": 0, "size": 1000}, "timeout": 3}
    assert [call[2]["params"]["page"] for call in http_get.calls] == [0, 1]


def test_fetch_pages_retries_failed_page_at_most_twice_then_raises():
    """每頁最多重試兩次，仍失敗時必須重拋，避免半套資料。"""
    http_get = SequenceGet(
        [requests.Timeout(), requests.Timeout(), requests.Timeout()])

    with pytest.raises(requests.Timeout):
        new_taipei_source.fetch_pages("dataset", 3, http_get=http_get)

    assert http_get.call_count == 3


def test_ntpc_session_keeps_certificate_and_hostname_checks_without_strict_mode():
    """新北舊憑證可相容，但不得關閉 CA 或主機名驗證。"""
    session = new_taipei_source.build_ntpc_session()
    adapter = session.get_adapter("https://data.ntpc.gov.tw/")
    context = adapter.poolmanager.connection_pool_kw["ssl_context"]

    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True
    if hasattr(ssl, "VERIFY_X509_STRICT"):
        assert not context.verify_flags & ssl.VERIFY_X509_STRICT


def test_collect_deduplicates_and_reports_metrics(monkeypatch):
    """重複 ID 最後一筆勝出；動態負數與無對應靜態的場站都要記錄。"""
    static_rows = load_fixture("new_taipei_static.json")
    static_rows.append(dict(static_rows[0], NAME="重複的最後一筆"))
    dynamic_rows = load_fixture("new_taipei_dynamic.json")
    monkeypatch.setattr(
        new_taipei_source, "fetch_pages",
        lambda _dataset_id, timeout, http_get=requests.get: (
            static_rows if _dataset_id.startswith("b1464") else dynamic_rows))

    lots, snapshots, metrics = new_taipei_source.NewTaipeiSourceAdapter.collect(3)

    assert [lot["lot_id"] for lot in lots] == ["NTP:010056", "NTP:060040"]
    assert lots[0]["lot_name"] == "重複的最後一筆"
    assert [row["lot_id"] for row in snapshots] == ["NTP:010056"]
    assert metrics == {
        "duplicates": 1, "invalid_dynamic": 1, "unmatched_dynamic": 1}
