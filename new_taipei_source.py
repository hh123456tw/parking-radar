"""下載新北市官方停車資料，轉換 TWD97 座標並正規化為統一字典契約。"""

import ssl
from datetime import datetime, timezone

import requests
from pyproj import Transformer
from requests.adapters import HTTPAdapter

from parking_metadata import infer_official_facility_type

STATIC_DATASET_ID = "b1464ef0-9c7c-4a6f-abf7-6bdf32847e68"
DYNAMIC_DATASET_ID = "e09b35a5-a738-48cc-b0f5-570b67ad9c78"
API_BASE_URL = "https://data.ntpc.gov.tw/api/datasets/{dataset_id}/json"
PAGE_SIZE = 1000
MAX_RETRIES = 2
# (min_latitude, max_latitude, min_longitude, max_longitude)
NEW_TAIPEI_BOUNDS = (24.5, 25.4, 121.2, 122.1)

_TWD97_TO_WGS84 = Transformer.from_crs("EPSG:3826", "EPSG:4326", always_xy=True)


class _VerifiedCompatibilityAdapter(HTTPAdapter):
    """只放寬舊憑證格式規則，保留 CA 與主機名驗證。"""

    def __init__(self, ssl_context, *args, **kwargs):
        self.ssl_context = ssl_context
        super().__init__(*args, **kwargs)

    def init_poolmanager(self, connections, maxsize, block=False, **kwargs):
        kwargs["ssl_context"] = self.ssl_context
        return super().init_poolmanager(
            connections, maxsize, block=block, **kwargs)


def build_ntpc_session():
    """建立僅供新北官方 API 使用的已驗證相容 Session。"""
    context = ssl.create_default_context()
    strict_flag = getattr(ssl, "VERIFY_X509_STRICT", 0)
    if strict_flag:
        # Python 3.13 預設 strict 會拒絕缺少 SKI 的舊憑證；CA 與 hostname
        # 驗證仍保持啟用，不能改成 verify=False。
        context.verify_flags &= ~strict_flag
    session = requests.Session()
    session.mount(
        "https://data.ntpc.gov.tw/",
        _VerifiedCompatibilityAdapter(context),
    )
    return session


_NTPC_SESSION = build_ntpc_session()


def twd97_to_wgs84(x, y):
    """把 EPSG:3826 座標轉成 WGS84；缺值、格式錯誤或超出雙北範圍時回傳 (None, None)。"""
    try:
        longitude, latitude = _TWD97_TO_WGS84.transform(float(x), float(y))
    except (TypeError, ValueError):
        return None, None
    min_latitude, max_latitude, min_longitude, max_longitude = NEW_TAIPEI_BOUNDS
    if not (min_latitude <= latitude <= max_latitude
            and min_longitude <= longitude <= max_longitude):
        return None, None
    return latitude, longitude


def _fetch_page(dataset_id, page, timeout, http_get):
    """抓取單頁 JSON；網路失敗最多重試兩次，仍失敗時重拋最後例外。"""
    url = API_BASE_URL.format(dataset_id=dataset_id)
    last_error = None
    for _attempt in range(MAX_RETRIES + 1):
        try:
            response = http_get(
                url, params={"page": page, "size": PAGE_SIZE}, timeout=timeout)
            response.raise_for_status()
            payload = response.json()
        except requests.RequestException as error:
            last_error = error
            continue
        if not isinstance(payload, list):
            raise ValueError("新北 API 分頁回應必須是 JSON 陣列")
        return payload
    raise last_error


def fetch_pages(dataset_id, timeout, http_get=None):
    """以固定頁大小抓完整個資料集；官方在最後一頁之後回傳空陣列，故以空頁停止。"""
    if http_get is None:
        http_get = _NTPC_SESSION.get
    rows = []
    page = 1
    while True:
        payload = _fetch_page(dataset_id, page, timeout, http_get)
        rows.extend(payload)
        if not payload:
            break
        page += 1
    return rows


