"""
운영·테스트용 토큰 발급 CLI (컨테이너 안에서만 실행 → 서명 키에 접근할 수 있는 사람만 사용 가능)

  docker exec receipt2vec-auth-server python -m app.cli token --sub eval
  docker exec receipt2vec-auth-server python -m app.cli token --sub <user_uid> --role ADMIN --ttl 3600
  docker exec receipt2vec-auth-server python -m app.cli promote --sub <user_uid>     # 관리자 지정

token: 실제 로그인과 같은 세션(Redis)을 만들고 access token 을 출력한다 (평가 스크립트·관리 작업용).
       MySQL 사용자 없이도 발급 가능 (sub 를 그대로 사용자 ID 로 씀).
"""
import argparse
import sys
from dataclasses import replace

from sqlalchemy import text

from app.core.config import settings
from app.db.mysql import engine
from app.services import users
from app.services.tokens import TokenService, load_or_create_key


def redis_client():
    # app.main 을 import 하면 로깅 설정이 stdout 에 출력돼 토큰 출력과 섞이므로 여기서 직접 만든다
    import redis
    return redis.Redis(host=settings.redis_host, port=settings.redis_port, db=settings.redis_db,
                       password=settings.redis_password, decode_responses=True, socket_timeout=5)


def main() -> int:
    ap = argparse.ArgumentParser(prog="python -m app.cli")
    sub = ap.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("token", help="access token 발급")
    t.add_argument("--sub", required=True, help="사용자 ID (users.user_uid 또는 테스트용 임의 ID)")
    t.add_argument("--role", default=None, help="USER/ADMIN (기본: DB 값, 없으면 USER)")
    t.add_argument("--ttl", type=int, default=None, help="유효 시간(초), 기본 ACCESS_TOKEN_TTL_SEC")
    p = sub.add_parser("promote", help="사용자를 ADMIN 으로")
    p.add_argument("--sub", required=True)
    args = ap.parse_args()

    if args.cmd == "promote":
        with engine().begin() as c:
            n = c.execute(text("UPDATE users SET role = 'ADMIN' WHERE user_uid = :u"), {"u": args.sub}).rowcount
        print("ADMIN 지정 완료" if n else "사용자 없음", file=sys.stderr)
        return 0 if n else 1

    user = users.get_user(args.sub) or users.User(id=0, user_uid=args.sub, email=None, name=args.sub,
                                                   profile_image_url=None, role="USER", status="ACTIVE")
    if args.role:
        user = replace(user, role=args.role.upper())
    svc = TokenService(redis_client(), load_or_create_key(settings.jwt_key_path))
    if args.ttl:
        svc.s = replace(settings, access_ttl_sec=args.ttl)
    sid, _ = svc.start_session(user, "cli", None, "app.cli")
    token, claims = svc.issue_access(user, sid, "cli")
    print(f"sub={claims['sub']} role={claims['role']} exp={claims['exp']} sid={sid}", file=sys.stderr)
    print(token)
    return 0


if __name__ == "__main__":
    sys.exit(main())
