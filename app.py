"""Flask 入口；集中協調查詢流程，不在路由內重寫分析公式。"""

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from flask import Flask, jsonify, render_template, request, session
from ai_service import IntentServiceError, TAIPEI_DISTRICTS, parse_parking_query
from analysis import (build_history_series, district_hell_score,
                      rank_candidates, rank_district_candidates,
                      split_recommendation_groups, summarize_hour_comparison,
                      summarize_matching_history)
from config import Config
from database import (fetch_current_lots, fetch_history,
                      fetch_matching_history, get_connection)
from geocoder import geocode_address


def parse_manual_payload(payload):
    """驗證手動表單並回傳與 Gemini 相同概念的普通字典。"""
    district = (payload.get("district") or "").strip()
    address = (payload.get("address") or "").strip()
    if not district and not address:
        raise ValueError("請輸入地址或選擇行政區")
    if district and district not in TAIPEI_DISTRICTS:
        raise ValueError("只支援臺北市十二行政區")
    arrival = datetime.fromisoformat(payload["arrival_time"])
    if arrival.tzinfo is None:
        raise ValueError("抵達時間必須包含時區")
    return {"intent": "recommend", "address": address or None,
            "district": district or None, "arrival_time": arrival}


def validate_parsed_query(parsed):
    """統一驗證 Gemini 與手動結果，避免缺少條件時進入資料庫分析。"""
    if parsed.get("missing_fields"):
        names = "、".join(parsed["missing_fields"])
        raise ValueError(f"還需要：{names}")
    if not parsed.get("address") and not parsed.get("district"):
        raise ValueError("請提供臺北市地址或行政區")
    if parsed.get("arrival_time") is None:
        raise ValueError("請提供預計抵達時間")
    if isinstance(parsed["arrival_time"], str):
        parsed["arrival_time"] = datetime.fromisoformat(parsed["arrival_time"])
        if parsed["arrival_time"].tzinfo is None:
            raise ValueError("抵達時間必須包含時區")
    return parsed


def attach_history(connection, rows, arrival_time):
    """一次查詢 30 天歷史，再把相同日別與小時摘要放回各候選場站。"""
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


def public_candidate(row):
    """只輸出頁面需要的安全欄位，並把 Decimal 與 datetime 轉成 JSON 型別。"""
    keys = ("lot_id", "lot_name", "district", "address", "operator_type",
            "total_spaces", "available_spaces", "fee_info", "service_time",
            "hell_label", "history_sample_count")
    result = {key: row.get(key) for key in keys}
    for key in ("latitude", "longitude", "distance_m", "hell_score",
                "historical_hell_score", "recommendation_score"):
        result[key] = float(row[key]) if row.get(key) is not None else None
    return result


def create_app(test_config=None):
    """建立 Flask 應用，允許測試覆寫設定並回傳 app。"""
    app = Flask(__name__)
    app.config.from_object(Config)
    if test_config:
        app.config.update(test_config)

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
        payload = request.get_json(silent=True) or {}
        if not isinstance(payload, dict):
            return jsonify(error="JSON 內容必須是物件"), 400
        try:
            if payload.get("mode") == "chat":
                parsed = parse_parking_query(payload.get("message", ""), dict(session)).model_dump()
            else:
                parsed = parse_manual_payload(payload)
            parsed = validate_parsed_query(parsed)
        except IntentServiceError as exc:
            return jsonify(error=str(exc), fallback="manual"), 503
        except (KeyError, TypeError, ValueError) as exc:
            return jsonify(error=str(exc)), 400

        connection = None
        try:
            connection = get_connection()
            destination = geocode_address(parsed.get("address"), connection) if parsed.get("address") else None
            if parsed.get("address") and destination is None:
                return jsonify(error="找不到地址，請修正或改選行政區", fallback="district"), 422
            rows = fetch_current_lots(connection, parsed.get("district"), Config.FRESHNESS_MINUTES)
            if destination:
                # 先用即時資料與距離縮到 1.5 公里，再查這批候選的歷史，避免讀取全市歷史。
                nearby = rank_candidates(rows, destination["latitude"], destination["longitude"])
                nearby = attach_history(connection, nearby, parsed["arrival_time"])
                ranked = rank_candidates(nearby, destination["latitude"], destination["longitude"])
                score_rows = ranked
            else:
                rows = attach_history(connection, rows, parsed["arrival_time"])
                ranked = rank_district_candidates(rows)
                score_rows = rows
            raw_groups = split_recommendation_groups(ranked)
            groups = {name: [public_candidate(row) for row in group]
                      for name, group in raw_groups.items()}
            destination_json = None if destination is None else {
                "display_address": destination["display_address"],
                "latitude": float(destination["latitude"]),
                "longitude": float(destination["longitude"]),
            }
            first = ranked[0] if ranked else None
            session.update(destination=parsed.get("address"), district=parsed.get("district"),
                           arrival_time=parsed["arrival_time"].isoformat(),
                           lot_id=ranked[0]["lot_id"] if ranked else None)
            updated_at = max((row["captured_at"] for row in rows), default=None)
            if updated_at is not None and updated_at.tzinfo is None:
                updated_at = updated_at.replace(tzinfo=timezone.utc)
            updated_at = updated_at.astimezone(ZoneInfo("Asia/Taipei")).isoformat() if updated_at else None
            return jsonify(destination=destination_json, current={
                "district_score": district_hell_score(score_rows),
                "valid_lot_count": len(score_rows)},
                history={"hell_score": first.get("historical_hell_score") if first else None,
                         "sample_count": first.get("history_sample_count", 0) if first else 0,
                         "comparison": first.get("history_comparison") if first else None},
                intent=parsed["intent"],
                updated_at=updated_at, **groups)
        except Exception:
            app.logger.exception("停車查詢失敗")
            return jsonify(error="服務暫時無法使用，請稍後再試"), 503
        finally:
            if connection is not None:
                connection.close()

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
