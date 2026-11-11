#!/usr/bin/env bash
# One-time (and safe to re-run) setup of OctoNews on a fresh Ubuntu 24.04 server.
# Installs Docker + WAHA, the bot in a Python venv, and a systemd timer that runs it every 30 minutes.
set -euo pipefail

REPO_URL="https://github.com/MohammadAlMokdad/OctoNews.git"
STATE_URL="https://raw.githubusercontent.com/MohammadAlMokdad/OctoNews/bot-state/state.json"
APP=/opt/octonews

echo "==> Timezone and swap (the free server has 1 GB RAM)"
sudo timedatectl set-timezone Asia/Beirut
if ! swapon --show | grep -q /swapfile; then
  sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile && sudo mkswap -q /swapfile && sudo swapon /swapfile
  grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab >/dev/null
fi

echo "==> Packages"
sudo apt-get update -qq
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq docker.io docker-compose-v2 python3-venv git curl openssl >/dev/null
sudo systemctl enable --now docker >/dev/null

echo "==> Code"
sudo mkdir -p "$APP" && sudo chown "$USER:$USER" "$APP"
if [ -d "$APP/.git" ]; then git -C "$APP" pull -q; else git clone -q "$REPO_URL" "$APP"; fi
cd "$APP"
python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements.txt
mkdir -p data waha/sessions waha/media

echo "==> Bot state (carry over what was already posted, so nothing is repeated)"
if [ ! -s data/state.json ]; then curl -fsSL "$STATE_URL" -o data/state.json || rm -f data/state.json; fi

echo "==> Secrets"
touch .env && chmod 600 .env
grep -q '^WAHA_API_KEY=' .env || echo "WAHA_API_KEY=$(openssl rand -hex 24)" >> .env
grep -q '^WHATSAPP_PROVIDER=' .env || echo "WHATSAPP_PROVIDER=waha" >> .env

echo "==> WAHA"
sudo docker compose -f deploy/docker-compose.yml --env-file .env up -d --quiet-pull

echo "==> Scheduler"
sudo cp deploy/octonews.service deploy/octonews.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now octonews.timer >/dev/null
systemctl list-timers octonews.timer --no-pager | head -3
echo "==> Done"
