#!/usr/bin/env bash
# =============================================================================
# dev-ops/secrets/ 시크릿 파일 생성 (개발 PC 에서 실행 → upload-secrets.sh 로 Jenkins 서버에 복사)
#
#   ./scripts/init-secrets.sh            # 없는 파일만 생성 (있는 값은 유지)
#   FORCE=1 ./scripts/init-secrets.sh    # 자동 생성 값(비밀번호)도 새로 만듦
#
# - 운영 DB/Redis/Grafana/Jenkins 비밀번호는 새로 생성 (로컬 개발 값과 분리)
# - 앱 키(Gemini, OAuth, Slack)는 로컬 .env 파일에서 가져옴
# - Docker Hub 비밀번호·AWS 키·SSH 키는 파일이 없으면 입력받음 (화면에 표시하지 않음)
# =============================================================================
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT=..
S=secrets
umask 077
mkdir -p "$S"

gen() { openssl rand -hex 24; }
env_get() { grep -E "^$2=" "$1" 2>/dev/null | head -1 | cut -d= -f2- || true; }
have() { [ -s "$S/$1" ] && [ "${FORCE:-0}" != 1 ]; }
put() { printf '%s' "$2" > "$S/$1"; echo "  생성: $1"; }

echo "[1] 자동 생성 비밀번호"
have jenkins_admin_password || put jenkins_admin_password "$(gen)"

echo "[2] 입력이 필요한 값 (이미 있으면 건너뜀)"
ask() {  # ask <파일> <설명>
  if [ -s "$S/$1" ]; then echo "  유지: $1"; return; fi
  if [ -t 0 ]; then read -r -s -p "  $2: " v; echo; printf '%s' "$v" > "$S/$1"; echo "  저장: $1"
  else : > "$S/$1"; echo "  비어 있음: $1 ($2 — 나중에 채우세요)"; fi
}
ask dockerhub_password "Docker Hub 비밀번호 또는 Access Token"
ask aws_access_key_id "AWS Access Key ID"
ask aws_secret_access_key "AWS Secret Access Key"
[ -e "$S/rag_server_ssh_key" ] || { : > "$S/rag_server_ssh_key"; echo "  비어 있음: rag_server_ssh_key (RAG 서버 EC2 .pem 내용을 넣으세요)"; }
[ -e "$S/github_token" ] || { : > "$S/github_token"; echo "  비어 있음: github_token (공개 저장소면 그대로 둠)"; }

echo "[3] Slack 웹훅 (루트 .env 에서)"
have slack_webhook_url || put slack_webhook_url "$(env_get $ROOT/.env SLACK_WEBHOOK_URL)"

echo "[4] 운영 환경 파일"
PUBLIC_BASE_URL=$(env_get .env PUBLIC_BASE_URL)
if ! have app_root_env; then
  cat > "$S/app_root_env" <<EOF
# 운영 루트 .env (Jenkins 가 배포 시 서버의 ~/receipt2vec/.env 로 복사)
MYSQL_ROOT_PASSWORD=$(gen)
MYSQL_PASSWORD=$(gen)
REDIS_PASSWORD=$(gen)
GRAFANA_ADMIN_PASSWORD=$(gen)
SLACK_WEBHOOK_URL=$(env_get $ROOT/.env SLACK_WEBHOOK_URL)
# 단일 진입점 gateway 를 80 포트로
GATEWAY_PUBLISH_PORT=80
SERVER_NAME=_
PUBLIC_BASE_URL=${PUBLIC_BASE_URL:-http://localhost}
EOF
  echo "  생성: app_root_env (새 DB·Redis 비밀번호)"
fi
copy_env() {  # copy_env <secret> <원본> [추가 줄...]
  local name=$1 src=$2; shift 2
  if have "$name"; then echo "  유지: $name"; return; fi
  [ -f "$src" ] || { echo "  경고: $src 없음 → 빈 파일"; : > "$S/$name"; return; }
  { cat "$src"; echo; for line in "$@"; do echo "$line"; done; } > "$S/$name"
  echo "  생성: $name (← $src)"
}
copy_env app_rag_backend_env $ROOT/rag_backend/.env
copy_env app_ocr_backend_env $ROOT/ocr_backend/.env
copy_env app_auth_server_env $ROOT/auth_server/.env "COOKIE_SECURE=false"

echo
echo "완료. 파일 목록 (내용은 출력하지 않음):"
for f in "$S"/*; do [ "$(basename "$f")" = README.md ] && continue; printf '  %-26s %s bytes\n' "$(basename "$f")" "$(wc -c < "$f" | tr -d ' ')"; done
