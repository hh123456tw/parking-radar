"""Flask 入口；集中協調查詢流程，不在路由內重寫分析公式。"""

import logging
import re
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from threading import Lock
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from flask import Flask, jsonify, render_template, request, session
from ai_service import IntentServiceError, TAIPEI_DISTRICTS, parse_parking_query
from analytics_database import insert_event, insert_navigation_event
from analytics_service import (SOURCES, analytics_identity,
                               build_browser_event, build_query_event)
from analysis import (build_history_series, district_hell_score,
                      rank_candidates, rank_district_candidates,
                      select_walking_candidates, split_recommendation_groups,
                      summarize_hour_comparison,
                      summarize_matching_history)
from calendar_service import classify_arrival_day
from config import Config
from collector import collect_once
from database import (fetch_current_lots, fetch_history,
                      fetch_latest_snapshot_time, fetch_matching_history,
                      get_connection)
from fee_service import build_fee_summary
from geocoder import geocode_address, geocode_candidates, resolve_known_landmark
from walking_service import WalkingRouteError, fetch_walking_routes

_refresh_lock = Lock()
LOCATION_CHOICE_CLIENT_VERSION = "2"


class ParkingDataUnavailable(RuntimeError):
    """表示資料庫沒有快照，而且官方資料也無法即時補入。"""


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


def _latest_snapshot_time():
    """使用短連線讀取全庫最新快照時間，避免刷新後沿用舊交易。"""
    connection = get_connection()
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


FACILITY_LABELS = {
    "mechanical": "機械式", "surface": "平面式",
    "underground": "地下停車場", "multi_storey": "立體停車場",
    "mixed": "混合型", "unknown": "型態待確認",
}


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
        "hourly_fee_label", "daily_cap_label", "fee_note", "fee_confidence",
        "facility_type", "facility_type_label", "facility_source",
    )
    result = {key: row.get(key) for key in keys}
    for key in ("latitude", "longitude", "distance_m", "walking_distance_m",
                "walking_duration_minutes", "hell_score",
                "historical_hell_score", "recommendation_score"):
        result[key] = float(row[key]) if row.get(key) is not None else None
    return result


