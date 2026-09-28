#!/usr/bin/env bash
# 在伺服器上以 root 執行：sudo bash deploy.sh <sha>
# 前置：先把 git archive 產生的 /tmp/parking-radar-<sha>.tar.gz 上傳到伺服器。
# 流程：備份資料庫 → 建立新版目錄 → 切換 → 健康檢查與冒煙測試；任何一步失敗自動換回舊版。
# 只處理程式變更；需要資料庫 migration 時請另外執行並先備份。
set -Eeuo pipefail

APP="/opt/parking-hell"
SHA="${1:?usage: deploy.sh <sha>}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
ARCHIVE="/tmp/parking-radar-${SHA}.tar.gz"
RELEASE="/opt/parking-hell.release-${STAMP}"
ROLLBACK="/opt/parking-hell.rollback-${STAMP}"
FAILED="/opt/parking-hell.failed-${STAMP}"
BACKUP_DIR="/opt/parking-backups"
DB_BACKUP="${BACKUP_DIR}/parking-${STAMP}-before-${SHA}.sql.gz"
SWITCHED=0

restore_on_error() {
  local rc=$?
  trap - ERR
  echo "DEPLOY_FAILED rc=${rc}"
  systemctl stop parking-radar || true
  if [[ "$SWITCHED" == "1" && -d "$ROLLBACK" ]]; then
    [[ -d "$APP" ]] && mv "$APP" "$FAILED"
    mv "$ROLLBACK" "$APP"
  else
    rm -rf "$RELEASE"
  fi
  systemctl start parking-radar || true
  echo "APP_ROLLBACK_COMPLETE db_backup=${DB_BACKUP}"
  exit "$rc"
}
trap restore_on_error ERR

test -f "$ARCHIVE"
test -d "$APP"
test -f "$APP/.env"
test -x "$APP/.venv/bin/python"

set -a
. "$APP/.env"
set +a
export MYSQL_PWD="$MYSQL_PASSWORD"
test "$MYSQL_DATABASE" = "parking_hell"

install -d -m 750 -o root -g parking "$BACKUP_DIR"
mysqldump --host="$MYSQL_HOST" --port="$MYSQL_PORT" --user="$MYSQL_USER" \
  --single-transaction --quick --routines --triggers \
  --databases "$MYSQL_DATABASE" | gzip -c > "$DB_BACKUP"
gzip -t "$DB_BACKUP"
chmod 640 "$DB_BACKUP"
chown root:parking "$DB_BACKUP"
echo "DB_BACKUP_OK path=${DB_BACKUP}"

install -d -m 755 -o parking -g www-data "$RELEASE"
tar -xzf "$ARCHIVE" -C "$RELEASE"
install -m 600 -o parking -g www-data "$APP/.env" "$RELEASE/.env"
cp -a "$APP/.venv" "$RELEASE/.venv"
if [[ -d "$APP/data" ]]; then
  install -d -m 775 -o parking -g www-data "$RELEASE/data"
  cp -a "$APP/data/." "$RELEASE/data/"
fi
# 排程日誌寫在 app 目錄內；換版時一併帶過去，避免日誌隨舊版目錄消失。
for log in "$APP"/*.log; do
  [[ -f "$log" ]] && cp -a "$log" "$RELEASE/"
done
chown -R parking:www-data "$RELEASE"
sudo -u parking "$RELEASE/.venv/bin/pip" install --disable-pip-version-check -q \
  -r "$RELEASE/requirements.txt"
sudo -u parking "$RELEASE/.venv/bin/python" -m py_compile \
  "$RELEASE/app.py" "$RELEASE/collector.py" "$RELEASE/fee_service.py" \
  "$RELEASE/snapshot_archive.py" "$RELEASE/config.py"
test -f "$RELEASE/templates/sw.js"
test -f "$RELEASE/static/vendor/leaflet/leaflet.js"

if grep -q '^DEPLOY_VERSION=' "$RELEASE/.env"; then
  sed -i "s/^DEPLOY_VERSION=.*/DEPLOY_VERSION=${SHA}/" "$RELEASE/.env"
else
  printf 'DEPLOY_VERSION=%s\n' "$SHA" >> "$RELEASE/.env"
fi
chmod 600 "$RELEASE/.env"
chown parking:www-data "$RELEASE/.env"

systemctl stop parking-radar
mv "$APP" "$ROLLBACK"
mv "$RELEASE" "$APP"
SWITCHED=1
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

# 冒煙測試：實際跑一次行政區查詢，確認資料庫與推薦流程正常。
SMOKE="$(curl -fsS -X POST http://127.0.0.1:8000/api/query \
  -H 'Content-Type: application/json' \
  -d "{\"mode\":\"manual\",\"district\":\"信義區\",\"arrival_time\":\"$(TZ=Asia/Taipei date -Iseconds)\"}")"
grep -q '"district_score"' <<<"$SMOKE"
echo "SMOKE_OK ${SMOKE:0:200}"
# 確認新版 collector 能解析官方資料（只解析，不寫入）。
sudo -u parking bash -c "cd $APP && .venv/bin/python -c 'import collector, datetime; p=collector.fetch_json(collector.DYNAMIC_URL); print(\"PARSE_OK\", len(collector.parse_dynamic(p, datetime.datetime.now(datetime.timezone.utc))))'"

echo "DEPLOY_OK sha=${SHA} rollback=${ROLLBACK} db_backup=${DB_BACKUP}"
