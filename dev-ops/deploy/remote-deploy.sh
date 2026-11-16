#!/usr/bin/env bash
# =============================================================================
# RAG 서버에서 실행되는 배포 스크립트 (Jenkins 가 ssh 로 호출)
#   ssh rag-server "bash ~/receipt2vec/remote-deploy.sh <dockerhub 사용자>" <<< "<dockerhub 비밀번호>"
#
# 1) Docker Hub 로그인 (임시 설정 폴더 → 서버에 자격증명을 남기지 않음)
# 2) 최신 이미지 pull → 컨테이너 교체 (데이터 볼륨은 유지)
# 3) 상태 확인 (gateway, auth-server, rag backend 모델 로딩 완료까지)
# =============================================================================
set -euo pipefail
cd "$(dirname "$0")"
DH_USER=${1:?Docker Hub 사용자명}

# compose 플러그인은 시스템 경로(/usr/local/lib/docker/cli-plugins)에 있어야 임시 DOCKER_CONFIG 에서도 동작
export DOCKER_CONFIG
DOCKER_CONFIG=$(mktemp -d)
trap 'rm -rf "$DOCKER_CONFIG"' EXIT
docker login -u "$DH_USER" --password-stdin >/dev/null
echo "[deploy] Docker Hub 로그인 완료"

C="docker compose -f docker-compose.yml -f docker-compose.prod.yml"
set -a; . ./.env; set +a
echo "[deploy] 이미지 ${IMAGE_REPO}:*-${IMAGE_TAG:-latest} pull"
$C pull --quiet
$C up -d --remove-orphans --no-build
docker image prune -f >/dev/null
echo "[deploy] 컨테이너 교체 완료"

# rag backend 는 임베딩·리랭커 모델 로딩에 수 분 걸림 → 최대 15분 대기
ok() { curl -fs -o /dev/null --max-time 5 "$1"; }
for i in $(seq 1 90); do
  g=0; a=0; b=0
  ok http://localhost/gateway/health && g=1
  ok http://localhost/api/auth/providers && a=1
  docker exec ragbase-backend python -c "import urllib.request;urllib.request.urlopen('http://localhost:3001/', timeout=3)" \
    >/dev/null 2>&1 && b=1
  if [ $g$a$b = 111 ]; then
    echo "[deploy] 상태 확인 통과 (${i}회차): gateway·auth-server·rag backend 정상"
    $C ps --format 'table {{.Name}}\t{{.Image}}\t{{.Status}}'
    exit 0
  fi
  [ $((i % 6)) = 0 ] && echo "[deploy] 대기 중… gateway=$g auth=$a backend=$b ($((i * 10))초)"
  sleep 10
done
echo "[deploy] 상태 확인 실패 — 최근 로그:" >&2
$C ps >&2
$C logs --tail 40 backend auth-server gateway >&2 || true
exit 1