def create_app(test_config=None):
    """建立 Flask 應用，允許測試覆寫設定並回傳 app。"""
    app = Flask(__name__)
    app.logger.setLevel(logging.INFO)
    app.config.from_object(Config)
    if test_config:
        app.config.update(test_config)

    def analytics_writer(event):
        """開啟新連線寫入事件，成功才提交；任何錯誤都回復後關閉。"""
        connection = get_connection()
        try:
            if event["event_type"] == "navigation_clicked":
                insert_navigation_event(connection, event)
            else:
                insert_event(connection, event)
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    app.extensions["analytics_writer"] = analytics_writer

    def write_analytics_safely(event):
        """分析寫入失敗只能留下不含目的地的警告，不得影響查詢。"""
        if not event:
            return
        try:
            app.extensions["analytics_writer"](event)
        except Exception:
            app.logger.warning(
                "analytics_write_failed event=%s", event["event_type"])

    def record_query_event(outcome_code, query_mode, request_id,
                           anonymous_hash, query_source, duration_ms,
                           result_count=0, district=None, latitude=None,
                           longitude=None):
        """在最佳努力隔離內建構並寫入查詢事件，任何異常都不外洩。"""
        if not anonymous_hash:
            return
        try:
            event = build_query_event(
                event_type="query_completed"
                if outcome_code.startswith("success")
                or outcome_code.startswith("degraded")
                else "query_failed",
                request_id=request_id,
                anonymous_id_hash=anonymous_hash,
                query_mode=query_mode,
                outcome_code=outcome_code,
                duration_ms=duration_ms,
                result_count=result_count,
                source=query_source,
                district=district,
                latitude=latitude,
                longitude=longitude,
            )
        except Exception:
            app.logger.warning("analytics_event_build_failed")
            return
        write_analytics_safely(event)

    def terminal(payload, status_code, outcome_code, query_mode, request_id,
                 anonymous_hash, query_source, duration_ms, result_count=0,
                 district=None, latitude=None, longitude=None):
        """加上 request_id 回傳終端 JSON，並記錄同意下的對應事件。"""
        payload["request_id"] = request_id
        record_query_event(
            outcome_code, query_mode, request_id, anonymous_hash,
            query_source, duration_ms, result_count, district, latitude,
            longitude)
        return jsonify(payload), status_code

    @app.after_request
    def allow_service_worker_root_scope(response):
        """讓 Flask 本機環境的服務器腳本也能控制網站根目錄。"""
        if request.path == "/static/sw.js":
            response.headers["Service-Worker-Allowed"] = "/"
        return response

    @app.get("/health")
    def health():
        """回傳不依賴外部服務的程序健康狀態。"""
        return jsonify(status="ok")

    @app.get("/")
    def index():
        """顯示唯一主頁，資料由前端呼叫 JSON API 載入。"""
        return render_template("index.html")

    @app.post("/api/query")
    def query_parking():
        """解析手動或聊天輸入，交由固定函式產生可驗證的停車結果。"""
        query_started = time.perf_counter()
        request_id = str(uuid4())
        anonymous_hash = analytics_identity(
            request.headers, app.config.get("ANALYTICS_HMAC_SECRET", ""))
        query_source = request.headers.get("X-Analytics-Source", "unknown")
        timings = {"walking_ms": 0}
        payload = request.get_json(silent=True) or {}
        query_mode = "chat" if isinstance(payload, dict) and \
            payload.get("mode") == "chat" else "manual"
        if not isinstance(payload, dict):
            return terminal({"error": "JSON 內容必須是物件"}, 400,
                            "failed_validation", query_mode, request_id,
                            anonymous_hash, query_source, 0)
        try:
            if payload.get("mode") == "chat":
                parsed = parse_parking_query(payload.get("message", ""), dict(session)).model_dump()
            else:
                parsed = parse_manual_payload(payload)
            parsed = validate_parsed_query(parsed)
            timings["parse_ms"] = round((time.perf_counter() - query_started) * 1000)
        except IntentServiceError as exc:
            return terminal({"error": str(exc), "fallback": "manual"}, 503,
                            "failed_internal", query_mode, request_id,
                            anonymous_hash, query_source, 0)
        except (KeyError, TypeError, ValueError) as exc:
            return terminal({"error": str(exc)}, 400, "failed_validation",
                            query_mode, request_id, anonymous_hash,
                            query_source, 0)

        connection = None
        try:
            geocode_started = time.perf_counter()
            # 抵達日分類只讀本機行事曆，任何異常都與資料查詢相同以 JSON 回傳。
            day_info = classify_arrival_day(parsed["arrival_time"])
            connection = get_connection()
            verified_choices = geocode_candidates(
                parsed.get("location_candidates", []), connection)
            needs_choice = len(verified_choices) > 1 or (
                verified_choices and requires_location_confirmation(parsed))
            if needs_choice:
                if request.headers.get("X-Client-Version") != \
                        LOCATION_CHOICE_CLIENT_VERSION:
                    return terminal(
                        {"error": "畫面已更新，請重新整理頁面後再查詢"},
                        409, "failed_validation", query_mode, request_id,
                        anonymous_hash, query_source, 0)
                return jsonify(
                    needs_location_choice=True,
                    location_choices=verified_choices,
                    arrival_time=parsed["arrival_time"].isoformat(),
                    intent=parsed["intent"],
                    request_id=request_id,
                )

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
                return terminal(
                    {"error": "找不到地址，請修正或改選行政區",
                     "fallback": "district"},
                    422, "failed_geocode", query_mode, request_id,
                    anonymous_hash, query_source, 0)
            timings["geocode_ms"] = round(
                (time.perf_counter() - geocode_started) * 1000)

            # 地理快取查詢可能已建立 MySQL 交易快照；先關閉，避免補抓後仍讀到舊資料。
            connection.close()
            connection = None
            freshness_started = time.perf_counter()
            if app.config.get("AUTO_REFRESH_ENABLED", True):
                data_status, data_notice = ensure_fresh_parking_data()
            else:
                data_status, data_notice = "fresh", None
            timings["freshness_ms"] = round(
                (time.perf_counter() - freshness_started) * 1000)
            database_started = time.perf_counter()
            connection = get_connection()
            freshness = Config.FRESHNESS_MINUTES if data_status == "fresh" else None
            rows = fetch_current_lots(connection, parsed.get("district"), freshness)
            if destination:
                # 一般查詢只使用即時資料與距離；歷史由使用者點擊後的專用 API 載入。
                ranked = rank_candidates(
                    rows, destination["latitude"], destination["longitude"])
                api_key = app.config.get("OPENROUTESERVICE_API_KEY", "")
                if api_key:
                    route_rows = select_walking_candidates(
                        ranked,
                        limit=app.config["WALKING_ROUTE_CANDIDATE_LIMIT"],
                    )
                    try:
                        walking_started = time.perf_counter()
                        walking_routes = fetch_walking_routes(
                            route_rows,
                            destination["latitude"], destination["longitude"],
                            api_key,
                            timeout=app.config["WALKING_ROUTE_TIMEOUT_SECONDS"],
                        )
                        for row in ranked:
                            row.update(walking_routes.get(row["lot_id"], {}))
                    except WalkingRouteError as exc:
                        app.logger.warning("%s，改用直線距離", exc)
                    finally:
                        timings["walking_ms"] = round(
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
            groups = {name: [public_candidate(row) for row in group]
                      for name, group in raw_groups.items()}
            destination_json = None if destination is None else {
                "display_address": parsed.get("destination_label")
                or destination["display_address"],
                "latitude": float(destination["latitude"]),
                "longitude": float(destination["longitude"]),
            }
            first = ranked[0] if ranked else None
            session.update(destination=parsed.get("address"), district=parsed.get("district"),
                           arrival_time=parsed["arrival_time"].isoformat(),
                           lot_id=ranked[0]["lot_id"] if ranked else None)
            collected_at = taipei_iso(max(
                (row["captured_at"] for row in rows), default=None))
            official_updated_at = taipei_iso(max(
                (row.get("snapshot_updated_at") for row in rows
                 if row.get("snapshot_updated_at") is not None),
                default=None,
            ))
            total_ms = round((time.perf_counter() - query_started) * 1000)
            timings["database_ms"] = max(
                0,
                round((time.perf_counter() - database_started) * 1000)
                - timings["walking_ms"],
            )
            app.logger.info(
                "query_complete mode=%s parse_ms=%s geocode_ms=%s "
                "freshness_ms=%s database_ms=%s walking_ms=%s total_ms=%s",
                "chat" if payload.get("mode") == "chat" else "manual",
                timings["parse_ms"],
                timings["geocode_ms"], timings["freshness_ms"],
                timings["database_ms"], timings["walking_ms"], total_ms,
            )
            payload = {
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
            payload.update(groups)
            if not ranked:
                outcome = "failed_no_candidates"
            else:
                outcome = ("degraded_stale_data" if data_status == "stale"
                           else "success")
            destination_coords = destination or {}
            return terminal(
                payload, 200, outcome,
                query_mode, request_id, anonymous_hash, query_source,
                round((time.perf_counter() - query_started) * 1000),
                result_count=len(ranked),
                district=parsed.get("district"),
                latitude=destination_coords.get("latitude"),
                longitude=destination_coords.get("longitude"),
            )
        except ParkingDataUnavailable as exc:
            return terminal({"error": str(exc)}, 503, "failed_database",
                            query_mode, request_id, anonymous_hash,
                            query_source, 0)
        except Exception:
            app.logger.exception("停車查詢失敗")
            return terminal(
                {"error": "服務暫時無法使用，請稍後再試"},
                503, "failed_internal", query_mode, request_id,
                anonymous_hash, query_source, 0)
        finally:
            if connection is not None:
                connection.close()

    @app.post("/api/analytics/events")
    def analytics_events():
        """接受固定白名單的 pwa_opened/navigation_clicked，失敗不影響前端。"""
        if not app.config.get("ANALYTICS_ENABLED", True):
            return "", 204
        if not app.config.get("ANALYTICS_HMAC_SECRET", ""):
            return "", 204
        payload = request.get_json(silent=True) or {}
        if not isinstance(payload, dict):
            return jsonify(error="JSON 內容必須是物件"), 400
        allowed_keys = {
            "event_type", "analytics_id", "request_id", "clicked_rank",
            "parking_lot_id", "walking_minutes", "availability_bucket",
            "source",
        }
        if not allowed_keys.issuperset(payload):
            return jsonify(error="不接受未知欄位"), 400
        event_type = payload.get("event_type")
        if event_type not in {"pwa_opened", "navigation_clicked"}:
            return jsonify(error="不接受的事件類型"), 400
        source = payload.get("source")
        if source not in SOURCES:
            return jsonify(error="不接受的事件來源"), 400
        raw_id = payload.get("analytics_id")
        try:
            UUID(raw_id)
        except (TypeError, ValueError, AttributeError):
            return jsonify(error="analytics_id 必須是 UUID"), 400
        # sendBeacon 無法帶自訂標頭；前端只在明確同意後才送出 body UUID，故以此計算 HMAC。
        anonymous_hash = analytics_identity(
            {"X-Analytics-Consent": "1", "X-Analytics-Id": raw_id},
            app.config.get("ANALYTICS_HMAC_SECRET", ""))
        if anonymous_hash is None:
            return jsonify(error="需要明確同意與合法 UUID"), 400
        event_kwargs = {
            "event_type": event_type,
            "anonymous_id_hash": anonymous_hash,
            "source": source,
        }
        if event_type == "navigation_clicked":
            request_id = payload.get("request_id")
            try:
                UUID(request_id)
            except (TypeError, ValueError, AttributeError):
                return jsonify(error="request_id 必須是 UUID"), 400
            clicked_rank = payload.get("clicked_rank")
            walking_minutes = payload.get("walking_minutes")
            availability_bucket = payload.get("availability_bucket")
            if not isinstance(clicked_rank, int) or isinstance(
                    clicked_rank, bool) or not 1 <= clicked_rank <= 99:
                return jsonify(error="clicked_rank 必須是 1-99 的整數"), 400
            if not isinstance(payload.get("parking_lot_id"), str) or not \
                    payload["parking_lot_id"].strip():
                return jsonify(error="parking_lot_id 不能為空"), 400
            if walking_minutes is not None and (
                    isinstance(walking_minutes, bool)
                    or not isinstance(walking_minutes, (int, float))
                    or not 0 <= walking_minutes <= 999):
                return jsonify(error="walking_minutes 必須是非負數字"), 400
            if availability_bucket not in {"0", "1_3", "4_10", "11_plus"}:
                return jsonify(error="availability_bucket 不在允許清單"), 400
            event_kwargs.update({
                "request_id": request_id,
                "clicked_rank": clicked_rank,
                "parking_lot_id": payload["parking_lot_id"].strip(),
                "walking_minutes": walking_minutes,
                "availability_bucket": availability_bucket,
            })
        event = build_browser_event(**event_kwargs)
        write_analytics_safely(event)
        return "", 204

    @app.get("/api/parking/<lot_id>/history")
    def parking_history(lot_id):
        """回傳單一場站最近七天的有效空位序列供唯一折線圖使用。"""
        end_utc = datetime.now(timezone.utc)
        start_utc = end_utc - timedelta(days=7)
        connection = None
        try:
            connection = get_connection()
            rows = fetch_history(connection, lot_id, start_utc, end_utc)
            return jsonify(lot_id=lot_id, points=build_history_series(rows))
        except Exception:
            app.logger.exception("歷史查詢失敗")
            return jsonify(error="暫時無法取得歷史資料"), 503
        finally:
            if connection is not None:
                connection.close()

    return app


app = create_app()
