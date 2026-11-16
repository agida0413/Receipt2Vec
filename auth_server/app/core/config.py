"""
인증 서버 설정 (모두 환경변수, 비밀값은 auth_server/.env — git 제외)
"""
import os
from dataclasses import dataclass, field
from typing import Dict, Optional


def _env(name: str, default: Optional[str] = None) -> Optional[str]:
    v = os.getenv(name)
    return v if v not in (None, "") else default


def _int(name: str, default: int) -> int:
    return int(_env(name, str(default)))


def _bool(name: str, default: bool) -> bool:
    return (_env(name, str(default)) or "").lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class ProviderConfig:
    name: str                 # google / naver / kakao
    client_id: Optional[str]
    client_secret: Optional[str]

    @property
    def enabled(self) -> bool:
        # 카카오는 client_secret 이 선택 (콘솔에서 '사용' 으로 켠 경우만 필요)
        return bool(self.client_id and (self.client_secret or self.name == "kakao"))


@dataclass(frozen=True)
class Settings:
    # 사용자가 보는 주소 (프론트 nginx). OAuth 콜백 = {public_base_url}/login/oauth2/code/{provider}
    public_base_url: str = field(default_factory=lambda: _env("PUBLIC_BASE_URL", "http://localhost:3000").rstrip("/"))
    # 로그인 후 돌아갈 프론트 주소
    frontend_url: str = field(default_factory=lambda: _env("FRONTEND_URL", "http://localhost:3000").rstrip("/"))

    mysql_host: str = field(default_factory=lambda: _env("MYSQL_HOST", "localhost"))
    mysql_port: int = field(default_factory=lambda: _int("MYSQL_PORT", 3306))
    mysql_database: str = field(default_factory=lambda: _env("MYSQL_DATABASE", "receipt2vec_auth"))
    mysql_user: str = field(default_factory=lambda: _env("MYSQL_USER", "auth"))
    mysql_password: str = field(default_factory=lambda: _env("MYSQL_PASSWORD", ""))

    redis_host: str = field(default_factory=lambda: _env("REDIS_HOST", "localhost"))
    redis_port: int = field(default_factory=lambda: _int("REDIS_PORT", 6379))
    redis_db: int = field(default_factory=lambda: _int("REDIS_DB", 0))
    redis_password: Optional[str] = field(default_factory=lambda: _env("REDIS_PASSWORD"))

    # JWT: RS256 (다른 서비스는 /.well-known/jwks.json 공개키로 검증 → 비밀키 공유 불필요)
    jwt_issuer: str = field(default_factory=lambda: _env("JWT_ISSUER", "receipt2vec-auth"))
    jwt_audience: str = field(default_factory=lambda: _env("JWT_AUDIENCE", "receipt2vec"))
    jwt_key_path: str = field(default_factory=lambda: _env("JWT_KEY_PATH", "/data/keys/jwt_private.pem"))
    access_ttl_sec: int = field(default_factory=lambda: _int("ACCESS_TOKEN_TTL_SEC", 30 * 60))
    refresh_ttl_sec: int = field(default_factory=lambda: _int("REFRESH_TOKEN_TTL_SEC", 14 * 24 * 3600))
    state_ttl_sec: int = field(default_factory=lambda: _int("OAUTH_STATE_TTL_SEC", 10 * 60))

    # 리프레시 토큰 쿠키 (HttpOnly). 운영(https)에서는 COOKIE_SECURE=true
    cookie_name: str = field(default_factory=lambda: _env("REFRESH_COOKIE_NAME", "r2v_refresh"))
    cookie_secure: bool = field(default_factory=lambda: _bool("COOKIE_SECURE", False))

    providers: Dict[str, ProviderConfig] = field(default_factory=lambda: {
        "google": ProviderConfig("google", _env("GOOGLE_CLIENT_ID"), _env("GOOGLE_CLIENT_SECRET")),
        "naver": ProviderConfig("naver", _env("NAVER_CLIENT_ID"), _env("NAVER_CLIENT_SECRET")),
        "kakao": ProviderConfig("kakao", _env("KAKAO_CLIENT_ID"), _env("KAKAO_CLIENT_SECRET")),
    })

    def redirect_uri(self, provider: str) -> str:
        return f"{self.public_base_url}/login/oauth2/code/{provider}"


settings = Settings()
