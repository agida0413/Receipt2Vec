"""
인증 API

브라우저 흐름 (모두 http://localhost:3000 nginx 를 거침)
  [로그인 버튼] GET /oauth2/authorization/{provider}?redirect=/      → 302 제공자 로그인 화면
  [제공자 콜백] GET /login/oauth2/code/{provider}?code&state         → 302 프론트 (/?login=success)
                + refresh token 을 HttpOnly 쿠키로 설정 (access token 은 URL 에 싣지 않음)
  [프론트]      POST /api/auth/token/refresh (쿠키)                    → {accessToken, user}
                API 호출 시 Authorization: Bearer <accessToken>, 만료되면 다시 refresh

다른 서비스(MSA)
  GET  /.well-known/jwks.json   공개키 → JWT 서명을 직접 검증 (빠름, 무상태)
  POST /api/auth/introspect     토큰 상태 확인 (로그아웃·세션 종료까지 반영)
"""
import logging
import re
from typing import Optional
from urllib.parse import urlencode

from fastapi import APIRouter, Body, Header, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, RedirectResponse

from app.core.config import settings
from app.core.logging_setup import current_request_id
from app.services import providers as prov
from app.services import users
from app.services.tokens import TokenError, TokenService, pkce_pair

logger = logging.getLogger(__name__)
router = APIRouter()

_SAFE_REDIRECT = re.compile(r"^/(?![/\\])[\w\-./?=&%#~]*$")


def safe_redirect(path: Optional[str]) -> str:
    """로그인 후 돌아갈 경로는 같은 사이트의 상대 경로만 허용 (오픈 리다이렉트 방지)"""
    return path if path and _SAFE_REDIRECT.match(path) and "//" not in path else "/"


def _tokens(request: Request) -> TokenService:
    return request.app.state.tokens


def _client_ip(request: Request) -> Optional[str]:
    fwd = request.headers.get("x-forwarded-for")
    return fwd.split(",")[0].strip() if fwd else (request.client.host if request.client else None)


def _provider_or_404(provider: str) -> str:
    p = provider.lower()
    if p not in settings.providers:
        raise HTTPException(404, f"지원하지 않는 로그인 방식: {provider}")
    if not settings.providers[p].enabled:
        raise HTTPException(503, f"{p} 로그인이 설정되지 않았어요 (client id/secret 확인)")
    return p


def _front(path: str, **params) -> str:
    sep = "&" if "?" in path else "?"
    return f"{settings.frontend_url}{path}" + (sep + urlencode(params) if params else "")


def _set_refresh_cookie(resp, token: str) -> None:
    resp.set_cookie(settings.cookie_name, token, max_age=settings.refresh_ttl_sec, httponly=True,
                    secure=settings.cookie_secure, samesite="lax", path="/api/auth")


def _clear_refresh_cookie(resp) -> None:
    resp.delete_cookie(settings.cookie_name, path="/api/auth", httponly=True, secure=settings.cookie_secure,
                       samesite="lax")


def _bearer(authorization: Optional[str]) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "Authorization: Bearer <token> 이 필요해요", headers={"WWW-Authenticate": "Bearer"})
    return authorization[7:].strip()


def _check_origin(request: Request) -> None:
    """쿠키로 동작하는 POST 는 같은 사이트에서 온 요청만 (CSRF 방어, SameSite=Lax 와 이중)"""
    origin = request.headers.get("origin")
    if origin and origin.rstrip("/") not in (settings.frontend_url, settings.public_base_url):
        logger.warning(f"다른 출처의 쿠키 요청 거부 origin={origin}")
        raise HTTPException(403, "허용되지 않은 출처")


# ============================================================================ 로그인 흐름
@router.get("/api/auth/providers")
async def list_providers():
    return [{"provider": n, "enabled": c.enabled, "loginUrl": f"/oauth2/authorization/{n}"}
            for n, c in settings.providers.items()]


