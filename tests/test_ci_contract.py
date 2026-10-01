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


def test_deploy_runs_only_after_tests_on_master():
    """自動部署必須等測試通過、只在 master push 觸發，且嚴格驗證主機指紋。"""
    text = WORKFLOW.read_text(encoding="utf-8")

    assert "needs: test" in text
    assert "github.ref == 'refs/heads/master'" in text
    assert "cancel-in-progress: false" in text
    assert "StrictHostKeyChecking=yes" in text
    assert "DEPLOY_OK sha=${SHORT_SHA}" in text


def test_deploy_key_is_forced_to_receive_script_only():
    """deploy 金鑰只能執行接收腳本，sudo 只能執行事先安裝的 deploy.sh。"""
    installer = (ROOT / "deploy" / "install-deploy-user.sh").read_text(encoding="utf-8")
    receive = (ROOT / "deploy" / "parking-radar-receive").read_text(encoding="utf-8")

    assert 'restrict,command="/usr/local/sbin/parking-radar-receive"' in installer
    # sudoers 行寫在 printf 格式字串內，結尾是字面的 \n。
    assert r"NOPASSWD: /usr/local/sbin/parking-radar-deploy\n" in installer
    assert "visudo -cf" in installer
    assert "^[0-9a-f]{7,40}$" in receive
    assert "exec sudo /usr/local/sbin/parking-radar-deploy" in receive
