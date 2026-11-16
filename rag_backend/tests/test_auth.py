"""
API 인증 미들웨어 테스트 (auth-server 없이: 테스트용 RSA 키로 토큰을 만들고 JWKS 캐시에 직접 넣음)
"""
import json
import sys
import time
from pathlib import Path
from typing import Optional

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI, Query
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core import auth  # noqa: E402

KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
JWK = {**json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(KEY.public_key())), "kid": "k1"}
auth.jwks_cache.set_keys({"keys": [JWK]})
auth.jwks_cache.min_refetch = 10 ** 9   # 테스트 중 원격 조회 금지


def token(sub="u1", key=KEY, kid="k1", **over):
    now = int(time.time())
    claims = {"iss": auth.ISSUER, "aud": auth.AUDIENCE, "sub": sub, "iat": now, "exp": now + 600,
              "jti": "j", "typ": "access", "role": "USER"} | over
    return jwt.encode(claims, key, algorithm="RS256", headers={"kid": kid})


app = FastAPI()
app.add_middleware(auth.AuthMiddleware)


@app.get("/api/health")
def health():
    return {"ok": True}


@app.get("/api/items")
def items(userId: Optional[str] = Query(None)):
    return {"user": auth.resolve_user_id(userId)}


@app.post("/api/admin")
def admin():
    auth.require_admin()
    return {"ok": True}


@app.get("/api/forward")
def forward():
    return auth.forward_auth_headers()


client = TestClient(app)
H = lambda t: {"Authorization": f"Bearer {t}"}  # noqa: E731


def test_public_and_missing_token():
    assert client.get("/api/health").status_code == 200
    r = client.get("/api/items")
    assert r.status_code == 401 and r.json()["detail"]["code"] == "unauthorized"


def test_valid_token_binds_user():
    assert client.get("/api/items", headers=H(token())).json() == {"user": "u1"}          # userId 생략 → 토큰 주인
    assert client.get("/api/items?userId=u1", headers=H(token())).status_code == 200
    r = client.get("/api/items?userId=someone-else", headers=H(token()))
    assert r.status_code == 403                                                          # 남의 데이터 차단


def test_rejects_bad_tokens():
    now = int(time.time())
    cases = {
        "token_expired": token(exp=now - 60, iat=now - 700),
        "invalid_token": token(key=OTHER_KEY),             # 다른 키로 서명 (위조)
    }
    for code, t in cases.items():
        r = client.get("/api/items", headers=H(t))
        assert r.status_code == 401 and r.json()["detail"]["code"] == code, (code, r.json())
    for bad in (token(aud="other"), token(iss="evil"), token(typ="refresh"), "not-a-jwt", token(kid="unknown")):
        assert client.get("/api/items", headers=H(bad)).status_code == 401
    hs = jwt.encode({"sub": "u1", "exp": now + 60, "iat": now}, "secret", algorithm="HS256", headers={"kid": "k1"})
    assert client.get("/api/items", headers=H(hs)).status_code == 401                   # 알고리즘 바꿔치기 차단


def test_admin_and_forwarding():
    assert client.post("/api/admin", headers=H(token())).status_code == 403
    assert client.post("/api/admin", headers=H(token(role="ADMIN"))).status_code == 200
    t = token()
    assert client.get("/api/forward", headers=H(t)).json() == {"Authorization": f"Bearer {t}"}
