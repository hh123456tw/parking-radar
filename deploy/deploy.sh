#!/usr/bin/env bash
# 在伺服器上以 root 執行：sudo bash deploy.sh <sha>
# 前置：先把 git archive 產生的 /tmp/parking-radar-<sha>.tar.gz 上傳到伺服器。
# 流程：備份資料庫 → 建立新版目錄 → 切換 → 健康檢查與資料庫連線檢查；任何一步失敗自動換回舊版。
# 只處理程式變更；需要資料庫 migration 時請另外執行並先備份。
set -Eeuo pipefail

APP="/opt/parking-hell"
SHA="${1:?usage: deploy.sh <sha>}"
[[ "$SHA" =~ ^[0-9a-f]{7,40}$ ]] || { echo "bad sha" >&2; exit 2; }
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
ARCHIVE="/tmp/parking-radar-${SHA}.tar.gz"
RELEASE="/opt/parking-hell.release-${STAMP}"
ROLLBACK="/opt/parking-hell.rollback-${STAMP}"
FAILED="/opt/parking-hell.failed-${STAMP}"
BACKUP_DIR="/opt/parking-backups"
DB_BACKUP="${BACKUP_DIR}/parking-${STAMP}-before-${SHA}.sql.gz"
LOG="/var/log/parking-radar-deploy-${STAMP}.log"
SWITCHED=0
BACKUP_OK=0

# SSH 斷線時不能讓部署停在一半：忽略 HUP/PIPE，輸出同時寫入日誌。
# tee -p 在 stdout 斷線後仍繼續寫日誌檔，部署照常跑完或回滾。
trap '' HUP PIPE
cd /
install -m 600 -o root -g root /dev/null "$LOG"
exec > >(tee -p -a "$LOG") 2>&1
echo "DEPLOY_LOG path=${LOG}"

restore_on_error() {
  local rc=$?
  trap - ERR
  echo "DEPLOY_FAILED rc=${rc}"
  # 備份沒完成就刪掉，避免留下看似可用的半截備份。
  [[ "$BACKUP_OK" == "1" ]] || rm -f -- "$DB_BACKUP"
  if [[ "$SWITCHED" == "1" ]]; then
    systemctl stop parking-radar || true
    if [[ -d "$ROLLBACK" ]]; then
      [[ -d "$APP" ]] && mv "$APP" "$FAILED"
      mv "$ROLLBACK" "$APP"
    fi
    rm -rf -- "$RELEASE"
    systemctl start parking-radar || true
    echo "APP_ROLLBACK_COMPLETE db_backup=${DB_BACKUP}"
  else
    # 尚未切換：舊版仍在正常服務，不重啟。
    rm -rf -- "$RELEASE"
    echo "APP_UNCHANGED"
  fi
  exit "$rc"
}
trap restore_on_error ERR

# .env 屬於 parking 使用者；以 parking 身分讀取，且只取需要的鍵，不以 root 執行其內容。
env_value() {
  sudo -u parking sed -n "s/^$1=//p" "$APP/.env" | tail -n 1 | tr -d '\r' \
    | sed -e 's/^"\(.*\)"$/\1/' -e "s/^'\(.*\)'$/\1/"
}

# 定期清理：只留最新 3 份回滾目錄，刪除 14 天前的備份、失敗目錄與部署日誌。
prune_old_releases() {
  find /opt -maxdepth 1 -type d -name 'parking-hell.rollback-*' -printf '%f\n' \
    | sort -r | tail -n +4 | while read -r old; do rm -rf -- "/opt/${old}"; done
  find "$BACKUP_DIR" -maxdepth 1 -type f -name '*.sql.gz' -mtime +14 -delete
  find /opt -maxdepth 1 -type d -name 'parking-hell.failed-*' -mtime +14 \
    -exec rm -rf -- {} +
  find /var/log -maxdepth 1 -type f -name 'parking-radar-deploy-*.log' -mtime +14 -delete
  rm -f -- "$ARCHIVE"
}

test -f "$ARCHIVE"
test -d "$APP"
test -f "$APP/.env"
test -x "$APP/.venv/bin/python"

MYSQL_HOST="$(env_value MYSQL_HOST)"
MYSQL_PORT="$(env_value MYSQL_PORT)"
MYSQL_USER="$(env_value MYSQL_USER)"
MYSQL_DATABASE="$(env_value MYSQL_DATABASE)"
MYSQL_PWD="$(env_value MYSQL_PASSWORD)"
export MYSQL_PWD
test "$MYSQL_DATABASE" = "parking_hell"

