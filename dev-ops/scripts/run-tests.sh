#!/usr/bin/env bash
# 빌드한 이미지 안에서 단위 테스트 실행 (Jenkins 에서 호출)
#   run-tests.sh <서비스 폴더> <이미지>
# Jenkins 는 호스트 Docker 를 쓰므로, 작업공간(jenkins_home 볼륨)을 --volumes-from 으로 테스트 컨테이너에 공유한다.
set -euo pipefail
DIR=${1:?서비스 폴더}; IMAGE=${2:?이미지}
SELF=$(hostname)   # Jenkins 컨테이너 ID
EXTRA_ENV=()
if [ "$DIR" = auth_server ]; then   # 설정 검증용 더미 값 (실제 키 아님)
  EXTRA_ENV=(-e GOOGLE_CLIENT_ID=t -e GOOGLE_CLIENT_SECRET=t -e NAVER_CLIENT_ID=t -e NAVER_CLIENT_SECRET=t -e KAKAO_CLIENT_ID=t)
fi
docker run --rm --volumes-from "$SELF" -w "$WORKSPACE/$DIR" -e PYTHONDONTWRITEBYTECODE=1 "${EXTRA_ENV[@]}" \
  --entrypoint sh "$IMAGE" -c \
  "pip install -q pytest 'PyJWT[crypto]>=2.8,<3' >/dev/null 2>&1 && python -m pytest -q -p no:cacheprovider tests --ignore=tests/eval"
