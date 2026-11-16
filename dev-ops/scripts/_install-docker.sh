#!/usr/bin/env bash
# Docker Engine + Compose 플러그인(시스템 경로) 설치 — Amazon Linux 2023 / Ubuntu 공통 (bootstrap 스크립트가 사용)
set -euo pipefail
if command -v dnf >/dev/null 2>&1; then
  sudo dnf install -y docker git rsync jq
elif command -v apt-get >/dev/null 2>&1; then
  sudo apt-get update -y && sudo apt-get install -y docker.io git rsync jq curl
fi
sudo systemctl enable --now docker
sudo usermod -aG docker "$USER" || true
# compose v2 (!reset 문법 위해 2.24+) 를 모든 사용자가 쓰는 시스템 경로에 설치
COMPOSE_VERSION=v2.32.4
sudo mkdir -p /usr/local/lib/docker/cli-plugins
sudo curl -fsSL "https://github.com/docker/compose/releases/download/${COMPOSE_VERSION}/docker-compose-linux-$(uname -m)" \
  -o /usr/local/lib/docker/cli-plugins/docker-compose
sudo chmod +x /usr/local/lib/docker/cli-plugins/docker-compose
docker compose version || sudo docker compose version