install -d -m 750 -o root -g parking "$BACKUP_DIR"
mysqldump --host="$MYSQL_HOST" --port="${MYSQL_PORT:-3306}" --user="$MYSQL_USER" \
  --single-transaction --quick --routines --triggers \
  --databases "$MYSQL_DATABASE" | gzip -c > "$DB_BACKUP"
gzip -t "$DB_BACKUP"
chmod 640 "$DB_BACKUP"
chown root:parking "$DB_BACKUP"
BACKUP_OK=1
echo "DB_BACKUP_OK path=${DB_BACKUP}"

# 打包檔只能有一般檔案與目錄：拒絕符號連結、硬連結、裝置檔，以及絕對路徑或 .. 路徑。
# 先存成變數再比對，避免管線提早結束時 pipefail 吃掉比對結果。
LISTING="$(tar -tzvf "$ARCHIVE")"
NAMES="$(tar -tzf "$ARCHIVE")"
if grep -qv '^[-d]' <<<"$LISTING"; then
  echo "REJECTED: archive contains links or special files" >&2
  false
fi
if grep -Eq '^/|(^|/)\.\.(/|$)' <<<"$NAMES"; then
  echo "REJECTED: archive contains absolute or .. paths" >&2
  false
fi

# 新版目錄的內容一律以 parking 身分寫入，root 不在 parking 可控的目錄裡解壓或複製。
install -d -m 755 -o parking -g www-data "$RELEASE"
sudo -u parking tar -xzf "$ARCHIVE" -C "$RELEASE" --no-same-owner --no-same-permissions
sudo -u parking cp -a "$APP/.venv" "$RELEASE/.venv"
if [[ -d "$APP/data" ]]; then
  sudo -u parking install -d -m 775 "$RELEASE/data"
  sudo -u parking cp -a "$APP/data/." "$RELEASE/data/"
fi
# 排程日誌寫在 app 目錄內；換版時一併帶過去，避免日誌隨舊版目錄消失。
for log in "$APP"/*.log; do
  [[ -f "$log" ]] && sudo -u parking cp -a "$log" "$RELEASE/"
done
# 新 .env = 舊 .env（去掉 DEPLOY_VERSION）+ 本次版本；以 parking 身分讀寫，權限維持 600。
sudo -u parking sh -c 'umask 077; rm -f -- "$3"; { grep -v "^DEPLOY_VERSION=" "$1" || true; printf "DEPLOY_VERSION=%s\n" "$2"; } > "$3"' \
  sh "$APP/.env" "$SHA" "$RELEASE/.env"
chown -R parking:www-data "$RELEASE"
sudo -u parking "$RELEASE/.venv/bin/pip" install --disable-pip-version-check -q \
  -r "$RELEASE/requirements.txt"
sudo -u parking "$RELEASE/.venv/bin/python" -m py_compile \
  "$RELEASE/app.py" "$RELEASE/query_service.py" "$RELEASE/analytics_recorder.py" \
  "$RELEASE"/routes/*.py "$RELEASE/collector.py" "$RELEASE/fee_service.py" \
  "$RELEASE/snapshot_archive.py" "$RELEASE/config.py"
test -f "$RELEASE/templates/sw.js"
test -f "$RELEASE/static/vendor/leaflet/leaflet.js"

# 從停止服務開始就算切換：之後任何失敗都要還原舊版並重新啟動。
SWITCHED=1
systemctl stop parking-radar
mv "$APP" "$ROLLBACK"
mv "$RELEASE" "$APP"
systemctl start parking-radar

healthy=0
for _ in $(seq 1 30); do
  if curl -fsS http://127.0.0.1:8000/health >/dev/null 2>&1; then
    healthy=1
    break
  fi
  sleep 1
done
test "$healthy" = "1"
echo "HEALTH_OK"

# 冒煙測試只驗證本機可控的部分：新版能連上資料庫。不依賴官方資料是否更新。
sudo -u parking bash -c "cd $APP && .venv/bin/python -c 'import database; c=database.get_connection(); c.cursor().execute(\"SELECT 1\"); c.close(); print(\"DB_OK\")'"

# 官方資料解析只提示：官方來源停擺或格式異常時不應讓部署回滾。
sudo -u parking bash -c "cd $APP && .venv/bin/python -c 'import collector, datetime; p=collector.fetch_json(collector.DYNAMIC_URL); print(\"PARSE_OK\", len(collector.parse_dynamic(p, datetime.datetime.now(datetime.timezone.utc))))'"   || echo "WARN_OFFICIAL_FEED_PARSE_FAILED (not rolling back)"

prune_old_releases || echo "WARN_PRUNE_FAILED"

echo "DEPLOY_OK sha=${SHA} rollback=${ROLLBACK} db_backup=${DB_BACKUP}"
