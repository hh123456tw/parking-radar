#!/usr/bin/env bash
# 在伺服器上以 root 執行：sudo bash install-nginx.sh <新版 app 目錄，預設 /opt/parking-hell>
# 先備份現行設定，通過 nginx -t 才 reload；檢查失敗就還原舊設定。
set -Eeuo pipefail

SRC="${1:-/opt/parking-hell}/deploy"
SITE="/etc/nginx/sites-available/parking-radar"
HTTP_CONF="/etc/nginx/conf.d/nginx-parking-radar-log-format.conf"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"

cp -a "$SITE" "${SITE}.bak-${STAMP}"
cp -a "$HTTP_CONF" "/root/nginx-parking-radar-log-format.conf.bak-${STAMP}"

restore() {
  cp -a "${SITE}.bak-${STAMP}" "$SITE"
  cp -a "/root/nginx-parking-radar-log-format.conf.bak-${STAMP}" "$HTTP_CONF"
  nginx -t && systemctl reload nginx
  echo "NGINX_RESTORED backup=${STAMP}"
}
trap 'restore; exit 1' ERR

install -m 644 "$SRC/nginx-parking-radar.conf" "$SITE"
install -m 644 "$SRC/nginx-parking-radar-log-format.conf" "$HTTP_CONF"
nginx -t
systemctl reload nginx
echo "NGINX_OK backup=${STAMP}"