@router.get("/oauth2/authorization/{provider}")
async def start_login(provider: str, request: Request, redirect: Optional[str] = None):
    p = _provider_or_404(provider)
    verifier, challenge = pkce_pair() if prov.SPECS[p].pkce else (None, None)
    state = await run_in_threadpool(_tokens(request).create_state, p, safe_redirect(redirect), verifier)
    logger.info(f"소셜 로그인 시작 provider={p} redirect={safe_redirect(redirect)} ip={_client_ip(request)}")
    return RedirectResponse(prov.authorize_url(p, state, challenge), status_code=302)


@router.get("/login/oauth2/code/{provider}")
async def login_callback(provider: str, request: Request, code: Optional[str] = None, state: Optional[str] = None,
                         error: Optional[str] = None, error_description: Optional[str] = None):
    p = provider.lower()
    ip, ua, rid = _client_ip(request), request.headers.get("user-agent"), current_request_id()
    tokens = _tokens(request)
    profile = None
    try:
        if p not in settings.providers:
            raise prov.OAuthError("unknown_provider", p)
        if error:  # 사용자가 동의 취소 등
            raise prov.OAuthError("access_denied" if error in ("access_denied", "user_denied") else "provider_error",
                                  f"{error} {error_description or ''}".strip())
        if not code:
            raise prov.OAuthError("missing_code", "code 없음")
        st = await run_in_threadpool(tokens.consume_state, state, p)
        provider_token = await prov.exchange_code(p, code, state, st.get("verifier"))
        profile = await prov.fetch_profile(p, provider_token)
        user = await run_in_threadpool(users.login_with_social, profile)
        if user.status != "ACTIVE":
            raise prov.OAuthError("user_blocked", f"status={user.status}")
        sid, refresh = await run_in_threadpool(tokens.start_session, user, p, ip, ua)
    except (prov.OAuthError, TokenError) as e:
        level = logging.INFO if e.code == "access_denied" else logging.WARNING
        logger.log(level, f"소셜 로그인 실패 provider={p} code={e.code} detail={e.detail} ip={ip}")
        await run_in_threadpool(users.record_login, p, False, None, profile.provider_user_id if profile else None,
                                f"{e.code}: {e.detail}", ip, ua, rid)
        return RedirectResponse(_front("/", login_error=e.code), status_code=302)
    except Exception as e:  # noqa: BLE001  (DB·Redis 장애 등)
        logger.exception(f"소셜 로그인 처리 오류 provider={p} error={type(e).__name__}: {e}")
        return RedirectResponse(_front("/", login_error="server_error"), status_code=302)

    await run_in_threadpool(users.record_login, p, True, user, profile.provider_user_id, None, ip, ua, rid)
    logger.info(f"소셜 로그인 성공 provider={p} user={user.user_uid} sid={sid} ip={ip}")
    resp = RedirectResponse(_front(st.get("redirect") or "/", login="success"), status_code=302)
    _set_refresh_cookie(resp, refresh)
    return resp


# ============================================================================ 토큰
@router.post("/api/auth/token/refresh")
async def refresh_token(request: Request):
    _check_origin(request)
    tokens = _tokens(request)
    try:
        uid, sid, new_refresh = await run_in_threadpool(tokens.rotate, request.cookies.get(settings.cookie_name))
        user = await run_in_threadpool(users.get_user, uid)
        if not user or user.status != "ACTIVE":
            await run_in_threadpool(tokens.revoke_session, sid)
            raise TokenError("user_inactive", "사용할 수 없는 계정")
    except TokenError as e:
        if e.code == "refresh_race":  # 쿠키는 그대로 두고 재시도하게 함
            return JSONResponse({"detail": {"code": e.code, "message": e.detail}}, status_code=409)
        if e.code != "no_refresh_token":
            logger.info(f"토큰 갱신 거부 code={e.code} detail={e.detail}")
        resp = JSONResponse({"detail": {"code": e.code, "message": e.detail}}, status_code=401)
        _clear_refresh_cookie(resp)
        return resp
    sess = await run_in_threadpool(tokens.session, sid) or {}
    access, claims = tokens.issue_access(user, sid, sess.get("provider"))
    logger.info(f"토큰 갱신 user={uid} sid={sid}")
    resp = JSONResponse({"accessToken": access, "tokenType": "Bearer", "expiresIn": settings.access_ttl_sec,
                         "user": user.public()})
    _set_refresh_cookie(resp, new_refresh)
    return resp


