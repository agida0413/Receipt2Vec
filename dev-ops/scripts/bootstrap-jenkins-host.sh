#!/usr/bin/env bash
# =============================================================================
# Jenkins 서버(EC2 13.125.111.48) 최초 설정 — 서버에서 실행
#   1) 서버: git clone https://github.com/agida0413/Receipt2Vec.git ~/Receipt2Vec
#   2) 개발 PC: ./scripts/upload-secrets.sh <키.pem>   (secrets/ 와 .env 를 git 없이 복사)
#   3) 서버: cd ~/Receipt2Vec/dev-ops && bash scripts/bootstrap-jenkins-host.sh
# =============================================================================
set -euo pipefail
cd "$(dirname "$0")/.."
bash scripts/_install-docker.sh
[ -f .env ] || cp .env.example .env
GID=$(getent group docker | cut -d: -f3)
sed -i "s/^DOCKER_GID=.*/DOCKER_GID=${GID}/" .env
for f in jenkins_admin_password dockerhub_password rag_server_ssh_key aws_access_key_id aws_secret_access_key \
         github_token slack_webhook_url app_root_env app_rag_backend_env app_ocr_backend_env app_auth_server_env; do
  [ -e "secrets/$f" ] || { echo "secrets/$f 없음 → 개발 PC 에서 upload-secrets.sh 실행"; exit 1; }
done
[ -s secrets/rag_server_ssh_key ] || echo "경고: secrets/rag_server_ssh_key 가 비어 있음 (배포 단계 실패)"
chmod 600 secrets/*
# Jenkins 컨테이너(uid 1000)가 docker secret 파일을 읽을 수 있도록 소유자 지정
sudo chown 1000:1000 secrets/* 2>/dev/null || true
sudo docker compose up -d --build
echo "Jenkins: $(grep ^JENKINS_URL .env | cut -d= -f2)  (admin / secrets/jenkins_admin_password)"
