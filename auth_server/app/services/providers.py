"""
OAuth2 제공자 (Google / Naver / Kakao) — Authorization Code 흐름

  1) authorize_url : 제공자 로그인 화면 주소 (state = CSRF 방지, Google 은 PKCE 도 사용)
  2) exchange      : 콜백의 code → 제공자 access token (서버 간 통신, client_secret 은 서버에만 존재)
  3) profile       : 제공자 사용자 정보 → SocialProfile 로 정규화
제공자 access token 은 프로필 조회에만 쓰고 저장하지 않는다 (우리 서비스는 자체 JWT 를 발급).
"""
import logging
from dataclasses import dataclass
from typing import Any, Dict, Optional
from urllib.parse import urlencode

import httpx

from app.core.config import settings
from app.services.users import SocialProfile

logger = logging.getLogger(__name__)


class OAuthError(Exception):
    """제공자 통신 실패·거부 (사용자에게는 짧은 사유만 보여줌)"""

    def __init__(self, code: str, detail: str = ""):
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class ProviderSpec:
    authorize_url: str
    token_url: str
    profile_url: str
    scope: Optional[str]
    pkce: bool = False


SPECS: Dict[str, ProviderSpec] = {
    "google": ProviderSpec(
        "https://accounts.google.com/o/oauth2/v2/auth", "https://oauth2.googleapis.com/token",
        "https://openidconnect.googleapis.com/v1/userinfo", "openid email profile", pkce=True),
    "naver": ProviderSpec(
        "https://nid.naver.com/oauth2.0/authorize", "https://nid.naver.com/oauth2.0/token",
        "https://openapi.naver.com/v1/nid/me", None),
    # 카카오 동의 항목(닉네임·프로필 사진·이메일)은 카카오 개발자 콘솔에서 설정 → scope 를 보내지 않음
    # (설정하지 않은 항목을 scope 로 요청하면 KOE205 오류)
    "kakao": ProviderSpec(
        "https://kauth.kakao.com/oauth/authorize", "https://kauth.kakao.com/oauth/token",
        "https://kapi.kakao.com/v2/user/me", None),
}
SCOPES = {name: spec.scope or "(콘솔 동의항목)" for name, spec in SPECS.items()}


def authorize_url(provider: str, state: str, code_challenge: Optional[str]) -> str:
    cfg, spec = settings.providers[provider], SPECS[provider]
    params = {"response_type": "code", "client_id": cfg.client_id, "redirect_uri": settings.redirect_uri(provider),
              "state": state}
    if spec.scope:
        params["scope"] = spec.scope
    if spec.pkce and code_challenge:
        params.update(code_challenge=code_challenge, code_challenge_method="S256")
    if provider == "google":
        params["prompt"] = "select_account"   # 여러 구글 계정 중 선택
    return f"{spec.authorize_url}?{urlencode(params)}"


async def exchange_code(provider: str, code: str, state: str, code_verifier: Optional[str],
                        client: Optional[httpx.AsyncClient] = None) -> str:
    """콜백 code → 제공자 access token"""
    cfg, spec = settings.providers[provider], SPECS[provider]
    data = {"grant_type": "authorization_code", "client_id": cfg.client_id, "code": code,
            "redirect_uri": settings.redirect_uri(provider)}
    if cfg.client_secret:
        data["client_secret"] = cfg.client_secret
    if provider == "naver":
        data["state"] = state
    if spec.pkce and code_verifier:
        data["code_verifier"] = code_verifier
    async with _client(client) as c:
        res = await c.post(spec.token_url, data=data, headers={"Accept": "application/json"})
    body = _json(res)
    token = body.get("access_token")
    if res.status_code != 200 or not token:
        # 응답 본문에 토큰이 없을 때만 오류 사유를 남김 (토큰 값은 로그에 남기지 않음)
        reason = body.get("error_description") or body.get("error") or f"HTTP {res.status_code}"
        raise OAuthError("token_exchange_failed", f"{provider}: {reason}")
    return token


async def fetch_profile(provider: str, access_token: str, client: Optional[httpx.AsyncClient] = None) -> SocialProfile:
    async with _client(client) as c:
        res = await c.get(SPECS[provider].profile_url, headers={"Authorization": f"Bearer {access_token}"})
    body = _json(res)
    if res.status_code != 200:
        raise OAuthError("profile_failed", f"{provider}: HTTP {res.status_code} {str(body)[:200]}")
    return normalize(provider, body)


def normalize(provider: str, body: Dict[str, Any]) -> SocialProfile:
    """제공자별 응답 → SocialProfile"""
    if provider == "google":
        if not body.get("sub"):
            raise OAuthError("profile_failed", "google: sub 없음")
        return SocialProfile("google", str(body["sub"]), email=body.get("email"),
                             email_verified=bool(body.get("email_verified")), name=body.get("name"),
                             profile_image_url=body.get("picture"), raw=body)
    if provider == "naver":
        if body.get("resultcode") not in (None, "00"):
            raise OAuthError("profile_failed", f"naver: {body.get('message')}")
        r = body.get("response") or {}
        if not r.get("id"):
            raise OAuthError("profile_failed", "naver: id 없음")
        # 네이버 이메일은 네이버가 확인한 연락처 이메일 → 인증된 이메일로 취급
        return SocialProfile("naver", str(r["id"]), email=r.get("email"), email_verified=bool(r.get("email")),
                             name=r.get("name") or r.get("nickname"), profile_image_url=r.get("profile_image"), raw=r)
    if provider == "kakao":
        if not body.get("id"):
            raise OAuthError("profile_failed", "kakao: id 없음")
        acc = body.get("kakao_account") or {}
        prof = acc.get("profile") or {}
        props = body.get("properties") or {}
        verified = bool(acc.get("is_email_valid") and acc.get("is_email_verified"))
        return SocialProfile("kakao", str(body["id"]), email=acc.get("email"), email_verified=verified,
                             name=prof.get("nickname") or props.get("nickname"),
                             profile_image_url=prof.get("profile_image_url") or props.get("profile_image"), raw=body)
    raise OAuthError("unknown_provider", provider)


def _json(res: httpx.Response) -> Dict[str, Any]:
    try:
        body = res.json()
        return body if isinstance(body, dict) else {}
    except ValueError:
        return {}


class _client:
    """테스트에서 주입한 클라이언트는 그대로, 아니면 요청마다 새로 만들고 닫음"""

    def __init__(self, client: Optional[httpx.AsyncClient]):
        self._given = client
        self._own: Optional[httpx.AsyncClient] = None

    async def __aenter__(self) -> httpx.AsyncClient:
        if self._given is not None:
            return self._given
        self._own = httpx.AsyncClient(timeout=10.0)
        return self._own

    async def __aexit__(self, *exc):
        if self._own is not None:
            await self._own.aclose()
