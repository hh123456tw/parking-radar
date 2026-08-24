"""前端圖卡契約測試：避免地址、容量、理由與兩種操作被意外移除。"""

from pathlib import Path

ROOT = Path(__file__).parents[1]
DASHBOARD_TEMPLATE = ROOT / "templates" / "admin_analytics.html"
DASHBOARD_SCRIPT = ROOT / "static" / "admin_analytics.js"


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
    assert "function compactLot(lot)" in script
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
    assert "優先顯示所有可以前往的場站" in template
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

    assert 'isBackup ? "備選" : (index === 0 ? "首選" : "選擇")' in script
    assert 'class="parking-card ${cardTone}"' in script


def test_self_use_results_prioritize_all_safe_lots_and_hide_avoid_cards():
    """自用版要保留其他安全場站，避雷只顯示排除數量而非場站卡片。"""
    script = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
    template = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")

    assert "data.other_recommended" in script
    assert "data.recommended_count" in script
    assert "data.excluded_count" in script
    assert "data.avoid" not in script
    assert 'lot.decision_status === "avoid"' not in script
    assert ".compact-lot.avoid" not in (
        ROOT / "static" / "style.css").read_text(encoding="utf-8")
    assert 'id="excluded-summary"' in template
    assert 'id="other-title"' in template
    assert "其他可以前往" in script
    assert "已排除" in script


def test_compact_lot_recommended_uses_green_accent():
    """安全場站的精簡列沿用綠色 --accent，避免未定義變數落到近似白色。"""
    style = (ROOT / "static" / "style.css").read_text(encoding="utf-8")
    assert ".compact-lot.recommended { --accent:var(--green); }" in style


def test_top_three_use_equal_cards_and_collapse_remaining_lots():
    """前三個選擇必須共用同一張卡片，其他場站仍預設收合。"""
    script = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
    style = (ROOT / "static" / "style.css").read_text(encoding="utf-8")
    template = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")

    assert "recommendations.map((lot, index) => primaryCard(lot, index)).join" in script
    assert "function alternativeCard" not in script
    assert "grid-template-columns:repeat(3,minmax(0,1fr))" in style
    assert "height:100%" in style.split(".parking-card", 1)[1].split("}", 1)[0]
    assert ".parking-card:first-child" not in style
    assert ".parking-card:nth-child" not in style
    assert 'id="other-toggle"' in template
    assert 'aria-controls="other-lots"' in template
    assert 'id="other-lots" class="compact-list" hidden' in template
    assert 'toggle.setAttribute("aria-expanded", "false")' in script


def test_exact_hourly_prices_can_show_a_cheapest_badge_without_resorting():
    """最便宜只由精確時費產生標籤，不得改變既有安全與距離排序。"""
    script = (ROOT / "static" / "app.js").read_text(encoding="utf-8")

    assert "function cheapestHourlyFee" in script
    assert 'lot.fee_confidence === "exact"' in script
    assert "lot.hourly_fee_value != null" in script
    assert "lot.hourly_fee_value" in script
    assert "每小時最便宜" in script
    assert ".sort(" not in script.split("function cheapestHourlyFee", 1)[1].split(
        "function", 1)[0]
    assert "otherRecommended.some(lot => lot.is_cheapest_hourly)" in script
    assert "（含每小時最便宜）" in script
    assert 'lot.decision_status === "recommended"' in script.split(
        "const decoratePrice", 1)[1].split("};", 1)[0]


def test_all_top_choices_keep_the_same_decision_information_and_actions():
    """前三張共用的卡片必須都有價格、上限、型態、導航及趨勢操作。"""
    script = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
    primary = script.split("function primaryCard", 1)[1].split(
        "function feeMetaLine", 1)[0]
    fee_meta = script.split("function feeMetaLine", 1)[1].split(
        "function compactLot", 1)[0]

    assert "feeMetaLine(lot)" in primary
    assert "lot.hourly_fee_label" in fee_meta
    assert "lot.daily_cap_label" in fee_meta
    assert "lot.facility_type_label" in fee_meta
    assert "開始導航" in primary
    assert "查看空位趨勢" in primary


