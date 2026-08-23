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
