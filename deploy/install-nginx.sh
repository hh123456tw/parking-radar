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
# 每個實際來源檔都先解析符號連結再檢查：不能位於 /opt/parking-hell*，
# 檔案本身與所有上層目錄也不能讓 parking 寫入，否則 root 會裝上 parking 可控的內容。
verified_source() {
  local file dir
  file="$(realpath -e "$1")" || return 2
  case "$file" in
    /opt/parking-hell*) echo "REJECTED: ${file} is under /opt/parking-hell" >&2; return 2 ;;
  esac
  local parent
  dir="$file"
  while :; do
    if sudo -u parking test -w "$dir"; then
      echo "REJECTED: ${dir} is writable by parking" >&2
      return 2
    fi
    # dirname 到根目錄後不再變動，以此結束，不假設根目錄一定寫成 /。
    parent="$(dirname "$dir")"
    [[ "$parent" == "$dir" ]] && break
    dir="$parent"
  done
  printf '%s\n' "$file"
}
SITE_SRC="$(verified_source "$SRC/nginx-parking-radar.conf")"
HTTP_CONF_SRC="$(verified_source "$SRC/nginx-parking-radar-log-format.conf")"
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

install -m 644 "$SITE_SRC" "$SITE"
install -m 644 "$HTTP_CONF_SRC" "$HTTP_CONF"
nginx -t
systemctl reload nginx
echo "NGINX_OK backup=${STAMP}"