def test_map_appears_before_collapsed_inventory():
    """前三名地圖要在大量場站清單之前，手機使用者不必先滑過完整庫存。"""
    template = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")

    assert template.index('class="map-layout"') < template.index(
        'id="other-section"')


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
    assert "decision-ui-v2" in template
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


def test_decline_persists_choice_and_removes_uuid_without_sending_events():
    """拒絕選擇要固定寫入 declined、刪除本機 UUID，且不送出任何分析請求。"""
    script = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
    decline = script.split(
        'declineButton.addEventListener("click"', 1)[1]
    assert 'localStorage.setItem(ANALYTICS_CONSENT_KEY, "declined")' in decline
    assert "localStorage.removeItem(ANALYTICS_ID_KEY)" in decline
    assert "localStorage.removeItem(ANALYTICS_CONSENT_KEY)" not in decline
    assert "sendAnalyticsEvent" not in decline


def test_consent_banner_shows_only_when_no_choice_exists():
    """橫幅只在完全沒有選擇紀錄時顯示；已同意或已拒絕都不再打擾。"""
    script = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
    banner = script.split(
        "if (consentSection && acceptButton && declineButton)", 1)[1]

    assert "localStorage.getItem(ANALYTICS_CONSENT_KEY) === null" in banner
    assert "analyticsConsented()" not in banner.split(
        "if (changeButton && consentSection)", 1)[0]


def test_team_mode_auto_enables_existing_anonymous_identity():
    """免選擇模式仍建立原有 UUID，才能保留重複使用率與導航關聯。"""
    script = (ROOT / "static" / "app.js").read_text(encoding="utf-8")

    assert 'document.body.dataset.analyticsRequireConsent === "1"' in script
    assert "if (!ANALYTICS_REQUIRE_CONSENT)" in script
    assert 'localStorage.setItem(ANALYTICS_CONSENT_KEY, "accepted")' in script
    assert "localStorage.setItem(ANALYTICS_ID_KEY, crypto.randomUUID())" in script


def test_compact_navigation_links_use_rank_zero():
    """其他場站的精簡導航不得偽裝成首選名次 1-3。"""
    script = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
    primary = script.split("function primaryCard", 1)[1].split(
        "function compactLot", 1)[0]
    compact = script.split("function compactLot", 1)[1].split(
        "function renderCards", 1)[0]

    assert 'data-navigation-rank="${index + 1}"' in primary
    assert 'data-navigation-rank="0"' in compact
    assert "${index + 1}" not in compact


def test_consent_banner_copy_is_exact():
    """同意橫幅文案必須如實說明 14 天文字保留與 90 天刪除。"""
    template = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
    copy = "是否允許匿名使用分析？記錄查詢文字與完整目的地 14 天、其餘分析資料 90 天；只保存行政區、約 1 公里的粗略區域、成功／速度／導航點擊與不可逆裝置雜湊，不保存 IP 或手機位置。"
    assert copy in template
    assert "允許匿名分析" in template
    assert "不要分析" in template
    assert "查看隱私說明" in template
    assert "行政區" in template
    assert "約 1 公里的粗略區域" in template
    assert "14 天" in template
    assert "不保存完整地址、對話" not in template


def test_admin_dashboard_omits_place_type_diagnostic():
    """地點類型欄位永遠沒有資料來源，儀表板不得再顯示空診斷表。"""
    template = (ROOT / "templates" / "admin_analytics.html").read_text(
        encoding="utf-8")
    script = (ROOT / "static" / "admin_analytics.js").read_text(
        encoding="utf-8")

    assert "熱門地點類型" not in template
    assert "place-type-body" not in template
    assert "place-type-body" not in script
    assert "place_types" not in script


