"""場站型態判定、OSM 匹配與同步測試；所有 Overpass 呼叫都以 fake 取代。"""

import json
from datetime import datetime, timezone

import pytest

import parking_metadata
from parking_metadata import (
    infer_official_facility_type,
    match_osm_facilities,
    sync_parking_metadata,
)


class JsonResponse:
    """提供 requests 回應所需的最小介面。"""

    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self.payload


class SpyCursor:
    """記錄 SQL 呼叫並提供可控制的查詢結果。"""

    def __init__(self, rows=None):
        self.rows = rows or []
        self.calls = []
        self.rowcount = 0

    def execute(self, sql, params=None):
        self.calls.append((sql, params))

    def executemany(self, sql, params):
        values = list(params)
        self.calls.append((sql, values))
        self.rowcount = len(values)

    def fetchall(self):
        return self.rows

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class MetadataConnection:
    """記錄 commit/rollback，並以固定列提供同步所需的候選場站。"""

    def __init__(self, rows=None):
        self.spy_cursor = SpyCursor(rows)
        self.committed = False
        self.rolled_back = False

    def cursor(self):
        return self.spy_cursor

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True


def row(lot_id, facility_type=None, facility_source=None,
        latitude=25.05, longitude=121.53):
    """建立一筆 fetch_parking_metadata_candidates 形狀的候選資料。"""
    return {
        "lot_id": lot_id, "lot_name": f"場站{lot_id}",
        "latitude": latitude, "longitude": longitude,
        "facility_type": facility_type, "facility_source": facility_source,
    }


def element(latitude, longitude, parking):
    """建立一個攤平後的 OSM 停車設施座標。"""
    return {"lat": latitude, "lon": longitude, "parking": parking}


def updates_by_lot(connection):
    """把同步最後的 executemany 更新轉成 lot_id 索引。"""
    sql, values = connection.spy_cursor.calls[-1]
    assert "UPDATE parking_lots" in sql
    return {item[3]: {"facility_type": item[0], "facility_source": item[1],
                      "metadata_checked_at": item[2]} for item in values}


@pytest.mark.parametrize(("text", "expected"), [
    ("忠孝機械停車場", ("mechanical", "official")),
    ("市民大道地下停車場", ("underground", "official")),
    ("河濱平面停車場", ("surface", "official")),
    ("公有立體停車場", ("multi_storey", "official")),
    ("一般停車場", ("unknown", "unknown")),
])
def test_infer_official_facility_type_uses_only_explicit_words(text, expected):
    assert infer_official_facility_type(text, "") == expected


def test_underground_is_not_inferred_as_mechanical():
    assert infer_official_facility_type("地下停車場", "") == (
        "underground", "official")


def test_infer_official_facility_type_returns_mixed_for_two_types():
    """名稱或說明同時出現兩種明確型態時回傳 mixed。"""
    assert infer_official_facility_type("地下立體停車場", "") == (
        "mixed", "official")
    assert infer_official_facility_type("忠孝機械停車場", "平面式車位") == (
        "mixed", "official")


def test_match_osm_facilities_matches_single_surface_within_range():
    """40 公尺內唯一 surface 設施即為明確匹配。"""
    lots = [row("L1", latitude=25.0, longitude=121.5)]
    elements = [element(25.0003, 121.5, "surface")]
    assert match_osm_facilities(lots, elements) == {"L1": "surface"}


def test_match_osm_facilities_rejects_feature_beyond_40_metres():
    """超過 40 公尺（例如約 44 公尺）的設施不得匹配。"""
    lots = [row("L1", latitude=25.0, longitude=121.5)]
    elements = [element(25.0004, 121.5, "surface")]
    assert match_osm_facilities(lots, elements) == {}


def test_match_osm_facilities_ambiguous_with_two_supported_features():
    """兩個支援設施同時在 40 公尺內時候選不唯一，不得猜測。"""
    lots = [row("L1", latitude=25.0, longitude=121.5)]
    elements = [
        element(25.0003, 121.5, "surface"),
        element(25.0, 121.5003, "underground"),
    ]
    assert match_osm_facilities(lots, elements) == {}


def test_match_osm_facilities_ignores_unsupported_parking_tags():
    """parking=street_side 等不支援的標籤一律不參與匹配。"""
    lots = [row("L1", latitude=25.0, longitude=121.5)]
    elements = [element(25.0003, 121.5, "street_side")]
    assert match_osm_facilities(lots, elements) == {}


@pytest.mark.parametrize(("parking_tag", "facility"), [
    ("underground", "underground"),
    ("multi-storey", "multi_storey"),
])
def test_match_osm_facilities_maps_supported_tags(parking_tag, facility):
    """支援的 OSM 標籤必須對應到正確型態，multi-storey 對應 multi_storey。"""
    lots = [row("L1", latitude=25.0, longitude=121.5)]
    elements = [element(25.0, 121.5, parking_tag)]
    assert match_osm_facilities(lots, elements) == {"L1": facility}


