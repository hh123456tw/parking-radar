#!/usr/bin/env bash
# 在伺服器上以 root 執行：sudo bash install-nginx.sh <已驗證的 git checkout 目錄>
# 先備份現行設定，通過 nginx -t 才 reload；檢查失敗就還原舊設定。
# 來源必須是 root 自己 checkout 的目錄；/opt/parking-hell* 可被 parking 改寫，不能當成設定來源。
set -Eeuo pipefail

SRC_ROOT="$(realpath -e "${1:?usage: install-nginx.sh <verified git checkout>}")"
case "$SRC_ROOT" in
  /opt/parking-hell*) echo "REJECTED: source must not be under /opt/parking-hell" >&2; exit 2 ;;
esac
SRC="${SRC_ROOT}/deploy"
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
