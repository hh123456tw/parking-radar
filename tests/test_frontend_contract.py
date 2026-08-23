"""前端圖卡契約測試：避免地址、容量、理由與兩種操作被意外移除。"""

from pathlib import Path

ROOT = Path(__file__).parents[1]


def test_decision_cards_keep_required_data_and_actions():
    script = (ROOT / "static" / "app.js").read_text(encoding="utf-8")

    assert "function formatFullAddress(lot)" in script
    assert "function googleMapsUrl(lot)" in script
    assert "https://www.google.com/maps/dir/?api=1&travelmode=driving&destination=" in script
    assert "https://www.google.com/maps/search/?api=1&query=" not in script
    assert "開始導航" in script
    assert "lot.total_spaces" in script
    assert "lot.reasons" in script
    assert "data.official_updated_at" in script
    assert "data.collected_at" in script
    assert 'target="_blank"' in script
    assert 'rel="noopener noreferrer"' in script
    assert 'data-history-lot="${escapeHtml(lot.lot_id)}"' in script
    assert "function compactLot(lot, index)" in script
    assert "data.data_notice" in script
    assert "score-details" not in script


def test_cards_and_map_distinguish_walking_route_from_straight_fallback():
    """有路線時顯示步行分鐘；降級時必須明說是直線距離。"""
    script = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
    template = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")

    assert "function formatProximity(lot)" in script
    assert "lot.walking_duration_minutes" in script
    assert "lot.walking_distance_m" in script
    assert "步行約" in script
    assert "直線約" in script
    assert script.count("formatProximity(lot)") >= 5
    assert "同風險場站按步行時間排序" in template
    assert "© openrouteservice.org by HeiGIT" in template


def test_query_has_timeout_and_history_does_not_block_cards():
    """Gemini 或歷史 API 變慢時，頁面不得永久停在分析中。"""
    script = (ROOT / "static" / "app.js").read_text(encoding="utf-8")

    assert "new AbortController()" in script
    assert "QUERY_TIMEOUT_MS = 20000" in script
    assert "分析超過 20 秒，請重試或改用手動查詢" in script
    assert "MIN_HISTORY_POINTS = 8" in script
    assert "data.points.length < MIN_HISTORY_POINTS" in script
    assert "loadHistory(data.recommendations[0]" not in script


def test_map_emphasizes_ranked_recommendations():
    """地圖首選必須有永久名次，側欄可定位並開啟 popup。"""
    script = (ROOT / "static" / "app.js").read_text(encoding="utf-8")

    assert "bindTooltip(String(index + 1)" in script
    assert 'data-map-lot="${escapeHtml(lot.lot_id)}"' in script
    assert "marker.openPopup()" in script
    assert 'lot.decision_status === "warning" ? "#f2c94c" : "#36c98f"' in script


def test_promoted_backup_card_keeps_warning_label():
    """低風險不足時補入的黃色場站，不能在卡片上假裝成綠色首選。"""
    script = (ROOT / "static" / "app.js").read_text(encoding="utf-8")

    assert 'const rankLabel = isBackup ? "備選" : "首選"' in script
    assert 'class="parking-card ${cardTone}"' in script


def test_primary_cards_offer_scrollable_official_fee_details():
    """首選卡保留完整官方文字，缺值有提示，長內容不無限撐高卡片。"""
    script = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
    style = (ROOT / "static" / "style.css").read_text(encoding="utf-8")
    primary_card = script.split("function primaryCard", 1)[1].split(
        "function compactLot", 1)[0]
    compact_lot = script.split("function compactLot", 1)[1].split(
        "function renderCards", 1)[0]

    assert 'class="parking-details"' in primary_card
    assert "費率與營業時間" in primary_card
    assert "lot.fee_info" in primary_card
    assert "lot.service_time" in primary_card
    assert "escapeHtml(feeInfo)" in primary_card
    assert "escapeHtml(serviceTime)" in primary_card
    assert "官方未提供" in primary_card
    assert "parking-details" not in compact_lot
    assert "max-height:160px" in style
    assert "overflow-y:auto" in style
    assert "white-space:pre-wrap" in style
    assert "overflow-wrap:anywhere" in style


