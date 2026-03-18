#!/usr/bin/env bash
set -euo pipefail

REPO_URL="${REPO_URL:-git@github.com:youngrezzee/bot_napominatel.git}"
HTTPS_REPO_URL="${HTTPS_REPO_URL:-https://github.com/youngrezzee/bot_napominatel.git}"
INSTALL_DIR="${INSTALL_DIR:-/opt/bot_napominatel}"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run this script as root:"
  echo "  sudo bash bootstrap.sh"
  exit 1
fi

echo "[1/5] Installing base packages"
if command -v apt-get >/dev/null 2>&1; then
  apt-get update
  apt-get install -y git curl python3 python3-pip python3-venv
else
  echo "apt-get not found. Install git, curl, python3, python3-pip and python3-venv manually."
  exit 1
fi

echo "[2/5] Downloading repository"
if [[ -d "${INSTALL_DIR}/.git" ]]; then
  git -C "${INSTALL_DIR}" pull
else
  rm -rf "${INSTALL_DIR}"
  if git clone "${REPO_URL}" "${INSTALL_DIR}" 2>/dev/null; then
    echo "Cloned via SSH"
  else
    git clone "${HTTPS_REPO_URL}" "${INSTALL_DIR}"
    echo "Cloned via HTTPS"
  fi
fi

echo "[3/5] Asking for bot settings"
read -r -p "Enter Telegram bot token: " TELEGRAM_BOT_TOKEN
read -r -p "Timezone [Europe/Moscow]: " BOT_TIMEZONE
BOT_TIMEZONE="${BOT_TIMEZONE:-Europe/Moscow}"

echo "[4/5] Running installer"
export TELEGRAM_BOT_TOKEN
export BOT_TIMEZONE
bash "${INSTALL_DIR}/deploy/install.sh"

echo "[5/5] Done"
echo "Bot installed in ${INSTALL_DIR}"
echo "Service name: reminder-bot"
echo "Logs: journalctl -u reminder-bot -f"
