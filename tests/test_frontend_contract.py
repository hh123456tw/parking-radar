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
