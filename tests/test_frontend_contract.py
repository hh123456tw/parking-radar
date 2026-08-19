"""前端圖卡契約測試：避免地址、容量、理由與兩種操作被意外移除。"""

from pathlib import Path

ROOT = Path(__file__).parents[1]


def test_decision_cards_keep_required_data_and_actions():
    script = (ROOT / "static" / "app.js").read_text(encoding="utf-8")

    assert "function formatFullAddress(lot)" in script
    assert "function googleMapsUrl(lot)" in script
    assert "https://www.google.com/maps/search/?api=1&query=" in script
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
    assert "fee-details-v1" in template
    assert 'document.querySelector("#result-content").hidden = true' in script
