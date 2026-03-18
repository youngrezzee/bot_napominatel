#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
SERVICE_PATH="/etc/systemd/system/reminder-bot.service"
TOKEN="${1:-${TELEGRAM_BOT_TOKEN:-}}"
TIMEZONE_VALUE="${BOT_TIMEZONE:-Europe/Moscow}"

if [[ -z "${TOKEN}" ]]; then
  read -r -p "Enter Telegram bot token: " TOKEN
fi

if [[ -z "${TOKEN}" ]]; then
  echo "Telegram bot token is required."
  exit 1
fi

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run this script with sudo or as root."
  exit 1
fi

APP_USER="${SUDO_USER:-root}"
if id "${APP_USER}" >/dev/null 2>&1; then
  APP_HOME="$(getent passwd "${APP_USER}" | cut -d: -f6)"
else
  APP_USER="root"
  APP_HOME="/root"
fi

echo "[1/6] Installing system packages"
if command -v apt-get >/dev/null 2>&1; then
  apt-get update
  apt-get install -y git python3 python3-pip python3-venv
else
  echo "apt-get not found. Install python3, python3-pip and python3-venv manually."
fi

echo "[2/6] Creating virtual environment"
cd "${REPO_DIR}"
python3 -m venv .venv

echo "[3/6] Installing Python dependencies"
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt

read -r -p "Timezone [${TIMEZONE_VALUE}]: " INPUT_TIMEZONE
TIMEZONE_VALUE="${INPUT_TIMEZONE:-${TIMEZONE_VALUE}}"

echo "[4/6] Writing .env"
cat > "${REPO_DIR}/.env" <<EOF
TELEGRAM_BOT_TOKEN=${TOKEN}
BOT_TIMEZONE=${TIMEZONE_VALUE}
EOF

echo "[5/6] Creating systemd service"
cat > "${SERVICE_PATH}" <<EOF
[Unit]
Description=Telegram Reminder Bot
After=network.target

[Service]
Type=simple
User=${APP_USER}
WorkingDirectory=${REPO_DIR}
EnvironmentFile=${REPO_DIR}/.env
ExecStart=${REPO_DIR}/.venv/bin/python ${REPO_DIR}/bot.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

echo "[6/6] Enabling and starting service"
systemctl daemon-reload
systemctl enable reminder-bot
systemctl restart reminder-bot

echo
echo "Done."
echo "Service status:"
systemctl --no-pager --full status reminder-bot || true
echo
echo "Useful commands:"
echo "  sudo systemctl status reminder-bot"
echo "  sudo systemctl restart reminder-bot"
echo "  sudo journalctl -u reminder-bot -f"
