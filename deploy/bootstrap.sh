#!/usr/bin/env bash
set -euo pipefail

REPO_URL="${REPO_URL:-git@github.com:youngrezzee/bot_napominatel.git}"
HTTPS_REPO_URL="${HTTPS_REPO_URL:-https://github.com/youngrezzee/bot_napominatel.git}"
INSTALL_DIR="${INSTALL_DIR:-/opt/bot_napominatel}"
BOT_TIMEZONE="${BOT_TIMEZONE:-Europe/Moscow}"

prompt_tty() {
  local __resultvar="$1"
  local prompt="$2"
  local value=""

  if [[ -r /dev/tty ]]; then
    read -r -p "${prompt}" value < /dev/tty
  else
    echo "Interactive terminal not available. Set TELEGRAM_BOT_TOKEN before running bootstrap."
    exit 1
  fi

  printf -v "${__resultvar}" '%s' "${value}"
}

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
if [[ -z "${TELEGRAM_BOT_TOKEN:-}" ]]; then
  prompt_tty TELEGRAM_BOT_TOKEN "Enter Telegram bot token: "
fi

if [[ -z "${TELEGRAM_BOT_TOKEN}" ]]; then
  echo "Telegram bot token is required."
  exit 1
fi

echo "[4/5] Running installer"
export TELEGRAM_BOT_TOKEN
export BOT_TIMEZONE
export SKIP_OPTIONAL_PROMPTS=1
bash "${INSTALL_DIR}/deploy/install.sh"

echo "[5/5] Done"
echo "Bot installed in ${INSTALL_DIR}"
echo "Service name: reminder-bot"
echo "Logs: journalctl -u reminder-bot -f"
