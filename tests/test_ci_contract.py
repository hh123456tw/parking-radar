"""CI contract：每次 push/PR 必須執行 lint、含覆蓋率門檻的測試與前端語法檢查。"""

from pathlib import Path

ROOT = Path(__file__).parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"


def test_ci_runs_required_offline_checks():
    text = WORKFLOW.read_text(encoding="utf-8")
    for required in (
        "push:", "pull_request:", "python-version: \"3.13\"",
        "node-version: \"22\"", "pip install -r requirements-dev.txt",
        "ruff check .", "pip-audit -r requirements.txt", "--cov-fail-under=90",
        "node --check static/app.js", "node --check static/admin_analytics.js",
        "node --check templates/sw.js",
    ):
        assert required in text


def test_test_tools_stay_out_of_production_requirements():
    """正式環境只安裝執行所需套件；測試與 lint 工具放在 requirements-dev.txt。"""
    production = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    development = (ROOT / "requirements-dev.txt").read_text(encoding="utf-8")

    assert "pytest" not in production
    assert "ruff" not in production
    assert "-r requirements.txt" in development
    assert "pytest-cov" in development
