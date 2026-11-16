#!/usr/bin/env bash
# 개발 PC → Jenkins 서버로 시크릿·설정 복사 (git 을 거치지 않음)
#   ./scripts/upload-secrets.sh <ssh 키.pem> [user@host]
set -euo pipefail
cd "$(dirname "$0")/.."
KEY=${1:?Jenkins 서버 ssh 키(.pem)}
DEST=${2:-ec2-user@13.125.111.48}
ssh -i "$KEY" "$DEST" 'mkdir -p ~/Receipt2Vec/dev-ops/secrets && chmod 700 ~/Receipt2Vec/dev-ops/secrets'
scp -i "$KEY" -p secrets/* "$DEST:Receipt2Vec/dev-ops/secrets/"
scp -i "$KEY" -p .env "$DEST:Receipt2Vec/dev-ops/.env"
ssh -i "$KEY" "$DEST" 'chmod 600 ~/Receipt2Vec/dev-ops/secrets/* ~/Receipt2Vec/dev-ops/.env'
echo "업로드 완료 → 서버에서: cd ~/Receipt2Vec/dev-ops && bash scripts/bootstrap-jenkins-host.sh"
