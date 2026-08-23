"""CI contract：每次 push/PR 必須執行後端與前端核心檢查。"""

from pathlib import Path


WORKFLOW = Path(__file__).parents[1] / ".github" / "workflows" / "ci.yml"


def test_ci_runs_required_offline_checks():
    text = WORKFLOW.read_text(encoding="utf-8")
    for required in (
        "push:", "pull_request:", "python-version: \"3.13\"",
        "node-version: \"22\"", "python -m pytest -q",
        "python -m compileall -q", "node --check static/app.js",
        "node --check static/sw.js",
    ):
        assert required in text


def test_ci_compiles_analytics_modules_and_checks_admin_js():
    """分析功能的所有 Python 模組必須納入 compileall，管理儀表板 JS 必須 node-check。"""
    text = WORKFLOW.read_text(encoding="utf-8")
    compileall_line = next(
        line for line in text.splitlines()
        if "python -m compileall -q" in line
    )
    for module in (
        "analytics_service.py", "analytics_database.py",
        "status_service.py", "analytics_cleanup.py",
    ):
        assert module in compileall_line
    js_step = text.split("- name: Check JavaScript syntax", 1)[1]
    js_step = js_step.split("- name:", 1)[0]
    assert "node --check static/admin_analytics.js" in js_step
