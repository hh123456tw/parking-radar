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
SUDOERS="/etc/sudoers.d/parking-radar-deploy"

id deploy >/dev/null 2>&1 || useradd --create-home --shell /bin/bash deploy
passwd --lock deploy >/dev/null

install -m 755 -o root -g root "$SRC/parking-radar-receive" /usr/local/sbin/parking-radar-receive
install -m 750 -o root -g root "$SRC/deploy.sh" /usr/local/sbin/parking-radar-deploy

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
