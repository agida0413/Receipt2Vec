#!/bin/sh
# =============================================================================
# gateway 시작 스크립트 (docker-compose 의 command 로 실행: sh /etc/nginx/r2v/start.sh)
#
#   DOMAIN 없음 (로컬 개발)        → HTTP 로 앱 서비스 (http://localhost:3000)
#   DOMAIN 있음 + 인증서 아직 없음 → HTTP 로 앱 서비스 + 인증서 발급용 경로(/.well-known/acme-challenge)
#   DOMAIN 있음 + 인증서 있음      → HTTPS 로 앱 서비스, HTTP 는 HTTPS 로 리다이렉트
#
# 인증서는 certbot 컨테이너가 발급·갱신 → 여기서 1분마다 확인해 생기면 자동으로 HTTPS 전환(재시작 없음),
# 12시간마다 다시 읽어 갱신된 인증서 반영.
# =============================================================================
set -eu
RUNTIME=/etc/nginx/runtime
mkdir -p "$RUNTIME"
CERT="/etc/letsencrypt/live/${DOMAIN:-_none_}/fullchain.pem"

has_cert() { [ -n "${DOMAIN:-}" ] && [ -s "$CERT" ]; }

apply() {
  if has_cert; then
    envsubst '${DOMAIN} ${SERVER_NAME}' < /etc/nginx/r2v/https.conf.template > /etc/nginx/conf.d/https.conf
    # HTTP 는 모두 대표 주소의 HTTPS 로 (kyj.ai.kr·IP 로 와도 https://${DOMAIN})
    echo "location / { set \$route redirect; return 301 https://${DOMAIN}\$request_uri; }" > "$RUNTIME/http-mode.conf"
    MODE=https
  else
    rm -f /etc/nginx/conf.d/https.conf
    echo 'include /etc/nginx/r2v/app-locations.conf;' > "$RUNTIME/http-mode.conf"
    MODE=http
  fi
}

apply
echo "[gateway] 시작 모드: $MODE (DOMAIN=${DOMAIN:-없음})"

# 인증서 생김/갱신 감시 (nginx 를 다시 읽기만 하므로 연결 끊김 없음)
(
  i=0
  while sleep 60; do
    i=$((i + 1))
    prev=$MODE
    apply
    if [ "$MODE" != "$prev" ] || [ $((i % 720)) -eq 0 ]; then
      if nginx -t -q 2>/dev/null; then
        nginx -s reload && echo "[gateway] 설정 다시 읽음 (모드 $prev → $MODE)"
      else
        echo "[gateway] 설정 오류 → 다시 읽지 않음" >&2
      fi
    fi
  done
) &

exec /docker-entrypoint.sh nginx -g 'daemon off;'
