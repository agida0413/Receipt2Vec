"""
Receipt2Vec 인증·인가 서버 (독립 서비스, 포트 3003)

  - 소셜 로그인: Google / Naver / Kakao (OAuth2 Authorization Code)
  - MySQL : 사용자·소셜 계정·제공자 설정·로그인 이력
  - Redis : OAuth state, refresh token(회전·재사용 탐지), access token 블랙리스트
  - JWT   : RS256, 공개키는 /.well-known/jwks.json
"""
import logging
from contextlib import asynccontextmanager

import redis
from fastapi import FastAPI
from fastapi.concurrency import run_in_threadpool

from app.api.routes import router
from app.core.config import settings
from app.core.logging_setup import RequestLoggingMiddleware, setup_logging
from app.db import mysql
from app.services import users
from app.services.providers import SCOPES
from app.services.tokens import TokenService, load_or_create_key

setup_logging("auth-server")
logger = logging.getLogger(__name__)


def redis_client() -> redis.Redis:
    return redis.Redis(host=settings.redis_host, port=settings.redis_port, db=settings.redis_db,
                       password=settings.redis_password, decode_responses=True, socket_timeout=5,
                       socket_connect_timeout=5, health_check_interval=30)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await run_in_threadpool(mysql.init_schema)
    await run_in_threadpool(users.sync_providers, SCOPES)
    r = redis_client()
    r.ping()
    logger.info(f"Redis 연결 host={settings.redis_host}:{settings.redis_port} db={settings.redis_db}")
    app.state.tokens = TokenService(r, load_or_create_key(settings.jwt_key_path))
    logger.info(f"인증 서버 시작 public={settings.public_base_url} access_ttl={settings.access_ttl_sec}s "
                f"refresh_ttl={settings.refresh_ttl_sec}s secure_cookie={settings.cookie_secure}")
    yield
    r.close()


app = FastAPI(title="Receipt2Vec Auth", version="1.0.0", lifespan=lifespan)
app.add_middleware(RequestLoggingMiddleware, service="auth-server")
app.include_router(router)


@app.get("/")
async def health():
    from sqlalchemy import text

    def check():
        with mysql.engine().connect() as c:
            c.execute(text("SELECT 1"))
        app.state.tokens.r.ping()

    await run_in_threadpool(check)
    return {"status": "ok", "service": "auth-server",
            "providers": {n: c.enabled for n, c in settings.providers.items()}}
