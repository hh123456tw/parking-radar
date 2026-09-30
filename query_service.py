"""停車查詢流程：解析輸入、找目的地、套用固定規則；不依賴 Flask，路由只負責包裝回應。"""

import re
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from threading import Lock
from zoneinfo import ZoneInfo

import database
from ai_service import TAIPEI_DISTRICTS, IntentServiceError, parse_parking_query
from analysis import (
    district_hell_score,
    rank_candidates,
    rank_district_candidates,
    select_walking_candidates,
    split_recommendation_groups,
    summarize_hour_comparison,
    summarize_matching_history,
)
from analytics_capture import infer_destination_district
from calendar_service import classify_arrival_day
from collector import collect_once
from config import Config
from database import fetch_current_lots, fetch_latest_snapshot_time, fetch_matching_history
from fee_service import build_fee_summary
from geocoder import geocode_address, geocode_candidates, resolve_known_landmark
from walking_service import WalkingRouteError, fetch_walking_routes

_refresh_lock = Lock()
LOCATION_CHOICE_CLIENT_VERSION = "2"

FACILITY_LABELS = {
    "mechanical": "機械式", "surface": "平面式",
    "underground": "地下停車場", "multi_storey": "立體停車場",
    "mixed": "混合型", "unknown": "型態待確認",
}


class ParkingDataUnavailable(RuntimeError):
    """表示資料庫沒有快照，而且官方資料也無法即時補入。"""


@dataclass
class QueryOutcome:
    """查詢結果與分析所需欄位；terminal=False 代表請使用者先選地點，不算完成查詢。"""

    body: dict
    status_code: int
    outcome_code: str
    trace: dict | None = None
    result_count: int = 0
    district: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    recommendation_groups: dict | None = None
    session_update: dict | None = None
    terminal: bool = True


def chat_message(payload, max_length):
    """聊天文字只接受字串且限制長度，超長內容不送進 Gemini。"""
    message = payload.get("message", "")
    if not isinstance(message, str):
        raise ValueError("請輸入文字目的地")
    message = message.strip()
    if len(message) > max_length:
        raise ValueError(f"問題太長，請精簡到 {max_length} 字以內")
    return message


def requires_location_confirmation(parsed):
    """沒有門牌的聊天地標必須由使用者確認，不能自動採用單一候選。"""
    original = (parsed.get("original_destination") or "").strip()
    if not original or parsed.get("destination_label"):
        return False
    return re.search(r"\d+(?:-\d+)?號", original) is None