@router.post("/api/auth/logout")
async def logout(request: Request, authorization: Optional[str] = Header(None)):
    _check_origin(request)
    tokens = _tokens(request)
    sid = await run_in_threadpool(tokens.revoke_refresh, request.cookies.get(settings.cookie_name))
    if authorization:
        try:
            claims = await run_in_threadpool(tokens.verify_access, _bearer(authorization))
            await run_in_threadpool(tokens.blacklist, claims)
            if not sid and claims.get("sid"):
                await run_in_threadpool(tokens.revoke_session, claims["sid"])
                sid = claims["sid"]
        except (TokenError, HTTPException):
            pass  # 이미 만료된 토큰이어도 로그아웃은 성공
    logger.info(f"로그아웃 sid={sid}")
    resp = JSONResponse({"loggedOut": True})
    _clear_refresh_cookie(resp)
    return resp


@router.post("/api/auth/logout-all")
async def logout_all(request: Request, authorization: Optional[str] = Header(None)):
    _check_origin(request)
    tokens = _tokens(request)
    claims = await _verified(tokens, authorization)
    n = await run_in_threadpool(tokens.revoke_all, claims["sub"])
    logger.info(f"모든 기기 로그아웃 user={claims['sub']} sessions={n}")
    resp = JSONResponse({"loggedOut": True, "sessions": n})
    _clear_refresh_cookie(resp)
    return resp


async def _verified(tokens: TokenService, authorization: Optional[str]):
    try:
        return await run_in_threadpool(tokens.verify_access, _bearer(authorization))
    except TokenError as e:
        raise HTTPException(401, {"code": e.code, "message": e.detail}, headers={"WWW-Authenticate": "Bearer"})


@router.get("/api/auth/me")
async def me(request: Request, authorization: Optional[str] = Header(None)):
    claims = await _verified(_tokens(request), authorization)
    user = await run_in_threadpool(users.get_user, claims["sub"])
    if not user:
        raise HTTPException(404, "사용자 없음")
    return {**user.public(), "loginProvider": claims.get("provider"), "tokenExpiresAt": claims["exp"]}


@router.post("/api/auth/introspect")
async def introspect(request: Request, token: str = Body(..., embed=True)):
    """RFC 7662 형식: 다른 서비스가 토큰 상태(로그아웃 여부 포함)를 확인"""
    try:
        claims = await run_in_threadpool(_tokens(request).verify_access, token)
    except TokenError as e:
        return {"active": False, "reason": e.code}
    return {"active": True, **{k: claims.get(k) for k in ("sub", "exp", "iat", "jti", "sid", "role", "name", "email",
                                                           "provider", "iss", "aud")}}


@router.get("/api/auth/verify")
async def verify_for_gateway(request: Request, authorization: Optional[str] = Header(None)):
    """
    gateway(nginx auth_request)가 API 요청마다 호출: 서명·만료 + 로그아웃·세션 종료(Redis)까지 확인.
    200 이면 통과(X-Auth-User/X-Auth-Role 헤더), 401 이면 gateway 가 요청을 백엔드로 보내지 않는다.
    """
    try:
        claims = await run_in_threadpool(_tokens(request).verify_access, _bearer(authorization))
    except HTTPException:
        return JSONResponse({"code": "unauthorized"}, status_code=401, headers={"X-Auth-Error": "unauthorized"})
    except TokenError as e:
        return JSONResponse({"code": e.code}, status_code=401, headers={"X-Auth-Error": e.code})
    return JSONResponse({"active": True}, headers={"X-Auth-User": claims["sub"], "X-Auth-Role": claims.get("role") or ""})


@router.get("/.well-known/jwks.json")
async def jwks(request: Request):
    return {"keys": [_tokens(request).key.public_jwk]}
