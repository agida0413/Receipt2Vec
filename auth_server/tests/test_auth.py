"""
인증 서버 단위 테스트 (MySQL·Redis·외부 제공자 없이: 가짜 Redis + httpx MockTransport)
"""
import asyncio
import json
import sys
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
import jwt
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.api.routes import safe_redirect  # noqa: E402
from app.core.config import settings  # noqa: E402
from app.db.mysql import SCHEMA_PATH, schema_statements  # noqa: E402
from app.services import providers as prov  # noqa: E402
from app.services.tokens import TokenError, TokenService, load_or_create_key  # noqa: E402
from app.services.users import User  # noqa: E402


class FakeRedis:
    """테스트에 필요한 명령만 구현 (만료는 시각 비교)"""

    def __init__(self):
        self.d, self.exp = {}, {}

    def _alive(self, k):
        if k in self.exp and self.exp[k] <= time.time():
            self.d.pop(k, None)
            self.exp.pop(k, None)
        return k in self.d

    def set(self, k, v, ex=None):
        self.d[k] = v
        if ex:
            self.exp[k] = time.time() + ex
        return True

    def get(self, k):
        return self.d.get(k) if self._alive(k) else None

    def getdel(self, k):
        v = self.get(k)
        self.d.pop(k, None)
        return v

    def exists(self, k):
        return int(self._alive(k))

    def delete(self, *ks):
        for k in ks:
            self.d.pop(k, None)

    def ttl(self, k):
        return int(self.exp[k] - time.time()) if self._alive(k) and k in self.exp else -1

    def expire(self, k, s):
        self.exp[k] = time.time() + s

    def sadd(self, k, v):
        self.d.setdefault(k, set()).add(v)

    def srem(self, k, v):
        self.d.get(k, set()).discard(v)

    def smembers(self, k):
        return set(self.d.get(k, set()))

    def pipeline(self):
        return self

    def execute(self):
        return []


@pytest.fixture(scope="module")
def key(tmp_path_factory):
    return load_or_create_key(str(tmp_path_factory.mktemp("k") / "jwt.pem"))


@pytest.fixture
def svc(key):
    return TokenService(FakeRedis(), key)


USER = User(id=1, user_uid="11111111-2222-3333-4444-555555555555", email="a@b.com", name="홍길동",
            profile_image_url=None, role="USER", status="ACTIVE", providers=["google"])


# ---------------------------------------------------------------------------- 제공자
def test_normalize_profiles():
    g = prov.normalize("google", {"sub": "g1", "email": "a@b.com", "email_verified": True, "name": "A", "picture": "p"})
    assert (g.provider_user_id, g.email_verified, g.profile_image_url) == ("g1", True, "p")
    n = prov.normalize("naver", {"resultcode": "00", "response": {"id": "n1", "email": "x@naver.com", "nickname": "닉"}})
    assert (n.provider_user_id, n.name, n.email_verified) == ("n1", "닉", True)
    k = prov.normalize("kakao", {"id": 123, "kakao_account": {"email": "k@kakao.com", "is_email_valid": True,
                                                               "is_email_verified": False,
                                                               "profile": {"nickname": "카카오"}}})
    assert (k.provider_user_id, k.name, k.email_verified) == ("123", "카카오", False)   # 미인증 이메일은 연결 안 함
    with pytest.raises(prov.OAuthError):
        prov.normalize("naver", {"resultcode": "024", "message": "Authentication failed"})


def test_authorize_urls():
    g = parse_qs(urlparse(prov.authorize_url("google", "S", "CH")).query)
    assert g["redirect_uri"] == ["http://localhost:3000/login/oauth2/code/google"]
    assert g["code_challenge_method"] == ["S256"] and g["scope"] == ["openid email profile"] and g["state"] == ["S"]
    k = parse_qs(urlparse(prov.authorize_url("kakao", "S", None)).query)
    assert "scope" not in k and k["redirect_uri"] == ["http://localhost:3000/login/oauth2/code/kakao"]