def test_dashboard_has_four_plain_language_sections_and_no_charts():
    html = DASHBOARD_TEMPLATE.read_text(encoding="utf-8")
    script = DASHBOARD_SCRIPT.read_text(encoding="utf-8")
    for heading in ("目前使用狀況", "使用者去哪裡", "系統哪裡需要改善", "最近查詢"):
        assert heading in html
    assert "canvas" not in html
    assert "Chart(" not in script
    assert "anonymous_id_hash" not in html + script
    assert "parsed_query_json" not in html + script
    assert "innerHTML" not in script
    assert "onclick=" not in html


def test_empty_tables_use_specific_helpful_messages():
    script = DASHBOARD_SCRIPT.read_text(encoding="utf-8")
    assert "尚無行政區資料，請完成一次新查詢" in script
    assert "尚無導航點擊" in script
    assert "尚無回饋" in script


def test_footer_offers_privacy_note_and_change_choice():
    """頁尾要有隱私說明錨點與可重新開啟選擇的控制項。"""
    template = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
    assert 'id="privacy-note"' in template
    assert 'id="analytics-choice"' in template
    assert "不保存 IP 或手機位置" in template
    assert "行政區" in template
    assert "14 天後清空" in template


def test_navigation_uses_beacon_with_keepalive_fallback():
    """導航點擊先送 sendBeacon；失敗退回 keepalive fetch，且不得阻擋點擊。"""
    script = (ROOT / "static" / "app.js").read_text(encoding="utf-8")

    assert "navigator.sendBeacon" in script
    assert "keepalive:true" in script.replace(" ", "")
    assert "data-navigation-rank" in script
    click_handler = script.split(
        'document.addEventListener("click"', 1)[1].split(
            "async function submitQuery", 1)[0]
    assert "preventDefault" not in click_handler
    assert ".catch(() => {})" in script


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


def test_feedback_block_renders_three_buttons_and_status():
    """結果區要有固定三種回饋按鈕與狀態列，預設隱藏。"""
    template = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")

    assert 'id="parking-feedback"' in template
    assert "這次推薦有幫助嗎？" in template
    assert 'data-feedback="found_space"' in template
    assert 'data-feedback="full_on_arrival"' in template
    assert 'data-feedback="did_not_go"' in template
    assert 'id="feedback-status"' in template
    assert "有，找到車位" in template
    assert "到場已滿" in template
    assert "沒有前往" in template


def test_feedback_uses_delegated_handler_and_disables_after_204():
    """回饋按鈕以單一委派處理，204 後停用並顯示狀態。"""
    script = (ROOT / "static" / "app.js").read_text(encoding="utf-8")

    assert 'closest("[data-feedback]")' in script
    assert '"/api/analytics/feedback"' in script
    assert "disabled = true" in script
    assert "feedback_code" in script


def test_feedback_buttons_lock_before_fetch_and_restore_only_on_failure():
    """按鈕必須在 fetch 前原子停用；只有失敗分支（非 204／網路錯誤）才恢復。"""
    script = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
    feedback = script.split("async function sendFeedback(code)", 1)[1].split(
        "async function submitQuery(payload)", 1)[0]

    assert feedback.index("disabled = true") < feedback.index(
        'fetch("/api/analytics/feedback"')
    assert feedback.count("disabled = false") == 2
    success_at = feedback.index("已記錄你的回饋，感謝！")
    catch_at = feedback.index("} catch {")
    assert success_at < catch_at
    # 成功訊息與 catch 之間不能有恢復；catch 內的恢復只屬網路失敗分支。
    assert "disabled = false" not in feedback[success_at:catch_at]


def test_new_interaction_events_use_delegated_handlers():
    """地圖、歷史與導航各以委派處理，新事件型態都出現在前端。"""
    template = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
    script = (ROOT / "static" / "app.js").read_text(encoding="utf-8")

    assert 'closest("[data-map-lot]")' in script
    assert 'closest("[data-history-lot]")' in script
    assert "onclick=" not in template
    assert "location_choice_shown" in script
    assert "location_choice_selected" in script
    assert "map_marker_clicked" in script
    assert "history_opened" in script


