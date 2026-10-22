#!/usr/bin/env bash
# 在伺服器上以 root 執行：sudo bash install-deploy-user.sh <deploy 公鑰檔> <已驗證的 git checkout 目錄>
# 建立 GitHub Actions 專用的 deploy 使用者：SSH 只能執行 parking-radar-receive，
# sudo 只能執行事先安裝的 deploy.sh。deploy.sh 有修改時，重新執行本腳本更新。
# 來源必須是 root 自己 checkout 的目錄；/opt/parking-hell* 可被 parking 改寫，不能當成 root 腳本來源。
set -Eeuo pipefail

PUBKEY_FILE="${1:?usage: install-deploy-user.sh <public key file> <verified git checkout>}"
SRC_ROOT="$(realpath -e "${2:?usage: install-deploy-user.sh <public key file> <verified git checkout>}")"
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
RECEIVE_SRC="$(verified_source "$SRC/parking-radar-receive")"
DEPLOY_SRC="$(verified_source "$SRC/deploy.sh")"
SUDOERS="/etc/sudoers.d/parking-radar-deploy"

id deploy >/dev/null 2>&1 || useradd --create-home --shell /bin/bash deploy
passwd --lock deploy >/dev/null

install -m 755 -o root -g root "$RECEIVE_SRC" /usr/local/sbin/parking-radar-receive
install -m 750 -o root -g root "$DEPLOY_SRC" /usr/local/sbin/parking-radar-deploy

install -d -m 700 -o deploy -g deploy /home/deploy/.ssh
# restrict 關閉轉送、PTY 等功能；command= 讓這把金鑰只能跑接收腳本。
printf 'restrict,command="/usr/local/sbin/parking-radar-receive" %s\n' \
  "$(head -n 1 "$PUBKEY_FILE")" > /home/deploy/.ssh/authorized_keys
chown deploy:deploy /home/deploy/.ssh/authorized_keys
chmod 600 /home/deploy/.ssh/authorized_keys

printf 'deploy ALL=(root) NOPASSWD: /usr/local/sbin/parking-radar-deploy\n' > "${SUDOERS}.tmp"
visudo -cf "${SUDOERS}.tmp"
install -m 440 -o root -g root "${SUDOERS}.tmp" "$SUDOERS"
rm -f "${SUDOERS}.tmp"
echo "DEPLOY_USER_OK"
