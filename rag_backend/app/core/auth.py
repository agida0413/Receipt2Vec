"""
API 인증 (auth-server 가 발급한 JWT 검증) — rag_backend / ocr_backend 공통 (같은 파일을 두 서비스에 둠)

  - 모든 /api/* 요청에 Authorization: Bearer <access token> 필수 (헬스체크 제외)
  - 서명은 auth-server 의 공개키(JWKS)로 이 서비스에서 직접 검증 → auth-server 에 매 요청 묻지 않음
    (로그아웃·세션 종료 같은 '취소' 는 gateway 의 auth_request 가 auth-server 에 확인해서 막음)
  - 토큰 주인(sub) = 사용자 ID. 요청의 userId 가 다르면 403 (남의 거래 조회·삭제 방지), 없으면 sub 를 씀
  - 서비스 간 호출(ocr → rag)은 사용자 토큰을 그대로 전달 (forward_auth_headers)

환경변수
  AUTH_REQUIRED   기본 true. false 면 검사하지 않음 (로컬 단위 테스트 등)
  AUTH_JWKS_URL   기본 http://auth-server:3003/.well-known/jwks.json
  AUTH_ISSUER / AUTH_AUDIENCE  기본 receipt2vec-auth / receipt2vec
"""
import asyncio
import contextvars
import json
import logging
import os
import time
from typing import Any, Dict, Optional

import httpx
import jwt
from fastapi import HTTPException
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

logger = logging.getLogger(__name__)


def _env(name: str, default: str) -> str:
    v = os.getenv(name)
    return v if v not in (None, "") else default


AUTH_REQUIRED = _env("AUTH_REQUIRED", "true").lower() in ("1", "true", "yes", "on")
JWKS_URL = _env("AUTH_JWKS_URL", "http://auth-server:3003/.well-known/jwks.json")
ISSUER = _env("AUTH_ISSUER", "receipt2vec-auth")
AUDIENCE = _env("AUTH_AUDIENCE", "receipt2vec")

# 인증 없이 허용하는 경로 (헬스체크·문서). 나머지 /api/* 는 전부 토큰 필요
PUBLIC_PREFIXES = ("/api/health",)

_claims_var: contextvars.ContextVar[Optional[Dict[str, Any]]] = contextvars.ContextVar("auth_claims", default=None)
_token_var: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("auth_token", default=None)


class AuthError(Exception):
    def __init__(self, code: str, message: str, status: int = 401):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


# ============================================================================ JWKS (공개키) 캐시
class JwksCache:
    """kid → 공개키. 10분마다 갱신, 모르는 kid(키 교체)가 오면 즉시 다시 받음 (단, 30초에 한 번까지)"""

    def __init__(self, url: str, ttl: float = 600, min_refetch: float = 30):
        self.url, self.ttl, self.min_refetch = url, ttl, min_refetch
        self._keys: Dict[str, Any] = {}
        self._fetched = 0.0
        self._lock = asyncio.Lock()

    def set_keys(self, jwks: Dict[str, Any]) -> None:
        self._keys = {k["kid"]: jwt.algorithms.RSAAlgorithm.from_jwk(json.dumps(k))
                      for k in jwks.get("keys", []) if k.get("kid") and k.get("kty") == "RSA"}
        self._fetched = time.time()

    async def _fetch(self) -> None:
        async with httpx.AsyncClient(timeout=5.0) as c:
            res = await c.get(self.url)
            res.raise_for_status()
            self.set_keys(res.json())
        logger.info(f"JWKS 갱신 url={self.url} keys={list(self._keys)}")

    async def get(self, kid: Optional[str]) -> Any:
        stale = time.time() - self._fetched > self.ttl
        if (kid not in self._keys or stale) and time.time() - self._fetched > self.min_refetch:
            async with self._lock:
                if (kid not in self._keys or time.time() - self._fetched > self.ttl) \
                        and time.time() - self._fetched > self.min_refetch:
                    try:
                        await self._fetch()
                    except Exception as e:  # noqa: BLE001
                        if not self._keys:
                            raise AuthError("auth_unavailable", f"인증 서버 공개키를 가져오지 못했어요 ({type(e).__name__})", 503)
                        logger.warning(f"JWKS 갱신 실패 → 기존 키 사용 error={type(e).__name__}: {e}")
        key = self._keys.get(kid)
        if key is None:
            raise AuthError("invalid_token", "알 수 없는 서명 키")
        return key