def test_pwa_asset_versions_bumped_for_decision_ui():
    """模板與服務器快取金鑰必須同步升版，避免手機沿用舊決策卡畫面。"""
    template = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
    sw = (ROOT / "static" / "sw.js").read_text(encoding="utf-8")
    script = (ROOT / "static" / "app.js").read_text(encoding="utf-8")

    assert "decision-ui-v2" in template
    assert "analytics-v3" not in template
    assert "parking-radar-shell-decision-ui-v2" in sw
    assert "style.css?v=decision-ui-v2" in sw
    assert "app.js?v=decision-ui-v2" in sw
    assert 'register("/static/sw.js?v=decision-ui-v2"' in script
    assert "decision-ui-v1" not in template
    assert "decision-ui-v1" not in sw


def test_safari_voice_input_is_optional_accessible_and_never_auto_submits():
    """語音輸入是選擇性按鈕：預設隱藏、無障礙標籤齊全，結果只填目的地且絕不自動送出。"""
    template = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
    script = (ROOT / "static" / "app.js").read_text(encoding="utf-8")

    voice_button = template.split('<button id="voice-input"', 1)[1].split(
        "</button>", 1)[0]
    assert "hidden" in voice_button
    assert 'type="button"' in voice_button
    assert 'aria-label="使用語音輸入目的地"' in voice_button
    assert 'aria-pressed="false"' in voice_button
    assert 'class="voice-label"' in voice_button
    assert "語音" in voice_button
    assert "分析" in template.split('id="voice-input"', 1)[1].split(
        "</div>", 1)[0]

    voice = script.split("const SpeechRecognitionApi", 1)[1].split(
        'document.addEventListener("DOMContentLoaded"', 1)[0]
    assert "window.SpeechRecognition || window.webkitSpeechRecognition" in voice
    assert 'recognition.lang = "zh-TW"' in voice
    assert "recognition.continuous = false" in voice
    assert "recognition.interimResults = true" in voice
    assert "recognition.maxAlternatives = 1" in voice
    assert "recognition.stop()" in voice
    assert "input.value = transcript" in voice
    assert "voiceButton.hidden = false" in voice
    for forbidden in ("submitQuery", ".submit(", ".requestSubmit(", '"/api/query"'):
        assert forbidden not in voice

    assert "setupVoiceInput();" in script.split(
        'document.addEventListener("DOMContentLoaded"', 1)[1]
    assert script.count("setupVoiceInput();") == 1


def test_voice_button_has_listening_wave_animation_with_reduced_motion():
    """聆聽動畫含四根裝飾音量柱，只在 listening 狀態播放並尊重減少動態設定。"""
    template = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
    styles = (ROOT / "static" / "style.css").read_text(encoding="utf-8")
    voice_button = template.split('<button id="voice-input"', 1)[1].split(
        "</button>", 1)[0]

    assert 'class="voice-input"' in voice_button
    assert 'class="voice-wave"' in voice_button
    assert 'aria-hidden="true"' in voice_button
    assert voice_button.count('class="voice-wave-bar"') == 4
    assert ".voice-wave {" in styles
    assert ".voice-input.listening .voice-wave" in styles
    assert ".voice-input.listening .voice-wave-bar" in styles
    assert "@keyframes voice-wave" in styles
    assert "@media (prefers-reduced-motion: reduce)" in styles
    assert "min-width:" in styles.split(".voice-input {", 1)[1].split("}", 1)[0]

    voice = (ROOT / "static" / "app.js").read_text(encoding="utf-8").split(
        "function setupVoiceInput()", 1)[1].split(
        'document.addEventListener("DOMContentLoaded"', 1)[0]
    assert 'listening ? "停止語音輸入" : "使用語音輸入目的地"' in voice
    assert 'processing ? "正在辨識語音" : "使用語音輸入目的地"' in voice