def test_match_osm_facilities_skips_lots_without_coordinates():
    """沒有座標的場站永遠不匹配 OSM。"""
    lots = [row("L1", latitude=None, longitude=None)]
    elements = [element(25.0, 121.5, "surface")]
    assert match_osm_facilities(lots, elements) == {}


def test_fetch_osm_parking_elements_uses_bbox_timeout_and_normalizes_center(monkeypatch):
    """Overpass 查詢需帶臺北 bbox、timeout 與 User-Agent，且攤平節點與 center。"""
    payload = {"elements": [
        {"type": "node", "lat": 25.02, "lon": 121.52,
         "tags": {"amenity": "parking", "parking": "surface"}},
        {"type": "way", "center": {"lat": 25.03, "lon": 121.53},
         "tags": {"amenity": "parking", "parking": "underground"}},
        {"type": "relation", "center": {"lat": 25.04, "lon": 121.54},
         "tags": {"amenity": "parking", "parking": "street_side"}},
    ]}
    captured = {}

    def fake_get(url, params, timeout, headers):
        captured.update(url=url, params=params, timeout=timeout, headers=headers)
        return JsonResponse(payload)

    monkeypatch.setattr(parking_metadata.requests, "get", fake_get)

    elements = parking_metadata.fetch_osm_parking_elements(timeout=7)

    assert captured["url"] == parking_metadata.OVERPASS_URL
    assert captured["timeout"] == 7
    query = captured["params"]["data"]
    assert "[out:json][timeout:7]" in query
    assert '"amenity"="parking"' in query
    assert "(24.8,121.3,25.3,121.8)" in query
    assert "out center;" in query
    assert captured["headers"]["User-Agent"]
    assert elements == [
        {"lat": 25.02, "lon": 121.52, "parking": "surface"},
        {"lat": 25.03, "lon": 121.53, "parking": "underground"},
        {"lat": 25.04, "lon": 121.54, "parking": "street_side"},
    ]


def test_sync_manual_override_beats_official_and_osm(tmp_path, monkeypatch):
    """人工覆寫必須壓過官方關鍵字與 OSM 的有力證據。"""
    overrides = tmp_path / "parking_overrides.json"
    overrides.write_text(json.dumps({"TPE-A": "mixed"}), encoding="utf-8")
    candidates = [
        row("TPE-A", facility_type="surface", facility_source="official"),
        row("TPE-B"),
    ]
    connection = MetadataConnection(candidates)
    monkeypatch.setattr(
        parking_metadata, "fetch_osm_parking_elements",
        lambda timeout=15: [element(25.05, 121.53, "surface")])

    counts = parking_metadata.sync_parking_metadata(
        connection, overrides_path=overrides)

    updates = updates_by_lot(connection)
    assert updates["TPE-A"]["facility_type"] == "mixed"
    assert updates["TPE-A"]["facility_source"] == "manual"
    assert updates["TPE-B"]["facility_source"] == "osm"
    assert counts == {"manual": 1, "official": 0, "osm": 1, "unknown": 0}
    assert connection.committed is True


def test_sync_keeps_official_over_closer_osm(tmp_path, monkeypatch):
    """既有官方型態必須保留，OSM 距離再近也不得覆寫。"""
    overrides = tmp_path / "parking_overrides.json"
    overrides.write_text("{}", encoding="utf-8")
    candidates = [
        row("TPE-C", facility_type="underground", facility_source="official"),
    ]
    connection = MetadataConnection(candidates)
    monkeypatch.setattr(
        parking_metadata, "fetch_osm_parking_elements",
        lambda timeout=15: [element(25.05, 121.53, "surface")])

    counts = parking_metadata.sync_parking_metadata(
        connection, overrides_path=overrides)

    updates = updates_by_lot(connection)
    assert updates["TPE-C"]["facility_type"] == "underground"
    assert updates["TPE-C"]["facility_source"] == "official"
    assert counts == {"manual": 0, "official": 1, "osm": 0, "unknown": 0}


def test_sync_adds_unambiguous_osm_to_unknown_lot(tmp_path, monkeypatch):
    """反向未知的場站可接受唯一的 OSM 匹配。"""
    overrides = tmp_path / "parking_overrides.json"
    overrides.write_text("{}", encoding="utf-8")
    candidates = [row("TPE-D")]
    connection = MetadataConnection(candidates)
    monkeypatch.setattr(
        parking_metadata, "fetch_osm_parking_elements",
        lambda timeout=15: [element(25.05, 121.53, "underground")])

    counts = parking_metadata.sync_parking_metadata(
        connection, overrides_path=overrides)

    updates = updates_by_lot(connection)
    assert updates["TPE-D"]["facility_type"] == "underground"
    assert updates["TPE-D"]["facility_source"] == "osm"
    assert counts == {"manual": 0, "official": 0, "osm": 1, "unknown": 0}
    assert connection.committed is True


