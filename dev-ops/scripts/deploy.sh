#!/usr/bin/env bash
# =============================================================================
# 배포 묶음을 만들어 RAG 서버로 보내고 remote-deploy.sh 실행 (Jenkins 'Deploy' 단계에서 호출)
#
# 필요한 환경변수 (Jenkins withCredentials/sshagent 가 제공):
#   RAG_HOST RAG_SSH_USER IMAGE_REPO DEPLOY_TAG
#   DH_USER DH_PASS                          Docker Hub
#   ROOT_ENV RAG_ENV OCR_ENV AUTH_ENV        운영 환경 파일 (Jenkins 비밀 파일 경로)
#
# 서버의 ~/receipt2vec 에 compose·설정·환경 파일만 올라감 (소스 코드 없음, 이미지는 Docker Hub 에서 pull)
# =============================================================================
set -euo pipefail
: "${RAG_HOST:?}" "${RAG_SSH_USER:?}" "${IMAGE_REPO:?}" "${DH_USER:?}" "${DH_PASS:?}"
: "${ROOT_ENV:?}" "${RAG_ENV:?}" "${OCR_ENV:?}" "${AUTH_ENV:?}"
TAG=${DEPLOY_TAG:-latest}
B=deploy-bundle
TARGET="${RAG_SSH_USER}@${RAG_HOST}"
SSH_OPTS=(-o StrictHostKeyChecking=accept-new -o ServerAliveInterval=30 -o BatchMode=yes)

rm -rf "$B"
mkdir -p "$B/rag_backend/app" "$B/ocr_backend" "$B/auth_server/app/db" "$B/docker"
cp docker-compose.yml dev-ops/deploy/docker-compose.prod.yml dev-ops/deploy/remote-deploy.sh "$B/"
cp -r docker/gateway docker/logging "$B/docker/"
cp auth_server/app/db/schema.sql "$B/auth_server/app/db/"
if [ -d rag_backend/app/test ]; then cp -r rag_backend/app/test "$B/rag_backend/app/"; else mkdir -p "$B/rag_backend/app/test"; fi

# 환경 파일 (권한 600). 이미지 저장소·태그를 루트 .env 에 추가
install -m 600 "$ROOT_ENV" "$B/.env"
printf '\n# Jenkins 배포 (빌드 #%s, 커밋 %s)\nIMAGE_REPO=%s\nIMAGE_TAG=%s\n' \
  "${BUILD_NUMBER:-?}" "${GIT_SHA:-?}" "$IMAGE_REPO" "$TAG" >> "$B/.env"
install -m 600 "$RAG_ENV" "$B/rag_backend/.env"
install -m 600 "$OCR_ENV" "$B/ocr_backend/.env"
install -m 600 "$AUTH_ENV" "$B/auth_server/.env"

echo "[deploy] ${TARGET}:~/receipt2vec 로 전송 (태그 ${TAG})"
ssh "${SSH_OPTS[@]}" "$TARGET" 'mkdir -p ~/receipt2vec && chmod 700 ~/receipt2vec'
rsync -az --chmod=Du=rwx,Dgo=,Fu=rw,Fgo= -e "ssh ${SSH_OPTS[*]}" "$B/" "$TARGET:receipt2vec/"
# Docker Hub 비밀번호는 표준입력으로만 전달 (명령줄·로그에 남지 않음)
printf '%s' "$DH_PASS" | ssh "${SSH_OPTS[@]}" "$TARGET" "bash ~/receipt2vec/remote-deploy.sh '$DH_USER'"
rm -rf "$B"