def deduplicate_static(rows):
    """以官方 ID 去重，最後一筆勝出；回傳 (依序保留的 dict, 重複筆數)。"""
    deduped = {}
    duplicates = 0
    for raw in rows:
        source_id = raw.get("ID")
        if source_id is None:
            continue
        key = str(source_id)
        if key in deduped:
            duplicates += 1
        deduped[key] = raw
    return deduped, duplicates


def parse_static(rows, realtime_ids, captured_at):
    """把官方靜態欄位統一成 parking_lots 所需格式；無效座標保存為 None。"""
    lots = []
    for raw in rows:
        source_id = str(raw.get("ID") or "").strip()
        if not source_id:
            continue
        latitude, longitude = twd97_to_wgs84(raw.get("TW97X"), raw.get("TW97Y"))
        facility_type, facility_source = infer_official_facility_type(
            raw.get("NAME", ""), raw.get("SUMMARY", ""))
        lots.append({
            "lot_id": f"NTP:{source_id}",
            "city": "新北市",
            "source": "new_taipei",
            "source_lot_id": source_id,
            "lot_name": raw.get("NAME") or "未命名停車場",
            "district": raw.get("AREA") or "未知",
            "address": raw.get("ADDRESS") or "",
            "operator_type": "官方路外停車場",
            "total_spaces": int(raw.get("TOTALCAR") or 0),
            "fee_info": raw.get("PAYEX") or "",
            "fare_rules_json": None,
            "facility_type": facility_type,
            "facility_source": facility_source,
            "metadata_checked_at": None,
            "service_time": raw.get("SERVICETIME") or "",
            "latitude": latitude,
            "longitude": longitude,
            "supports_realtime": source_id in realtime_ids,
            "source_updated_at": captured_at,
        })
    return lots


def parse_dynamic(rows, captured_at):
    """保留非負汽車剩餘格數並加上 NTP: 前綴；新北沒有可靠官方時間，以取得時間為準。"""
    snapshots = []
    for raw in rows:
        source_id = str(raw.get("ID") or "").strip()
        if not source_id:
            continue
        try:
            available = int(raw["AVAILABLECAR"])
        except (KeyError, TypeError, ValueError):
            continue
        if available < 0:
            continue
        snapshots.append({
            "lot_id": f"NTP:{source_id}",
            "available_spaces": available,
            "source_updated_at": captured_at,
            "captured_at": captured_at,
        })
    return snapshots


class NewTaipeiSourceAdapter:
    """新北官方資料來源：分頁下載、去重、座標轉換並回傳可寫入的統一記錄。"""

    @classmethod
    def collect(cls, timeout, http_get=None):
        """抓取兩份資料集，回傳 (lots, snapshots, metrics)。"""
        static_rows = fetch_pages(
            STATIC_DATASET_ID, timeout, http_get=http_get)
        dynamic_rows = fetch_pages(
            DYNAMIC_DATASET_ID, timeout, http_get=http_get)
        captured_at = datetime.now(timezone.utc)
        deduped, duplicates = deduplicate_static(static_rows)
        realtime_ids = {
            str(row["ID"]) for row in dynamic_rows if row.get("ID") is not None}
        lots = parse_static(list(deduped.values()), realtime_ids, captured_at)
        snapshots = parse_dynamic(dynamic_rows, captured_at)
        matched_ids = {f"NTP:{source_id}" for source_id in deduped}
        # 動態 ID 找不到對應靜態場站時跳過快照，避免缺少總車位與座標的半套記錄。
        matched_snapshots = [
            row for row in snapshots if row["lot_id"] in matched_ids]
        metrics = {
            "duplicates": duplicates,
            "invalid_dynamic": len(dynamic_rows) - len(snapshots),
            "unmatched_dynamic": len(snapshots) - len(matched_snapshots),
        }
        return lots, matched_snapshots, metrics