def test_exchange_code_sends_expected_fields_and_hides_errors():
    seen = {}

    def handler(req: httpx.Request):
        seen.update(parse_qs(req.content.decode()))
        if seen.get("code") == ["bad"]:
            return httpx.Response(400, json={"error": "invalid_grant", "error_description": "bad code"})
        return httpx.Response(200, json={"access_token": "AT", "token_type": "bearer"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    assert asyncio.run(prov.exchange_code("naver", "c1", "ST", None, client)) == "AT"
    assert seen["state"] == ["ST"] and seen["grant_type"] == ["authorization_code"]
    with pytest.raises(prov.OAuthError) as e:
        asyncio.run(prov.exchange_code("naver", "bad", "ST", None, client))
    assert e.value.code == "token_exchange_failed" and "bad code" in e.value.detail


def test_safe_redirect_blocks_open_redirect():
    assert safe_redirect("/chat?x=1") == "/chat?x=1"
    for bad in ("//evil.com", "https://evil.com", "/\\evil.com", "/a//b", None, "", "javascript:alert(1)"):
        assert safe_redirect(bad) == "/"


# ---------------------------------------------------------------------------- state / 토큰
def test_state_is_one_time_and_bound_to_provider(svc):
    s = svc.create_state("google", "/", "verifier")
    with pytest.raises(TokenError):
        svc.consume_state(s, "naver")             # 다른 제공자 → 거부 (그리고 소모됨)
    s = svc.create_state("google", "/", "verifier")
    assert svc.consume_state(s, "google")["verifier"] == "verifier"
    with pytest.raises(TokenError):
        svc.consume_state(s, "google")            # 재사용 불가


def test_access_token_verify_blacklist_and_jwks(svc, key):
    sid, _ = svc.start_session(USER, "google", "1.2.3.4", "ua")
    token, claims = svc.issue_access(USER, sid, "google")
    assert svc.verify_access(token)["sub"] == USER.user_uid
    # 다른 서비스: JWKS 공개키만으로 검증 가능
    pub = jwt.algorithms.RSAAlgorithm.from_jwk(json.dumps(key.public_jwk))
    assert jwt.decode(token, pub, algorithms=["RS256"], audience=settings.jwt_audience)["sid"] == sid
    assert jwt.get_unverified_header(token)["kid"] == key.public_jwk["kid"]
    svc.blacklist(claims)
    with pytest.raises(TokenError) as e:
        svc.verify_access(token)
    assert e.value.code == "token_revoked"
    with pytest.raises(TokenError):
        svc.verify_access(token[:-4] + "AAAA")    # 서명 위조


def test_refresh_rotation_and_reuse_detection(svc):
    sid, rt1 = svc.start_session(USER, "kakao", None, None)
    uid, sid2, rt2 = svc.rotate(rt1)
    assert (uid, sid2) == (USER.user_uid, sid) and rt2 != rt1
    with pytest.raises(TokenError) as e:
        svc.rotate(rt1)                           # 교체 직후 → 다른 탭의 동시 갱신으로 보고 세션 유지
    assert e.value.code == "refresh_race"
    used_key = next(k for k in svc.r.d if k.startswith("auth:rt_used:"))
    sid_, _, at = svc.r.d[used_key].partition("|")
    svc.r.d[used_key] = f"{sid_}|{int(at) - 60}"  # 1분 전에 교체된 것으로
    with pytest.raises(TokenError) as e:
        svc.rotate(rt1)                           # 오래전에 교체된 토큰 재사용 → 탈취 의심
    assert e.value.code == "refresh_reused"
    with pytest.raises(TokenError):
        svc.rotate(rt2)                           # 세션이 끊겨 최신 토큰도 무효
    token, _ = svc.issue_access(USER, sid, "kakao")
    with pytest.raises(TokenError):
        svc.verify_access(token)                  # 종료된 세션의 access token 도 무효


def test_logout_and_logout_all(svc):
    sid_a, rt_a = svc.start_session(USER, "google", None, None)
    sid_b, rt_b = svc.start_session(USER, "naver", None, None)
    assert svc.revoke_refresh(rt_a) == sid_a
    with pytest.raises(TokenError):
        svc.rotate(rt_a)
    token_b, _ = svc.issue_access(USER, sid_b, "naver")
    assert svc.revoke_all(USER.user_uid) == 1
    with pytest.raises(TokenError):
        svc.verify_access(token_b)
    with pytest.raises(TokenError):
        svc.rotate(rt_b)


def test_key_persists_across_restart(tmp_path):
    a = load_or_create_key(str(tmp_path / "k.pem"))
    b = load_or_create_key(str(tmp_path / "k.pem"))
    assert a.kid == b.kid


def test_schema_has_oauth_tables():
    sql = "\n".join(schema_statements(SCHEMA_PATH.read_text(encoding="utf-8")))
    for table in ("users", "user_social_accounts", "oauth_providers", "login_history"):
        assert f"CREATE TABLE IF NOT EXISTS {table}" in sql
    assert "uk_social_provider_user (provider, provider_user_id)" in sql