def test_voice_input_has_plain_language_listening_and_error_messages():
    """聆聽與錯誤訊息必須白話：狀態切換、權限、靜音、網路與未知錯誤各有精確文案。"""
    script = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
    voice = script.split("function setupVoiceInput()", 1)[1].split(
        'document.addEventListener("DOMContentLoaded"', 1)[0]

    assert '"停止"' in voice
    assert '"語音"' in voice
    assert "classList.toggle" in voice
    assert 'setAttribute("aria-pressed", String(listening))' in voice
    assert 'textContent = listening ? "停止" : "語音"' in voice
    assert "setListening(false)" in voice
    assert "正在聆聽，請說出目的地" in voice
    assert "已填入語音結果，請確認後按分析" in voice
    assert "請允許 Safari 使用麥克風" in voice
    assert 'code === "not-allowed" || code === "service-not-allowed"' in voice
    assert "沒有聽到語音，請再試一次" in voice
    assert "語音服務暫時無法連線" in voice
    assert "語音輸入失敗，請改用鍵盤輸入" in voice


def test_voice_result_ignores_empty_transcript_and_focuses_message():
    """空轉錄結果不得顯示成功或覆寫輸入；非空結果要聚焦 #message 供確認。"""
    script = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
    voice = script.split("function setupVoiceInput()", 1)[1].split(
        'document.addEventListener("DOMContentLoaded"', 1)[0]
    onresult = voice.split("recognition.onresult", 1)[1].split(
        "recognition.onerror", 1)[0]

    assert "if (transcript) {" in onresult
    assert onresult.index("if (transcript) {") < onresult.index(
        "input.value = transcript")
    assert onresult.index("input.value = transcript") < onresult.index(
        "input.focus()")
    assert 'showStatus("已填入語音結果，請確認後按分析"' in onresult
    assert "已填入語音結果，請確認後按分析" not in onresult.split(
        "if (transcript) {", 1)[0]


def test_voice_result_rebuilds_all_safari_interim_results_safely():
    """Safari 中間結果要從目前完整陣列重建，避免停頓後遺失前句或重複累加。"""
    script = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
    voice = script.split("function setupVoiceInput()", 1)[1].split(
        'document.addEventListener("DOMContentLoaded"', 1)[0]
    onresult = voice.split("recognition.onresult", 1)[1].split(
        "recognition.onerror", 1)[0]

    assert "recognition.interimResults = true" in voice
    assert "const results = event.results" in onresult
    assert "if (!results?.length) return" in onresult
    assert "let index = 0" in onresult
    assert "index < results.length" in onresult
    assert "results[index]?.[0]?.transcript" in onresult
    assert "input.value = transcript" in onresult


def test_voice_manual_stop_immediately_leaves_listening_state():
    """使用者按停止時要立即還原按鈕，不能等待 Safari 延遲觸發 onend。"""
    script = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
    voice = script.split("function setupVoiceInput()", 1)[1].split(
        'document.addEventListener("DOMContentLoaded"', 1)[0]
    click_handler = voice.split(
        'voiceButton.addEventListener("click", () => {', 1)[1].split(
        "recognition.onstart", 1)[0]
    stop_branch = voice.split("function requestVoiceStop()", 1)[1].split(
        'voiceButton.addEventListener("click"', 1)[0]

    assert "requestVoiceStop()" in click_handler
    assert "setListening(false)" in stop_branch
    assert stop_branch.index("setListening(false)") < stop_branch.index(
        "recognition.stop()")
    assert "正在辨識語音，請稍候" in stop_branch


def test_voice_stop_waits_for_terminal_event_before_allowing_restart():
    """停止後必須鎖住重啟，直到 Safari 的 onend 完成，避免舊事件污染下一輪。"""
    script = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
    voice = script.split("function setupVoiceInput()", 1)[1].split(
        'document.addEventListener("DOMContentLoaded"', 1)[0]
    click_handler = voice.split(
        'voiceButton.addEventListener("click", () => {', 1)[1].split(
        "recognition.onstart", 1)[0]
    stop_helper = voice.split("function requestVoiceStop()", 1)[1].split(
        'voiceButton.addEventListener("click"', 1)[0]
    onend = voice.split("recognition.onend", 1)[1]

    assert "let isStopping = false" in voice
    assert "isStopping = true" in stop_helper
    assert "if (isStarting || isStopping) return" in click_handler
    assert "isStopping = false" in onend
    assert "voiceButton.disabled = processing" in voice