def snapshot_age_minutes(captured_at, now=None):
    """計算 UTC 快照距現在幾分鐘；未提供快照時回傳 None。"""
    if captured_at is None:
        return None
    if captured_at.tzinfo is None:
        captured_at = captured_at.replace(tzinfo=timezone.utc)
    current = now or datetime.now(timezone.utc)
    return max(0, int((current - captured_at).total_seconds() // 60))


def elapsed_ms(started, now=None):
    """以 perf_counter 起點換算耗時毫秒；now 只供測試注入固定值。"""
    current = now if now is not None else time.perf_counter()
    return max(0, round((current - started) * 1000))


def _latest_snapshot_time():
    """使用短連線讀取全庫最新快照時間，避免刷新後沿用舊交易。"""
    connection = database.get_connection()
    try:
        return fetch_latest_snapshot_time(connection)
    finally:
        connection.close()


def ensure_fresh_parking_data(now=None):
    """查詢只讀既有快照；僅全新資料庫才同步補抓一次。"""
    latest = _latest_snapshot_time()
    age = snapshot_age_minutes(latest, now)
    if age is not None and age <= Config.FRESHNESS_MINUTES:
        return "fresh", None
    if latest is not None:
        return "stale", f"資料更新排程尚未完成，目前顯示 {age} 分鐘前資料"

    with _refresh_lock:
        # 全新資料庫才允許補抓；等鎖期間排程或其他請求可能已經寫入。
        latest = _latest_snapshot_time()
        age = snapshot_age_minutes(latest, now)
        if age is not None and age <= Config.FRESHNESS_MINUTES:
            return "fresh", None
        if latest is not None:
            return "stale", f"資料更新排程尚未完成，目前顯示 {age} 分鐘前資料"
        try:
            collect_once(timeout=Config.ON_DEMAND_FETCH_TIMEOUT_SECONDS)
            refreshed = _latest_snapshot_time()
            refreshed_age = snapshot_age_minutes(refreshed, now)
            if refreshed_age is not None and refreshed_age <= Config.FRESHNESS_MINUTES:
                return "fresh", None
            latest, age = refreshed, refreshed_age
            reason = "官方尚未提供更新"
        except Exception:
            reason = "官方更新失敗"

    if latest is None:
        raise ParkingDataUnavailable("暫時無法取得官方停車資料")
    return "stale", f"{reason}，目前顯示 {age} 分鐘前資料"


def parse_manual_payload(payload):
    """驗證手動表單並回傳與 Gemini 相同概念的普通字典。"""
    district = (payload.get("district") or "").strip()
    address = (payload.get("address") or "").strip()
    destination_label = (payload.get("destination_label") or "").strip()
    if not district and not address:
        raise ValueError("請輸入地址或選擇行政區")
    if district and district not in TAIPEI_DISTRICTS:
        raise ValueError("只支援臺北市十二行政區")
    arrival = datetime.fromisoformat(payload["arrival_time"])
    if arrival.tzinfo is None:
        raise ValueError("抵達時間必須包含時區")
    return {"intent": "recommend", "address": address or None,
            "district": district or None, "arrival_time": arrival,
            "destination_label": destination_label or None,
            # 與聊天模式共用地標別名與地址快取，避免同一地點重複查外部服務。
            "original_destination": address or None}


def validate_parsed_query(parsed, now=None):
    """驗證 Gemini 結果；未指定抵達時間時，自動使用台北現在時間。"""
    # 地標名稱也能交給 Nominatim 搜尋，例如「臺北市政府」或「資策會」。
    landmark = (parsed.get("original_destination") or "").strip()
    if not parsed.get("address") and not parsed.get("district") and landmark:
        parsed["address"] = landmark

    # Gemini 只負責抽取文字；已知地標由固定規則處理，不能讓模型猜座標。
    address_before_alias = (parsed.get("address") or "").strip()
    if landmark and address_before_alias == landmark:
        resolved_address = resolve_known_landmark(landmark)
        parsed["address"] = resolved_address
        if resolved_address != landmark:
            # 地圖服務可能把同一門牌顯示成建築名稱，畫面仍保留使用者熟悉的地標。
            parsed["destination_label"] = f"{landmark}（{resolved_address}）"

    # Gemini 可能把「信義區市府路1號」拆成兩欄，地址服務需要重新組合。
    address = (parsed.get("address") or "").strip()
    district = (parsed.get("district") or "").strip()
    if address and district:
        without_city = address.removeprefix("臺北市").removeprefix("台北市")
        if district in without_city:
            parsed["address"] = "臺北市" + without_city
        elif without_city.endswith("號"):
            parsed["address"] = "臺北市" + district + without_city
        else:
            parsed["address"] = f"{address}, {district}, 臺北市"

    missing_fields = [
        name for name in parsed.get("missing_fields", [])
        if name not in {"arrival_time", "original_destination"}
        and not (name == "address" and parsed.get("district"))
        and not (name == "district" and parsed.get("address"))
    ]
    parsed["missing_fields"] = missing_fields
    if missing_fields:
        names = "、".join(missing_fields)
        raise ValueError(f"還需要：{names}")
    if not parsed.get("address") and not parsed.get("district"):
        raise ValueError("請提供臺北市地址或行政區")
    if parsed.get("arrival_time") is None:
        parsed["arrival_time"] = now or datetime.now(ZoneInfo("Asia/Taipei"))
    if isinstance(parsed["arrival_time"], str):
        parsed["arrival_time"] = datetime.fromisoformat(parsed["arrival_time"])
        if parsed["arrival_time"].tzinfo is None:
            raise ValueError("抵達時間必須包含時區")
    elif parsed["arrival_time"].tzinfo is None:
        # Gemini 結構化輸出可能省略時區；使用者談的一律是臺北當地時間。
        parsed["arrival_time"] = parsed["arrival_time"].replace(
            tzinfo=ZoneInfo("Asia/Taipei"))
    return parsed


def attach_history(connection, rows, arrival_time):
    """一次查詢最近數天歷史，再把相同日別與小時摘要放回候選場站。"""
    end_utc = datetime.now(timezone.utc)
    start_utc = end_utc - timedelta(days=Config.HISTORY_LOOKBACK_DAYS)
    history_rows = fetch_matching_history(
        connection, [row["lot_id"] for row in rows], start_utc, end_utc)
    grouped = defaultdict(list)
    for row in history_rows:
        grouped[row["lot_id"]].append(row)
    for row in rows:
        summary = summarize_matching_history(grouped[row["lot_id"]], arrival_time)
        row["historical_hell_score"] = summary["hell_score"]
        row["history_sample_count"] = summary["sample_count"]
        row["history_comparison"] = summarize_hour_comparison(
            grouped[row["lot_id"]], arrival_time.astimezone(ZoneInfo("Asia/Taipei")).hour)
    return rows


def taipei_iso(value):
    """把 MySQL 的 naive UTC datetime 轉成台北 ISO 字串。"""
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(ZoneInfo("Asia/Taipei")).isoformat()


def enrich_candidate_metadata(row, arrival_time, day_info):
    """以本機資料補上費率、抵達日與場站型態，不修改推薦分數。"""
    row.update(build_fee_summary(
        row.get("fare_rules_json"), row.get("fee_info"),
        arrival_time, day_info["kind"]))
    facility_type = row.get("facility_type") or "unknown"
    row.update(
        arrival_day_label=day_info["label"],
        calendar_source=day_info["source"],
        facility_type=facility_type,
        facility_type_label=FACILITY_LABELS.get(facility_type, "型態待確認"),
        facility_source=row.get("facility_source") or "unknown",
    )
    return row


def public_candidate(row):
    """只輸出頁面需要的安全欄位，並把 Decimal 與 datetime 轉成 JSON 型別。"""
    keys = (
        "lot_id", "lot_name", "district", "address", "operator_type",
        "total_spaces", "available_spaces", "fee_info", "service_time",
        "hell_label", "history_sample_count", "decision_status",
        "decision_label", "pressure_label", "recommendation_label", "reasons",
        "arrival_day_label", "calendar_source",
        "hourly_fee_label", "hourly_fee_value", "daily_cap_label",
        "fee_note", "fee_confidence",
        "facility_type", "facility_type_label", "facility_source",
    )
    result = {key: row.get(key) for key in keys}
    for key in ("latitude", "longitude", "distance_m", "walking_distance_m",
                "walking_duration_minutes", "hell_score",
                "historical_hell_score", "recommendation_score"):
        result[key] = float(row[key]) if row.get(key) is not None else None
    return result


def parse_query_input(payload, session_state, max_chat_length):
    """把聊天或手動輸入轉成已驗證的查詢字典；錯誤以例外交給呼叫端分類。"""
    if payload.get("mode") == "chat":
        message = chat_message(payload, max_chat_length)
        parsed = parse_parking_query(message, session_state).model_dump()
    else:
        parsed = parse_manual_payload(payload)
    return validate_parsed_query(parsed)


def run_query(payload, trace, started, *, session_state, client_version,
              config, logger):
    """執行一次停車查詢並回傳 QueryOutcome；所有失敗都轉成對應狀態碼，不向外拋出。"""
    try:
        parsed = parse_query_input(
            payload, session_state, config["MAX_CHAT_MESSAGE_LENGTH"])
        trace["parse_ms"] = round((time.perf_counter() - started) * 1000)
        trace["parsed"] = parsed
    except IntentServiceError as exc:
        trace["error_stage"] = "parse"
        return QueryOutcome({"error": str(exc), "fallback": "manual"}, 503,
                            "failed_internal")
    except (KeyError, TypeError, ValueError) as exc:
        trace["error_stage"] = "parse"
        return QueryOutcome({"error": str(exc)}, 400, "failed_validation")

    connection = None
    try:
        geocode_started = time.perf_counter()
        # 抵達日分類只讀本機行事曆，任何異常都與資料查詢相同以 JSON 回傳。
        day_info = classify_arrival_day(parsed["arrival_time"])
        connection = database.get_connection()
        verified_choices = geocode_candidates(
            parsed.get("location_candidates", []), connection)
        needs_choice = len(verified_choices) > 1 or (
            verified_choices and requires_location_confirmation(parsed))
        if needs_choice:
            trace["geocode_ms"] = round(
                (time.perf_counter() - geocode_started) * 1000)
            if client_version != LOCATION_CHOICE_CLIENT_VERSION:
                return QueryOutcome(
                    {"error": "畫面已更新，請重新整理頁面後再查詢"},
                    409, "failed_validation", trace=trace)
            trace["location_choice_count"] = len(verified_choices)
            return QueryOutcome(
                {"needs_location_choice": True,
                 "location_choices": verified_choices,
                 "arrival_time": parsed["arrival_time"].isoformat(),
                 "intent": parsed["intent"]},
                200, "location_choice_required", trace=trace, terminal=False)

        if verified_choices:
            choice = verified_choices[0]
            parsed["address"] = choice["address"]
            parsed["district"] = choice["district"] or parsed.get("district")
            parsed["destination_label"] = \
                f'{choice["name"]}（{choice["address"]}）'
            destination = {
                "display_address": choice["display_address"],
                "latitude": choice["latitude"],
                "longitude": choice["longitude"],
            }
        else:
            destination = geocode_address(
                parsed.get("address"), connection) if parsed.get("address") else None
        if parsed.get("address") and destination is None:
            trace["error_stage"] = "geocode"
            trace["geocode_ms"] = round(
                (time.perf_counter() - geocode_started) * 1000)
            return QueryOutcome(
                {"error": "找不到地址，請修正或改選行政區",
                 "fallback": "district"},
                422, "failed_geocode", trace=trace)
        trace["geocode_ms"] = round(
            (time.perf_counter() - geocode_started) * 1000)
        trace["district"] = infer_destination_district(
            parsed.get("district"), parsed.get("address"),
            destination.get("display_address") if destination else None)

        # 地理快取查詢可能已建立 MySQL 交易快照；先關閉，避免補抓後仍讀到舊資料。
        connection.close()
        connection = None
        freshness_started = time.perf_counter()
        if config.get("AUTO_REFRESH_ENABLED", True):
            data_status, data_notice = ensure_fresh_parking_data()
        else:
            data_status, data_notice = "fresh", None
        trace["freshness_ms"] = round(
            (time.perf_counter() - freshness_started) * 1000)
        trace["data_status"] = data_status
        database_started = time.perf_counter()
        connection = database.get_connection()
        # 資料延遲時仍顯示舊資料，但不採用超過上限的快照，避免把數小時前的空位當成現況。
        freshness = (Config.FRESHNESS_MINUTES if data_status == "fresh"
                     else Config.STALE_MAX_MINUTES)
        # 已有目的地座標時以半徑篩選，不再限制行政區，避免漏掉交界對面的場站。
        rows = fetch_current_lots(
            connection, None if destination else parsed.get("district"),
            freshness)
        if not rows and data_status != "fresh":
            raise ParkingDataUnavailable("停車資料已超過 3 小時未更新，請稍後再試")
        if destination:
            # 一般查詢只使用即時資料與距離；歷史由使用者點擊後的專用 API 載入。
            ranked = rank_candidates(
                rows, destination["latitude"], destination["longitude"])
            api_key = config.get("OPENROUTESERVICE_API_KEY", "")
            if api_key:
                route_rows = select_walking_candidates(
                    ranked, limit=config["WALKING_ROUTE_CANDIDATE_LIMIT"])
                walking_started = time.perf_counter()
                try:
                    walking_routes = fetch_walking_routes(
                        route_rows,
                        destination["latitude"], destination["longitude"],
                        api_key,
                        timeout=config["WALKING_ROUTE_TIMEOUT_SECONDS"],
                    )
                    for row in ranked:
                        row.update(walking_routes.get(row["lot_id"], {}))
                except WalkingRouteError as exc:
                    logger.warning("%s，改用直線距離", exc)
                finally:
                    trace["walking_ms"] = round(
                        (time.perf_counter() - walking_started) * 1000)
            score_rows = ranked
        else:
            # 行政區可能包含大量場站，避免每次查詢都讀取歷史快照。
            ranked = rank_district_candidates(rows)
            score_rows = rows
        # 每個候選都補上本機的抵達日、費率與型態，不影響排序分數。
        for row in ranked:
            enrich_candidate_metadata(row, parsed["arrival_time"], day_info)
        if parsed["intent"] in {"history", "compare"}:
            # 只有明確詢問歷史時才載入前三座的最近 7 天資料。
            attach_history(connection, ranked[:3], parsed["arrival_time"])
        raw_groups = split_recommendation_groups(ranked)
        # 清單欄位轉成公開格式；統計數字保持整數，避免混用同一種序列化流程。
        groups = {
            name: [public_candidate(row) for row in raw_groups[name]]
            for name in ("recommendations", "other_recommended", "warning")
        }
        groups.update(
            recommended_count=raw_groups["recommended_count"],
            excluded_count=raw_groups["excluded_count"],
        )
        destination_json = None if destination is None else {
            "display_address": parsed.get("destination_label")
            or destination["display_address"],
            "latitude": float(destination["latitude"]),
            "longitude": float(destination["longitude"]),
        }
        first = ranked[0] if ranked else None
        session_update = dict(
            destination=parsed.get("address"), district=parsed.get("district"),
            arrival_time=parsed["arrival_time"].isoformat(),
            lot_id=ranked[0]["lot_id"] if ranked else None)
        trace["collected_at"] = max(
            (row["captured_at"] for row in rows), default=None)
        trace["official_data_at"] = max(
            (row.get("snapshot_updated_at") for row in rows
             if row.get("snapshot_updated_at") is not None),
            default=None,
        )
        collected_at = taipei_iso(trace["collected_at"])
        official_updated_at = taipei_iso(trace["official_data_at"])
        total_ms = round((time.perf_counter() - started) * 1000)
        trace["database_ms"] = max(
            0,
            round((time.perf_counter() - database_started) * 1000)
            - (trace["walking_ms"] or 0),
        )
        trace["result_count"] = len(ranked)
        logger.info(
            "query_complete mode=%s parse_ms=%s geocode_ms=%s "
            "freshness_ms=%s database_ms=%s walking_ms=%s total_ms=%s",
            "chat" if payload.get("mode") == "chat" else "manual",
            trace["parse_ms"], trace["geocode_ms"], trace["freshness_ms"],
            trace["database_ms"], trace["walking_ms"], total_ms,
        )
        body = {
            "destination": destination_json,
            "current": {
                "district_score": district_hell_score(score_rows),
                "valid_lot_count": len(score_rows),
            },
            "history": {
                "hell_score": first.get("historical_hell_score") if first else None,
                "sample_count": first.get("history_sample_count", 0) if first else 0,
                "comparison": first.get("history_comparison") if first else None,
            },
            "intent": parsed["intent"],
            "official_updated_at": official_updated_at,
            "collected_at": collected_at,
            "updated_at": collected_at,
            "data_status": data_status,
            "data_notice": data_notice,
        }
        body.update(groups)
        if not ranked:
            outcome = "failed_no_candidates"
        else:
            outcome = ("degraded_stale_data" if data_status == "stale"
                       else "success")
        destination_coords = destination or {}
        return QueryOutcome(
            body, 200, outcome, trace=trace,
            result_count=len(ranked),
            district=parsed.get("district"),
            latitude=destination_coords.get("latitude"),
            longitude=destination_coords.get("longitude"),
            recommendation_groups=groups,
            session_update=session_update,
        )
    except ParkingDataUnavailable as exc:
        trace["error_stage"] = "database"
        return QueryOutcome({"error": str(exc)}, 503, "failed_database",
                            trace=trace)
    except Exception:
        logger.exception("停車查詢失敗")
        trace["error_stage"] = "internal"
        return QueryOutcome({"error": "服務暫時無法使用，請稍後再試"},
                            503, "failed_internal", trace=trace)
    finally:
        if connection is not None:
            connection.close()
