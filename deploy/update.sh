#!/usr/bin/env bash
# Pull the latest code onto the server and restart what changed.
set -euo pipefail
cd /opt/octonews
git pull -q
.venv/bin/pip install -q -r requirements.txt
sudo docker compose -f deploy/docker-compose.yml --env-file .env up -d --quiet-pull
sudo cp deploy/octonews.service deploy/octonews.timer /etc/systemd/system/
sudo systemctl daemon-reload
echo "Updated to $(git log --oneline -1)"
