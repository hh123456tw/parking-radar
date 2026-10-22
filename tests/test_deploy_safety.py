"""部署腳本安全契約：root 不執行 parking 可控的內容，失敗與斷線時行為可預期。"""

import re
from pathlib import Path

ROOT = Path(__file__).parents[1]
DEPLOY = (ROOT / "deploy" / "deploy.sh").read_text(encoding="utf-8")
RECEIVE = (ROOT / "deploy" / "parking-radar-receive").read_text(encoding="utf-8")
INSTALL_USER = (ROOT / "deploy" / "install-deploy-user.sh").read_text(encoding="utf-8")
INSTALL_NGINX = (ROOT / "deploy" / "install-nginx.sh").read_text(encoding="utf-8")


def _function_body(text, name):
    return text.split(f"{name}() {{", 1)[1].split("\n}\n", 1)[0]


def test_smoke_test_checks_health_and_database_not_official_feed():
    """官方資料凍結時部署不能因此回滾：只以 /health 與資料庫連線決定成敗。"""
    assert "curl -fsS http://127.0.0.1:8000/health" in DEPLOY
    assert "database.get_connection()" in DEPLOY
    assert "SELECT 1" in DEPLOY
    assert "/api/query" not in DEPLOY
    feed_line = next(line for line in DEPLOY.splitlines() if "collector.fetch_json" in line)
    assert '|| echo "WARN_OFFICIAL_FEED_PARSE_FAILED' in feed_line


def test_env_is_read_as_data_never_sourced_by_root():
    assert not re.search(r'^\s*(\.|source)\s+"?\$APP/\.env', DEPLOY, re.M)
    assert "eval" not in DEPLOY
    assert 'sudo -u parking sed -n "s/^$1=//p" "$APP/.env"' in DEPLOY
    for key in ("MYSQL_HOST", "MYSQL_PORT", "MYSQL_USER", "MYSQL_PASSWORD", "MYSQL_DATABASE"):
        assert f"env_value {key})" in DEPLOY


def test_archive_is_vetted_then_extracted_as_parking():
    assert "tar -tzvf" in DEPLOY
    assert "grep -qv '^[-d]'" in DEPLOY
    assert r"grep -Eq '^/|(^|/)\.\.(/|$)'" in DEPLOY
    assert DEPLOY.index("tar -tzvf") < DEPLOY.index("tar -xzf")
    extract = next(line for line in DEPLOY.splitlines() if "tar -xzf" in line)
    assert extract.startswith("sudo -u parking tar -xzf")
    assert "--no-same-owner" in extract and "--no-same-permissions" in extract
    # 唯一的解壓指令必須以 parking 身分執行。
    assert DEPLOY.count("tar -xzf") == 1


def test_path_filter_rejects_traversal_but_not_dotted_names():
    pattern = re.compile(r"^/|(^|/)\.\.(/|$)")
    for bad in ("/etc/passwd", "../x", "a/../b", "a/.."):
        assert pattern.search(bad)
    for good in ("a/b", "foo..bar", "a/..hidden", "static/app.js"):
        assert not pattern.search(good)


def test_sha_is_validated_right_after_reading():
    lines = DEPLOY.splitlines()
    sha_line = lines.index('SHA="${1:?usage: deploy.sh <sha>}"')
    assert lines[sha_line + 1] == (
        '[[ "$SHA" =~ ^[0-9a-f]{7,40}$ ]] || { echo "bad sha" >&2; exit 2; }')


def test_old_releases_backups_and_archive_are_pruned_before_ok():
    body = _function_body(DEPLOY, "prune_old_releases")
    assert "parking-hell.rollback-*" in body and "tail -n +4" in body
    assert "'*.sql.gz' -mtime +14 -delete" in body
    assert "parking-hell.failed-*' -mtime +14" in body
    assert 'rm -f -- "$ARCHIVE"' in body
    # 清理失敗只警告，不能讓已驗證的新版被回滾。
    assert DEPLOY.index('prune_old_releases || echo') < DEPLOY.index('echo "DEPLOY_OK')


def test_failure_before_switch_does_not_restart_old_version():
    body = _function_body(DEPLOY, "restore_on_error")
    switched, unchanged = body.split('if [[ "$SWITCHED" == "1" ]]; then', 1)[1].split("else", 1)
    assert "systemctl stop" in switched and "systemctl start" in switched
    assert "systemctl" not in unchanged
    assert "systemctl" not in body.split('if [[ "$SWITCHED" == "1" ]]', 1)[0]
    # 切換旗標在停止服務之前設定，停服務後的任何失敗都會還原。
    assert DEPLOY.index("SWITCHED=1\n") < DEPLOY.index("systemctl stop parking-radar\n")


def test_partial_db_backup_is_removed_on_failure():
    body = _function_body(DEPLOY, "restore_on_error")
    assert '[[ "$BACKUP_OK" == "1" ]] || rm -f -- "$DB_BACKUP"' in body
    assert DEPLOY.index('gzip -t "$DB_BACKUP"') < DEPLOY.index("BACKUP_OK=1\n")


def test_deploy_survives_ssh_disconnect_and_keeps_log():
    assert "trap '' HUP PIPE" in DEPLOY
    assert 'LOG="/var/log/parking-radar-deploy-${STAMP}.log"' in DEPLOY
    assert 'exec > >(tee -p -a "$LOG") 2>&1' in DEPLOY
    assert DEPLOY.index("trap '' HUP PIPE") < DEPLOY.index("exec > >(tee")


def test_receive_command_matches_sudoers_whitelist():
    """receive 執行的 sudo 指令必須和 sudoers 白名單完全一致。"""
    assert 'exec sudo /usr/local/sbin/parking-radar-deploy "$SHA"' in RECEIVE
    assert r"deploy ALL=(root) NOPASSWD: /usr/local/sbin/parking-radar-deploy\n" in INSTALL_USER
    assert "visudo -cf" in INSTALL_USER


def test_installers_refuse_parking_writable_source():
    for text in (INSTALL_USER, INSTALL_NGINX):
        assert "/opt/parking-hell*) echo \"REJECTED" in text
        assert 'realpath -e' in text
        assert ":-/opt/parking-hell" not in text


def test_installer_source_check_matches_release_dirs():
    """case 樣式需同時擋住 /opt/parking-hell 與 rollback/release 目錄，但不誤擋其他路徑。"""
    from fnmatch import fnmatchcase

    for bad in ("/opt/parking-hell", "/opt/parking-hell/deploy", "/opt/parking-hell.rollback-1"):
        assert fnmatchcase(bad, "/opt/parking-hell*")
    for good in ("/root/parking-radar-src", "/opt/parking", "/srv/parking-hell"):
        assert not fnmatchcase(good, "/opt/parking-hell*")