def test_voice_speech_end_reuses_manual_stop_to_release_microphone():
    """自然說完必須和手動停止走同一個 stop() 流程，不可只等待 Safari 自動結束。"""
    script = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
    voice = script.split("function setupVoiceInput()", 1)[1].split(
        'document.addEventListener("DOMContentLoaded"', 1)[0]
    assert "function requestVoiceStop()" in voice
    stop_helper = voice.split("function requestVoiceStop()", 1)[1].split(
        'voiceButton.addEventListener("click"', 1)[0]
    click_handler = voice.split(
        'voiceButton.addEventListener("click", () => {', 1)[1].split(
        "recognition.onstart", 1)[0]
    speech_end = voice.split("recognition.onspeechend", 1)[1].split(
        "recognition.onresult", 1)[0]

    assert "recognition.stop()" in stop_helper
    assert "requestVoiceStop()" in click_handler
    assert "requestVoiceStop()" in speech_end


def test_voice_stop_keeps_success_status_when_interim_result_already_exists():
    """onresult 早於 speechend 時，停止流程不得把成功提示覆寫回「正在辨識」。"""
    script = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
    voice = script.split("function setupVoiceInput()", 1)[1].split(
        'document.addEventListener("DOMContentLoaded"', 1)[0]
    stop_helper = voice.split("function requestVoiceStop()", 1)[1].split(
        'voiceButton.addEventListener("click"', 1)[0]
    assert "} else if (!receivedError) {" in stop_helper
    success_branch = stop_helper.split("if (receivedResult) {", 1)[1].split(
        "} else if (!receivedError) {", 1)[0]
    waiting_branch = stop_helper.split("} else if (!receivedError) {", 1)[1].split(
        "}", 1)[0]

    assert "if (receivedResult)" in stop_helper
    assert "已填入語音結果，請確認後按分析" in success_branch
    assert "正在辨識語音，請稍候" not in success_branch
    assert "正在辨識語音，請稍候" in waiting_branch
    assert "已填入語音結果，請確認後按分析" not in waiting_branch


def test_voice_end_always_finishes_processing_with_result_or_clear_error():
    """stop 仍保留最終文字；若 Safari 沒有結果，onend 必須結束等待並顯示錯誤。"""
    script = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
    voice = script.split("function setupVoiceInput()", 1)[1].split(
        'document.addEventListener("DOMContentLoaded"', 1)[0]
    onresult = voice.split("recognition.onresult", 1)[1].split(
        "recognition.onerror", 1)[0]
    onerror = voice.split("recognition.onerror", 1)[1].split(
        "recognition.onend", 1)[0]
    onend = voice.split("recognition.onend", 1)[1]

    assert "receivedResult = true" in onresult
    assert "input.value = transcript" in onresult
    assert "receivedError = true" in onerror
    assert "setProcessing(false)" in onend
    assert "if (!receivedResult && !receivedError)" in onend
    assert "沒有取得語音文字，請再試一次" in onend
    assert "input.value" not in onend


def test_admin_analytics_js_renders_empty_and_disabled_states():
    """管理儀表板必須呈現零資料、未設定與載入失敗三種誠實狀態。"""
    script = (ROOT / "static" / "admin_analytics.js").read_text(encoding="utf-8")

    assert "尚無任何資料，請先完成一次新查詢" in script
    assert "匿名分析未設定：缺少 HMAC 秘密，統計保持空白。" in script
    assert "指標載入失敗" in script


def test_dashboard_tables_are_wrapped_in_table_scroll():
    """行政區／目的地／停車場表格必須直接包在 .table-scroll 內，行動版可橫向捲動。"""
    html = DASHBOARD_TEMPLATE.read_text(encoding="utf-8")
    for body_id in ("district-body", "destination-body", "lot-body"):
        prefix = html.split(f'<tbody id="{body_id}">', 1)[0]
        before_table = prefix.rsplit("<table", 1)[0]
        assert before_table.rstrip().endswith('<div class="table-scroll">')
