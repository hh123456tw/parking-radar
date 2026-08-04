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
    assert 'data-lot="${escapeHtml(lot.lot_id)}"' in script


def test_query_has_timeout_and_history_does_not_block_cards():
    """Gemini 或歷史 API 變慢時，頁面不得永久停在分析中。"""
    script = (ROOT / "static" / "app.js").read_text(encoding="utf-8")

    assert "new AbortController()" in script
    assert "controller.abort(), 15000" in script
    assert "分析逾時，請重試或改用手動查詢" in script
    assert "await loadHistory" not in script
