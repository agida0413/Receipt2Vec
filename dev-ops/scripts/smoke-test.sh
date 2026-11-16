#!/usr/bin/env bash
# =============================================================================
# 배포 후 외부 확인 (Jenkins 'Smoke test' 단계)
#   종료 코드 0: 도메인(HTTPS)으로 정상
#            2: 도메인은 아직 안 되지만 서버 IP(HTTP)로는 정상 (DNS 미설정·인증서 발급 전) → UNSTABLE
#            1: 서비스 이상 → FAILURE
# 확인 항목: gateway 상태, 인증 서버 응답, 토큰 없는 API 가 401 (인증 검사 동작)
# =============================================================================
set -uo pipefail
set -a; . dev-ops/deploy/prod.env; set +a
: "${PUBLIC_BASE_URL:?}" "${RAG_HOST:?}"

check() {
  local base=$1 code
  curl -fsS --max-time 10 -o /dev/null "$base/gateway/health" || return 1
  curl -fsS --max-time 10 -o /dev/null "$base/api/auth/providers" || return 1
  code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "$base/api/transactions")
  [ "$code" = 401 ] || { echo "  $base/api/transactions → $code (401 이어야 함)"; return 1; }
}

# 첫 배포는 certbot 이 인증서를 받는 데 1~2분 걸림 → 최대 3분 대기
for i in $(seq 1 18); do
  if check "$PUBLIC_BASE_URL"; then
    echo "외부 확인 통과: $PUBLIC_BASE_URL (gateway·인증 정상, 인증 없는 API 401)"
    exit 0
  fi
  sleep 10
done

echo "경고: $PUBLIC_BASE_URL 접속 실패 — DNS A 레코드(${CERT_DOMAINS:-$DOMAIN} → $RAG_HOST) 또는 인증서 발급 상태 확인"
echo "      서버에서: docker logs ragbase-certbot --tail 20"
if check "http://$RAG_HOST"; then
  echo "서버 IP(HTTP)로는 정상: http://$RAG_HOST"
  exit 2
fi
echo "서버 IP(HTTP)로도 실패 → 서비스 이상"
exit 1