def test_decision_metadata_renders_on_primary_and_compact_cards():
    """抵達日、時費、每日上限與場站型態要渲染於首選卡與緊湊列，值都經轉義。"""
    script = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
    primary_card = script.split("function primaryCard", 1)[1].split(
        "function compactLot", 1)[0]
    compact_lot = script.split("function compactLot", 1)[1].split(
        "function renderCards", 1)[0]

    for field in (
        "lot.arrival_day_label", "lot.hourly_fee_label",
        "lot.daily_cap_label", "lot.facility_type_label",
    ):
        assert f"displayValue({field}" in primary_card
        assert f"displayValue({field}" in compact_lot

    assert "decision-meta" in primary_card
    assert "compact-meta" in compact_lot
    assert "上限官方未標示" in primary_card
    assert "上限官方未標示" in compact_lot
    assert "型態待確認" in primary_card
    assert "型態待確認" in compact_lot
    assert "fee-note" in primary_card
    assert "fee-note" in compact_lot
    assert "parking-details" in primary_card
    assert "parking-details" not in compact_lot


def test_location_choices_are_clickable_and_reuse_manual_query():
    """模糊地標候選必須顯示成按鈕，點擊後沿用既有手動查詢 API。"""
    script = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
    template = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")

    assert "needs_location_choice" in script
    assert '"X-Client-Version":CLIENT_VERSION' in script
    assert 'data-location-choice="${index}"' in script
    assert 'mode:"manual"' in script
    assert 'destination_label:`${choice.name}（${choice.address}）`' in script
    assert 'id="location-choice-section"' in template
    assert 'id="result-content"' in template
    assert "analytics-v1" in template
    assert 'document.querySelector("#result-content").hidden = true' in script


def test_opt_in_controls_and_privacy_link_exist():
    """同意橫幅、隱私說明與本機 UUID 是分析的最小入口。"""
    template = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
    script = (ROOT / "static" / "app.js").read_text(encoding="utf-8")

    assert 'id="analytics-consent"' in template
    assert 'id="analytics-accept"' in template
    assert 'id="analytics-decline"' in template
    assert "parking_analytics_consent" in script
    assert "crypto.randomUUID()" in script


def test_consent_banner_copy_is_exact():
    """同意橫幅文案必須逐字保留，不能淡化隱私承諾。"""
    template = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
    copy = "是否允許匿名使用分析？只記錄查詢是否成功、速度與導航點擊；不保存地址、對話、IP 或手機位置，90 天後刪除。"
    assert copy in template
    assert "允許匿名分析" in template
    assert "不要分析" in template
    assert "查看隱私說明" in template


def test_footer_offers_privacy_note_and_change_choice():
    """頁尾要有隱私說明錨點與可重新開啟選擇的控制項。"""
    template = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
    assert 'id="privacy-note"' in template
    assert 'id="analytics-choice"' in template


def test_navigation_uses_beacon_with_keepalive_fallback():
    """導航點擊先送 sendBeacon，不可用或失敗時退回 keepalive fetch。"""
    script = (ROOT / "static" / "app.js").read_text(encoding="utf-8")

    assert "navigator.sendBeacon" in script
    assert "keepalive:true" in script.replace(" ", "")
    assert "data-navigation-rank" in script


def test_navigation_capture_uses_single_delegated_handler():
    """導航分析只靠一個委派的 document 點擊處理器，連結不得內嵌 JS。"""
    template = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
    script = (ROOT / "static" / "app.js").read_text(encoding="utf-8")

    assert 'document.addEventListener("click"' in script
    assert 'closest("a[data-navigation-rank]")' in script
    assert "onclick=" not in template


def test_navigation_payload_is_allowlisted_scalars_from_attributes():
    """導航事件只能由 data-* 屬性組成白名單純量，帶最新 request_id。"""
    script = (ROOT / "static" / "app.js").read_text(encoding="utf-8")

    assert 'event_type:"navigation_clicked"' in script
    assert "analytics_id:" in script
    assert "request_id:activeRequestId" in script
    assert "parking_lot_id:" in script
    assert "availability_bucket:" in script


def test_active_request_id_updates_only_from_terminal_result():
    """只有終端查詢結果成功時才更新 activeRequestId，供導航事件使用。"""
    script = (ROOT / "static" / "app.js").read_text(encoding="utf-8")

    assert "let activeRequestId" in script
    assert "activeRequestId = data.request_id" in script


def test_active_request_id_resets_before_each_query():
    """新查詢開始要先清空 activeRequestId，失敗或進行中點擊不能連到上一筆。"""
    script = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
    submit = script.split("async function submitQuery(payload)", 1)[1].split(
        'document.querySelector("#chat-form")', 1)[0]

    assert "activeRequestId = null" in submit
    assert submit.index("activeRequestId = null") < submit.index('fetch("/api/query"')


def test_pwa_open_and_navigation_event_types_exist():
    """同意後每頁載入記錄一次 pwa_opened，導航點擊記錄 navigation_clicked。"""
    script = (ROOT / "static" / "app.js").read_text(encoding="utf-8")

    assert '"pwa_opened"' in script
    assert '"navigation_clicked"' in script
