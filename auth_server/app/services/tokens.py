"""
JWT 발급·검증 + Redis 토큰 상태 관리

Access token (JWT, RS256, 기본 30분)
  - 다른 서비스는 /.well-known/jwks.json 공개키로 서명만 확인하면 됨 (무상태)
  - 로그아웃한 토큰은 만료 전까지 Redis 블랙리스트(auth:bl:{jti})로 막고, /api/auth/introspect 가 이를 반영
Refresh token (불투명 랜덤 문자열, 기본 14일, HttpOnly 쿠키)
  - Redis 에는 SHA-256 해시만 저장 (Redis 가 유출돼도 토큰 원문은 없음)
  - 쓸 때마다 새 토큰으로 교체(rotation). 이미 교체된 토큰이 다시 오면 탈취로 보고 그 세션 전체를 끊음

Redis 키
  oauth:state:{state}        로그인 시작 시 만든 state (+ PKCE verifier, 돌아갈 경로) — 1회용, 10분
  auth:rt:{hash}             유효한 refresh token → {sid, uid}
  auth:rt_used:{hash}        이미 교체된 refresh token → sid (재사용 탐지)
  auth:sess:{sid}            로그인 세션 {uid, provider, rt(현재 토큰 해시), ip, ua, createdAt}
  auth:user_sess:{uid}       사용자의 세션 목록 (SET)
  auth:bl:{jti}              로그아웃된 access token (남은 유효시간만큼)
  auth:revoked_before:{uid}  '모든 기기 로그아웃' 시각 — 그 전에 발급된 access token 무효
"""
import base64
import hashlib
import json
import logging
import os
import secrets
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app.core.config import settings

logger = logging.getLogger(__name__)


REUSE_GRACE_SEC = 15  # 교체 직후 같은 토큰이 다시 오면 동시 요청으로 봄 (탈취 판정 제외)


class TokenError(Exception):
    def __init__(self, code: str, detail: str = ""):
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


# ============================================================================ 서명 키
@dataclass
class SigningKey:
    private_key: Any
    kid: str

    @property
    def public_jwk(self) -> Dict[str, Any]:
        jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(self.private_key.public_key()))
        return {**jwk, "kid": self.kid, "use": "sig", "alg": "RS256"}


def load_or_create_key(path: str) -> SigningKey:
    """RSA 키를 파일(도커 볼륨)에서 읽고, 없으면 만든다 → 재시작해도 기존 토큰이 계속 유효"""
    p = Path(path)
    if p.exists():
        key = serialization.load_pem_private_key(p.read_bytes(), password=None)
        logger.info(f"JWT 서명 키 로드 path={path}")
    else:
        p.parent.mkdir(parents=True, exist_ok=True)
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption())
        p.write_bytes(pem)
        os.chmod(p, 0o600)
        logger.info(f"JWT 서명 키 새로 생성 path={path}")
    der = key.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    kid = base64.urlsafe_b64encode(hashlib.sha256(der).digest()[:12]).decode().rstrip("=")
    return SigningKey(key, kid)


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def pkce_pair() -> Tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    return verifier, challenge


