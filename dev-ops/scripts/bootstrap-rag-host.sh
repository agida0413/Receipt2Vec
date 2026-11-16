#!/usr/bin/env bash
# =============================================================================
# RAG 서버(EC2 15.164.106.172) 최초 설정 — 서버에서 한 번 실행 (이후 배포는 Jenkins 가 자동)
#   scp dev-ops/scripts/_install-docker.sh dev-ops/scripts/bootstrap-rag-host.sh ec2-user@15.164.106.172:
#   ssh ec2-user@15.164.106.172 'bash bootstrap-rag-host.sh'
# =============================================================================
set -euo pipefail
cd "$(dirname "$0")"
bash ./_install-docker.sh
# OpenSearch 필수 커널 설정
echo 'vm.max_map_count=262144' | sudo tee /etc/sysctl.d/99-opensearch.conf >/dev/null
sudo sysctl -w vm.max_map_count=262144
# 메모리 여유가 적으면 스왑 4GB (임베딩 모델 + OpenSearch + OCR 동시 로딩 대비)
if [ "$(free -m | awk '/^Mem:/{print $2}')" -lt 15000 ] && ! swapon --show | grep -q swapfile; then
  sudo fallocate -l 4G /swapfile && sudo chmod 600 /swapfile && sudo mkswap /swapfile && sudo swapon /swapfile
  echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab >/dev/null
fi
mkdir -p ~/receipt2vec && chmod 700 ~/receipt2vec
echo "완료. 보안그룹: 80(사용자), 22(Jenkins 서버 13.125.111.48 만) 허용"
