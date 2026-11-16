"""
사용자·소셜 계정 저장소 (MySQL)

로그인 시 계정 연결 규칙
  1) (provider, provider_user_id) 로 이미 연결된 계정이 있으면 그 사용자
  2) 제공자가 '인증된 이메일' 을 줬고 같은 이메일의 사용자가 있으면 그 사용자에 새 소셜 계정을 연결
     (인증되지 않은 이메일로 연결하면 남의 이메일로 가입해 계정을 가로챌 수 있으므로 연결하지 않음)
  3) 없으면 새 사용자 생성
"""
import json
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from sqlalchemy import text

from app.core.config import settings
from app.db.mysql import engine

logger = logging.getLogger(__name__)


@dataclass
class SocialProfile:
    """제공자별 응답을 같은 모양으로 정규화한 프로필"""
    provider: str
    provider_user_id: str
    email: Optional[str] = None
    email_verified: bool = False
    name: Optional[str] = None
    profile_image_url: Optional[str] = None
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass
class User:
    id: int
    user_uid: str
    email: Optional[str]
    name: Optional[str]
    profile_image_url: Optional[str]
    role: str
    status: str
    providers: List[str] = field(default_factory=list)

    def public(self) -> Dict[str, Any]:
        return {"id": self.user_uid, "email": self.email, "name": self.name,
                "profileImageUrl": self.profile_image_url, "role": self.role, "providers": self.providers}


_USER_COLS = "id, user_uid, email, name, profile_image_url, role, status"


def _user(row, providers: Optional[List[str]] = None) -> User:
    return User(id=row.id, user_uid=row.user_uid, email=row.email, name=row.name,
                profile_image_url=row.profile_image_url, role=row.role, status=row.status,
                providers=providers or [])


def _providers(conn, user_id: int) -> List[str]:
    rows = conn.execute(text("SELECT provider FROM user_social_accounts WHERE user_id = :u ORDER BY connected_at"),
                        {"u": user_id})
    return [r.provider for r in rows]


def login_with_social(p: SocialProfile) -> User:
    """소셜 프로필로 사용자 찾기/연결/생성 (한 트랜잭션)"""
    raw = json.dumps(p.raw, ensure_ascii=False)[:60000]
    with engine().begin() as conn:
        acc = conn.execute(text(
            "SELECT id, user_id FROM user_social_accounts WHERE provider = :p AND provider_user_id = :pid FOR UPDATE"),
            {"p": p.provider, "pid": p.provider_user_id}).first()
        how = "existing"
        if acc:
            user_id = acc.user_id
            conn.execute(text(
                "UPDATE user_social_accounts SET email = :e, email_verified = :v, name = :n, profile_image_url = :img, "
                "raw_profile = :raw, last_login_at = CURRENT_TIMESTAMP(3) WHERE id = :id"),
                {"e": p.email, "v": p.email_verified, "n": p.name, "img": p.profile_image_url, "raw": raw, "id": acc.id})
        else:
            row = None
            if p.email and p.email_verified:
                row = conn.execute(text("SELECT id FROM users WHERE email = :e AND status = 'ACTIVE' "
                                        "ORDER BY id LIMIT 1 FOR UPDATE"), {"e": p.email}).first()
            if row:
                user_id, how = row.id, "linked_by_email"
            else:
                res = conn.execute(text(
                    "INSERT INTO users (user_uid, email, name, profile_image_url) VALUES (:uid, :e, :n, :img)"),
                    {"uid": str(uuid.uuid4()), "e": p.email, "n": p.name, "img": p.profile_image_url})
                user_id, how = res.lastrowid, "created"
            conn.execute(text(
                "INSERT INTO user_social_accounts (user_id, provider, provider_user_id, email, email_verified, name, "
                "profile_image_url, raw_profile, last_login_at) "
                "VALUES (:u, :p, :pid, :e, :v, :n, :img, :raw, CURRENT_TIMESTAMP(3))"),
                {"u": user_id, "p": p.provider, "pid": p.provider_user_id, "e": p.email, "v": p.email_verified,
                 "n": p.name, "img": p.profile_image_url, "raw": raw})
        # 비어 있는 프로필 항목만 소셜 정보로 채움 (사용자가 바꾼 값은 덮어쓰지 않음)
        conn.execute(text(
            "UPDATE users SET email = COALESCE(email, :e), name = COALESCE(name, :n), "
            "profile_image_url = COALESCE(profile_image_url, :img), last_login_at = CURRENT_TIMESTAMP(3) "
            "WHERE id = :u"), {"e": p.email, "n": p.name, "img": p.profile_image_url, "u": user_id})
        row = conn.execute(text(f"SELECT {_USER_COLS} FROM users WHERE id = :u"), {"u": user_id}).first()
        user = _user(row, _providers(conn, user_id))
    logger.info(f"소셜 로그인 사용자 처리 provider={p.provider} result={how} user={user.user_uid} "
                f"providers={user.providers} email_verified={p.email_verified}")
    return user


def get_user(user_uid: str) -> Optional[User]:
    with engine().connect() as conn:
        row = conn.execute(text(f"SELECT {_USER_COLS} FROM users WHERE user_uid = :uid"), {"uid": user_uid}).first()
        return _user(row, _providers(conn, row.id)) if row else None


def record_login(provider: str, success: bool, user: Optional[User] = None, provider_user_id: Optional[str] = None,
                 reason: Optional[str] = None, ip: Optional[str] = None, user_agent: Optional[str] = None,
                 request_id: Optional[str] = None) -> None:
    """로그인 이력 (실패해도 로그인 흐름은 막지 않음)"""
    try:
        with engine().begin() as conn:
            conn.execute(text(
                "INSERT INTO login_history (user_id, provider, provider_user_id, success, failure_reason, ip, "
                "user_agent, request_id) VALUES (:u, :p, :pid, :s, :r, :ip, :ua, :rid)"),
                {"u": user.id if user else None, "p": provider, "pid": provider_user_id, "s": success,
                 "r": (reason or "")[:255] or None, "ip": ip, "ua": (user_agent or "")[:500] or None, "rid": request_id})
    except Exception as e:  # noqa: BLE001
        logger.warning(f"로그인 이력 저장 실패 provider={provider} error={type(e).__name__}: {e}")


def sync_providers(scopes: Dict[str, str]) -> None:
    """oauth_providers 테이블에 현재 설정 반영 (client_secret 은 저장하지 않음)"""
    with engine().begin() as conn:
        for name, cfg in settings.providers.items():
            conn.execute(text(
                "INSERT INTO oauth_providers (provider, client_id, redirect_uri, scopes, enabled) "
                "VALUES (:p, :cid, :uri, :sc, :en) ON DUPLICATE KEY UPDATE client_id = VALUES(client_id), "
                "redirect_uri = VALUES(redirect_uri), scopes = VALUES(scopes), enabled = VALUES(enabled)"),
                {"p": name, "cid": cfg.client_id, "uri": settings.redirect_uri(name), "sc": scopes.get(name),
                 "en": cfg.enabled})
    logger.info("OAuth 제공자 설정: " + ", ".join(
        f"{n}={'사용' if c.enabled else '미설정'}" for n, c in settings.providers.items()))