def test_sync_keeps_existing_osm_value_without_fresh_match(tmp_path, monkeypatch):
    """OSM 本月暫時無候選時，不得把既有 OSM 型態抹成 unknown。"""
    overrides = tmp_path / "parking_overrides.json"
    overrides.write_text("{}", encoding="utf-8")
    candidates = [
        row("TPE-E", facility_type="surface", facility_source="osm"),
    ]
    connection = MetadataConnection(candidates)
    monkeypatch.setattr(
        parking_metadata, "fetch_osm_parking_elements", lambda timeout=15: [])

    counts = parking_metadata.sync_parking_metadata(
        connection, overrides_path=overrides)

    updates = updates_by_lot(connection)
    assert updates["TPE-E"]["facility_type"] == "surface"
    assert updates["TPE-E"]["facility_source"] == "osm"
    assert counts["osm"] == 1
    assert counts["unknown"] == 0


def test_sync_uses_new_unambiguous_osm_match_over_stale_value(tmp_path, monkeypatch):
    """OSM 重新匹配到不同的唯一設施時，應更新成最新結果。"""
    overrides = tmp_path / "parking_overrides.json"
    overrides.write_text("{}", encoding="utf-8")
    candidates = [
        row("TPE-F", facility_type="surface", facility_source="osm"),
    ]
    connection = MetadataConnection(candidates)
    monkeypatch.setattr(
        parking_metadata, "fetch_osm_parking_elements",
        lambda timeout=15: [element(25.05, 121.53, "underground")])

    parking_metadata.sync_parking_metadata(connection, overrides_path=overrides)

    updates = updates_by_lot(connection)
    assert updates["TPE-F"]["facility_type"] == "underground"
    assert updates["TPE-F"]["facility_source"] == "osm"


def test_sync_leaves_ambiguous_osm_untouched(tmp_path, monkeypatch):
    """兩個候選都在 40 公尺內時保持 unknown，不得猜測。"""
    overrides = tmp_path / "parking_overrides.json"
    overrides.write_text("{}", encoding="utf-8")
    candidates = [row("TPE-G")]
    connection = MetadataConnection(candidates)
    monkeypatch.setattr(
        parking_metadata, "fetch_osm_parking_elements",
        lambda timeout=15: [
            element(25.05, 121.53, "surface"),
            element(25.0503, 121.53, "underground"),
        ])

    counts = parking_metadata.sync_parking_metadata(
        connection, overrides_path=overrides)

    updates = updates_by_lot(connection)
    assert updates["TPE-G"]["facility_type"] == "unknown"
    assert updates["TPE-G"]["facility_source"] == "unknown"
    assert counts["unknown"] == 1


def test_sync_stamps_utc_checked_at(tmp_path, monkeypatch):
    """每次同步都要以 UTC 更新 metadata_checked_at。"""
    overrides = tmp_path / "parking_overrides.json"
    overrides.write_text("{}", encoding="utf-8")
    connection = MetadataConnection([row("TPE-A")])
    monkeypatch.setattr(
        parking_metadata, "fetch_osm_parking_elements", lambda timeout=15: [])

    parking_metadata.sync_parking_metadata(connection, overrides_path=overrides)

    updates = updates_by_lot(connection)
    assert isinstance(updates["TPE-A"]["metadata_checked_at"], datetime)
    assert updates["TPE-A"]["metadata_checked_at"].tzinfo == timezone.utc


def test_sync_rejects_invalid_override_value(tmp_path):
    """覆寫值不在允許清單時必須拒絕，避免寫入不認識的型態。"""
    overrides = tmp_path / "parking_overrides.json"
    overrides.write_text(json.dumps({"TPE-A": "skywalk"}), encoding="utf-8")

    with pytest.raises(ValueError, match="skywalk"):
        parking_metadata.sync_parking_metadata(
            MetadataConnection([]), overrides_path=overrides)


def test_sync_rejects_non_object_override_file(tmp_path):
    """覆寫檔格式錯誤（例如陣列）時必須拒絕並交由 rollback 處理。"""
    overrides = tmp_path / "parking_overrides.json"
    overrides.write_text("[]", encoding="utf-8")

    with pytest.raises(ValueError):
        parking_metadata.sync_parking_metadata(
            MetadataConnection([]), overrides_path=overrides)


def test_sync_rolls_back_and_rethrows_when_update_fails(tmp_path, monkeypatch):
    """寫入失敗時不得 commit，必須 rollback 並重拋例外。"""
    overrides = tmp_path / "parking_overrides.json"
    overrides.write_text("{}", encoding="utf-8")
    connection = MetadataConnection([row("TPE-A")])
    monkeypatch.setattr(
        parking_metadata, "fetch_osm_parking_elements", lambda timeout=15: [])

    def boom(conn, updates):
        raise RuntimeError("update failed")

    monkeypatch.setattr(parking_metadata, "update_parking_metadata", boom)

    with pytest.raises(RuntimeError, match="update failed"):
        parking_metadata.sync_parking_metadata(
            connection, overrides_path=overrides)

    assert connection.rolled_back is True
    assert connection.committed is False