jwks_cache = JwksCache(JWKS_URL)


async def verify_token(token: str) -> Dict[str, Any]:
    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError:
        raise AuthError("invalid_token", "토큰 형식이 올바르지 않아요")
    if header.get("alg") != "RS256":
        raise AuthError("invalid_token", "허용하지 않는 서명 방식")
    key = await jwks_cache.get(header.get("kid"))
    try:
        claims = jwt.decode(token, key, algorithms=["RS256"], audience=AUDIENCE, issuer=ISSUER,
                            options={"require": ["exp", "iat", "sub"]}, leeway=10)
    except jwt.ExpiredSignatureError:
        raise AuthError("token_expired", "로그인이 만료되었어요")
    except jwt.PyJWTError as e:
        raise AuthError("invalid_token", f"유효하지 않은 토큰 ({type(e).__name__})")
    if claims.get("typ") != "access":
        raise AuthError("invalid_token", "access token 이 아니에요")
    return claims


def _bearer(request: Request) -> Optional[str]:
    h = request.headers.get("authorization") or ""
    return h[7:].strip() if h.lower().startswith("bearer ") else None


class AuthMiddleware(BaseHTTPMiddleware):
    """/api/* 요청의 토큰 검증 → 요청 처리 동안 current_user() 로 조회 가능"""

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if not AUTH_REQUIRED or request.method == "OPTIONS" or not path.startswith("/api/") \
                or path.startswith(PUBLIC_PREFIXES):
            return await call_next(request)
        token = _bearer(request)
        try:
            if not token:
                raise AuthError("unauthorized", "로그인이 필요해요 (Authorization: Bearer 토큰 없음)")
            claims = await verify_token(token)
        except AuthError as e:
            logger.warning(f"인증 거부 path={path} code={e.code} reason={e.message}")
            return JSONResponse({"detail": {"code": e.code, "message": e.message}}, status_code=e.status,
                                headers={"WWW-Authenticate": "Bearer"} if e.status == 401 else None)
        t1, t2 = _claims_var.set(claims), _token_var.set(token)
        try:
            return await call_next(request)
        finally:
            _claims_var.reset(t1)
            _token_var.reset(t2)


# ============================================================================ 엔드포인트에서 쓰는 함수
def current_user() -> Optional[Dict[str, Any]]:
    """현재 요청의 토큰 클레임 (인증 꺼짐이면 None)"""
    return _claims_var.get()


def resolve_user_id(requested: Optional[str]) -> str:
    """
    요청의 userId 를 토큰 주인으로 확정.
      - 토큰 sub 와 다르면 403 (다른 사용자 데이터 접근 차단)
      - 비어 있으면 sub 사용
      - 인증이 꺼져 있으면 요청값 그대로 (없으면 400)
    """
    claims = current_user()
    if claims is None:
        if AUTH_REQUIRED:
            raise HTTPException(401, {"code": "unauthorized", "message": "로그인이 필요해요"})
        if not requested:
            raise HTTPException(400, "userId 가 필요해요")
        return requested
    sub = claims["sub"]
    if requested and requested != sub:
        logger.warning(f"다른 사용자 데이터 접근 차단 token_user={sub} requested={requested}")
        raise HTTPException(403, {"code": "forbidden", "message": "다른 사용자의 데이터에는 접근할 수 없어요"})
    return sub


def require_admin() -> Dict[str, Any]:
    """관리 API (인덱스 생성·삭제·재보강 등)는 ADMIN 만"""
    claims = current_user()
    if claims is None and not AUTH_REQUIRED:
        return {}
    if not claims or claims.get("role") != "ADMIN":
        raise HTTPException(403, {"code": "forbidden", "message": "관리자만 사용할 수 있어요"})
    return claims


def forward_auth_headers() -> Dict[str, str]:
    """서비스 간 호출에 사용자 토큰을 그대로 전달 (ocr → rag)"""
    token = _token_var.get()
    return {"Authorization": f"Bearer {token}"} if token else {}