# ============================================================================ 토큰 서비스
class TokenService:
    def __init__(self, redis, key: SigningKey):
        self.r = redis
        self.key = key
        self.s = settings

    # ---------------------------------------------------------------- OAuth state (CSRF 방지, 1회용)
    def create_state(self, provider: str, redirect: str, verifier: Optional[str]) -> str:
        state = secrets.token_urlsafe(32)
        self.r.set(f"oauth:state:{state}", json.dumps({"provider": provider, "redirect": redirect,
                                                       "verifier": verifier, "at": int(time.time())}),
                   ex=self.s.state_ttl_sec)
        return state

    def consume_state(self, state: Optional[str], provider: str) -> Dict[str, Any]:
        if not state:
            raise TokenError("invalid_state", "state 없음")
        raw = self.r.getdel(f"oauth:state:{state}")   # 원자적으로 꺼내고 삭제 → 재사용 불가
        if not raw:
            raise TokenError("invalid_state", "만료되었거나 이미 사용한 state")
        data = json.loads(raw)
        if data.get("provider") != provider:
            raise TokenError("invalid_state", "다른 제공자의 state")
        return data

    # ---------------------------------------------------------------- access token
    def issue_access(self, user, sid: str, provider: Optional[str] = None) -> Tuple[str, Dict[str, Any]]:
        now = int(time.time())
        claims = {
            "iss": self.s.jwt_issuer, "aud": self.s.jwt_audience, "sub": user.user_uid,
            "iat": now, "nbf": now, "exp": now + self.s.access_ttl_sec, "jti": uuid.uuid4().hex,
            "typ": "access", "sid": sid, "role": user.role, "name": user.name, "email": user.email,
            "provider": provider,
        }
        token = jwt.encode(claims, self.key.private_key, algorithm="RS256", headers={"kid": self.key.kid})
        return token, claims

    def verify_access(self, token: str) -> Dict[str, Any]:
        """서명·만료·발급자 + Redis 블랙리스트/전체 로그아웃 확인"""
        try:
            claims = jwt.decode(token, self.key.private_key.public_key(), algorithms=["RS256"],
                                audience=self.s.jwt_audience, issuer=self.s.jwt_issuer,
                                options={"require": ["exp", "iat", "sub", "jti"]})
        except jwt.ExpiredSignatureError:
            raise TokenError("token_expired", "access token 만료")
        except jwt.PyJWTError as e:
            raise TokenError("invalid_token", type(e).__name__)
        if claims.get("typ") != "access":
            raise TokenError("invalid_token", "access token 아님")
        if self.r.exists(f"auth:bl:{claims['jti']}"):
            raise TokenError("token_revoked", "로그아웃된 토큰")
        before = self.r.get(f"auth:revoked_before:{claims['sub']}")
        if before and claims["iat"] < int(before):
            raise TokenError("token_revoked", "모든 기기에서 로그아웃됨")
        if claims.get("sid") and not self.r.exists(f"auth:sess:{claims['sid']}"):
            raise TokenError("token_revoked", "종료된 세션")
        return claims

    def blacklist(self, claims: Dict[str, Any]) -> None:
        ttl = int(claims.get("exp", 0)) - int(time.time())
        if ttl > 0:
            self.r.set(f"auth:bl:{claims['jti']}", "1", ex=ttl)

    # ---------------------------------------------------------------- refresh token / 세션
    def start_session(self, user, provider: str, ip: Optional[str], ua: Optional[str]) -> Tuple[str, str]:
        """로그인 성공 → (sid, refresh token)"""
        sid = uuid.uuid4().hex
        token = secrets.token_urlsafe(48)
        ttl = self.s.refresh_ttl_sec
        p = self.r.pipeline()
        p.set(f"auth:sess:{sid}", json.dumps({"uid": user.user_uid, "provider": provider, "rt": _hash(token),
                                              "ip": ip, "ua": (ua or "")[:200], "createdAt": int(time.time())}), ex=ttl)
        p.set(f"auth:rt:{_hash(token)}", json.dumps({"sid": sid, "uid": user.user_uid}), ex=ttl)
        p.sadd(f"auth:user_sess:{user.user_uid}", sid)
        p.expire(f"auth:user_sess:{user.user_uid}", ttl)
        p.execute()
        return sid, token

    def rotate(self, token: Optional[str]) -> Tuple[str, str, str]:
        """refresh token → (uid, sid, 새 refresh token). 이미 쓴 토큰이면 세션을 끊고 거부"""
        if not token:
            raise TokenError("no_refresh_token", "로그인이 필요해요")
        h = _hash(token)
        raw = self.r.getdel(f"auth:rt:{h}")
        if not raw:
            used = self.r.get(f"auth:rt_used:{h}")
            if used:
                sid, _, at = used.partition("|")
                if at and time.time() - int(at) < REUSE_GRACE_SEC:
                    # 탭 여러 개가 동시에 갱신한 경우 (다른 탭이 방금 교체) → 세션은 유지, 새 쿠키로 다시 시도하게 함
                    raise TokenError("refresh_race", "다른 탭에서 방금 갱신됨 — 다시 시도")
                logger.warning(f"리프레시 토큰 재사용 감지 → 세션 종료 sid={sid}")
                self.revoke_session(sid)
                raise TokenError("refresh_reused", "이미 사용된 토큰 (보안을 위해 다시 로그인해 주세요)")
            raise TokenError("invalid_refresh_token", "만료되었거나 로그아웃된 토큰")
        data = json.loads(raw)
        sid, uid = data["sid"], data["uid"]
        sess_raw = self.r.get(f"auth:sess:{sid}")
        if not sess_raw:
            raise TokenError("invalid_refresh_token", "종료된 세션")
        sess = json.loads(sess_raw)
        new = secrets.token_urlsafe(48)
        ttl = max(1, self.r.ttl(f"auth:sess:{sid}"))   # 세션 만료는 연장하지 않음 (최초 로그인 + 14일)
        sess["rt"] = _hash(new)
        p = self.r.pipeline()
        p.set(f"auth:rt_used:{h}", f"{sid}|{int(time.time())}", ex=ttl)
        p.set(f"auth:rt:{_hash(new)}", json.dumps({"sid": sid, "uid": uid}), ex=ttl)
        p.set(f"auth:sess:{sid}", json.dumps(sess), ex=ttl)
        p.execute()
        return uid, sid, new

    def session(self, sid: str) -> Optional[Dict[str, Any]]:
        raw = self.r.get(f"auth:sess:{sid}")
        return json.loads(raw) if raw else None

    def revoke_session(self, sid: str) -> None:
        sess = self.session(sid)
        p = self.r.pipeline()
        p.delete(f"auth:sess:{sid}")
        if sess:
            p.delete(f"auth:rt:{sess.get('rt')}")
            p.srem(f"auth:user_sess:{sess['uid']}", sid)
        p.execute()

    def revoke_refresh(self, token: Optional[str]) -> Optional[str]:
        """로그아웃: refresh token 이 가리키는 세션 종료 → sid"""
        if not token:
            return None
        raw = self.r.get(f"auth:rt:{_hash(token)}")
        if not raw:
            return None
        sid = json.loads(raw)["sid"]
        self.revoke_session(sid)
        return sid

    def revoke_all(self, uid: str) -> int:
        """모든 기기 로그아웃: 세션 전부 종료 + 지금까지 발급한 access token 무효"""
        sids = list(self.r.smembers(f"auth:user_sess:{uid}") or [])
        for sid in sids:
            self.revoke_session(sid)
        self.r.set(f"auth:revoked_before:{uid}", str(int(time.time()) + 1), ex=self.s.access_ttl_sec + 60)
        self.r.delete(f"auth:user_sess:{uid}")
        return len(sids)
