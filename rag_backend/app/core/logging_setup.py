"""
공통 로깅 설정 (rag_backend · ocr_backend 동일 파일)

형식 (Grafana/Loki 에서 검색하기 쉽게 한 줄 = 한 사건, 값은 key=value)
  2026-11-13 16:20:01.123 | INFO    | rid=a1b2c3d4 | app.services.chat.service | 질문 접수 user=eval q="이번 달 얼마 썼어?"

- rid(요청 ID): nginx → ocr_backend → rag_backend 까지 같은 값이 이어짐 (X-Request-ID 헤더)
  → Grafana 에서 rid 하나로 요청 전체 흐름을 볼 수 있음
- 요청마다 '요청 시작' / '요청 완료 status ms' 한 줄씩 (헬스체크는 DEBUG)
- 4xx 는 WARNING, 5xx 와 처리되지 않은 예외는 ERROR + 스택트레이스
- 시끄러운 라이브러리 로그(httpx, opensearch, urllib3 …)는 WARNING 이상만
"""
import contextvars
import logging
import logging.config
import os
import time
import uuid
from typing import Optional

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")

QUIET_LOGGERS = (
    "httpx", "httpx2", "httpcore", "urllib3", "opensearch", "google_genai", "google_genai.models", "google.auth",
    "sentence_transformers", "huggingface_hub", "filelock", "multipart", "PIL", "paddle", "paddlex", "ppocr",
)


class RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.rid = request_id_var.get()
        return True


def setup_logging(service: str) -> None:
    level = os.getenv("LOG_LEVEL", "INFO").upper()
    logging.config.dictConfig({
        "version": 1,
        "disable_existing_loggers": False,
        "filters": {"rid": {"()": RequestIdFilter}},
        "formatters": {
            "std": {
                "format": "%(asctime)s.%(msecs)03d | %(levelname)-7s | rid=%(rid)s | %(name)s | %(message)s",
                "datefmt": "%Y-%m-%d %H:%M:%S",
            },
        },
        "handlers": {
            "stdout": {"class": "logging.StreamHandler", "stream": "ext://sys.stdout",
                       "formatter": "std", "filters": ["rid"]},
        },
        "root": {"level": level, "handlers": ["stdout"]},
        "loggers": {
            # uvicorn 로그도 같은 형식으로 (접속 로그는 아래 미들웨어가 더 자세히 남기므로 끔)
            "uvicorn": {"level": "INFO", "handlers": [], "propagate": True},
            "uvicorn.error": {"level": "INFO", "handlers": [], "propagate": True},
            "uvicorn.access": {"level": "WARNING", "handlers": [], "propagate": False},
            **{name: {"level": "WARNING"} for name in QUIET_LOGGERS},
            # 중복 저장(409) 같은 정상 응답도 WARNING 으로 찍음 → 앱 로그가 대신 기록하므로 ERROR 만
            "opensearch": {"level": "ERROR"},
        },
    })
    logging.getLogger("app").info(f"로깅 설정 완료 service={service} level={level}")


_QUIET_PATHS = ("/", "/api/health", "/api/health/", "/favicon.ico")


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """요청마다 rid 부여 + 시작/완료 로그 (메서드, 경로, 쿼리, 사용자, 상태, 소요시간)"""

    def __init__(self, app, service: str):
        super().__init__(app)
        self.service = service
        self.log = logging.getLogger(f"app.http.{service}")

    async def dispatch(self, request: Request, call_next):
        rid = (request.headers.get("x-request-id") or uuid.uuid4().hex[:16])[:32]
        token = request_id_var.set(rid)
        started = time.perf_counter()
        path = request.url.path
        quiet = path in _QUIET_PATHS
        user = request.query_params.get("userId") or request.query_params.get("user_id")
        client = request.client.host if request.client else "-"
        query = request.url.query
        if not quiet:
            self.log.info(f"요청 시작 method={request.method} path={path}"
                          + (f" query={query[:200]}" if query else "")
                          + (f" user={user}" if user else "") + f" client={client}"
                          + f" size={request.headers.get('content-length', '0')}")
        try:
            try:
                response = await call_next(request)
            except Exception:
                ms = int((time.perf_counter() - started) * 1000)
                self.log.exception(f"요청 실패 method={request.method} path={path} status=500 ms={ms} (처리되지 않은 예외)")
                raise
            ms = int((time.perf_counter() - started) * 1000)
            status = response.status_code
            # SSE 같은 스트리밍 응답은 '응답 시작' 시점까지의 시간 (본문 완료는 각 서비스가 별도로 기록)
            msg = f"요청 완료 method={request.method} path={path} status={status} ms={ms}"
            if quiet and status < 400:
                self.log.debug(msg)
            elif status >= 500:
                self.log.error(msg)
            elif status >= 400:
                self.log.warning(msg)
            else:
                self.log.info(msg)
            response.headers["X-Request-ID"] = rid
            return response
        finally:
            request_id_var.reset(token)


def current_request_id() -> Optional[str]:
    rid = request_id_var.get()
    return None if rid == "-" else rid


def short(text: Optional[str], n: int = 120) -> str:
    """로그용 문자열 (줄바꿈 제거, 길이 제한, 따옴표)"""
    t = (text or "").replace("\n", " ").replace('"', "'")
    return f'"{t[:n]}{"…" if len(t) > n else ""}"'
