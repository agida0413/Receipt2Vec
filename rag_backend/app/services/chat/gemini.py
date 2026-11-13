"""
Gemini 공용 클라이언트 / 재시도 / 할당량 쿨다운

OCR 추출기와 챗봇이 같은 API 키(같은 프로젝트 할당량)를 쓰므로 쿨다운 상태를 공유한다.
- 500/503(과부하): 지수 백오프 재시도
- 429 + 짧은 대기(분당 한도): 서버가 알려준 시간만큼 기다렸다 재시도
- 429 + 긴 대기(일일 한도 등): 재시도 없이 그 시간 동안 호출 중단(쿨다운)
"""
import asyncio
import logging
import re
import time
from datetime import datetime
from typing import AsyncIterator, Awaitable, Callable, Dict, Optional, TypeVar
from zoneinfo import ZoneInfo

from app.core.config import settings

logger = logging.getLogger(__name__)

RETRYABLE_CODES = {500, 503}
MAX_ATTEMPTS = 3
RETRY_BASE_DELAY_SEC = 1.0
MAX_INLINE_RETRY_WAIT_SEC = 10.0
DEFAULT_COOLDOWN_SEC = 60.0
# 402(선불 크레딧 소진/결제 필요): 충전 전까지 풀리지 않으므로 길게 쉬고 주기적으로만 재확인
BILLING_COOLDOWN_SEC = 600.0

T = TypeVar("T")


class QuotaExceededError(RuntimeError):
    """Gemini 할당량 초과 — 쿨다운 동안 LLM 호출 중단"""


def parse_retry_delay(message: str) -> Optional[float]:
    """429 응답의 RetryInfo('retryDelay': '344s') 또는 'retry in 5m44.7s' 에서 대기 초 추출"""
    m = re.search(r"retryDelay['\"]?\s*:\s*['\"](\d+(?:\.\d+)?)s", message)
    if m:
        return float(m.group(1))
    m = re.search(r"retry in (?:(\d+)h)?(?:(\d+)m)?(\d+(?:\.\d+)?)s", message)
    if m:
        h, mi, s = m.groups()
        return int(h or 0) * 3600 + int(mi or 0) * 60 + float(s)
    return None


class QuotaGuard:
    """할당량 초과 시 일정 시간 LLM 호출을 막는 차단기"""

    def __init__(self):
        self._until: Optional[float] = None
        self._reason = "quota"

    def message(self, model_name: str) -> Optional[str]:
        if self._until is None or time.time() >= self._until:
            return None
        until = datetime.fromtimestamp(self._until, ZoneInfo(settings.timezone))
        if self._reason == "billing":
            return (f"Gemini 선불 크레딧 소진/결제 필요({model_name}) — {until:%H:%M:%S}까지 LLM 호출 중단. "
                    f"https://ai.studio/projects 에서 결제 설정을 확인하세요")
        return (f"Gemini 할당량 초과({model_name}) — {until:%H:%M:%S} 이후 재개 "
                f"(무료 등급 일일 한도일 수 있음, https://ai.dev/rate-limit 에서 확인)")

    def start(self, retry_after: Optional[float], reason: str = "quota") -> None:
        wait = retry_after if retry_after is not None else DEFAULT_COOLDOWN_SEC
        self._until = time.time() + wait
        self._reason = reason
        logger.warning(f"Gemini {'결제/크레딧 문제' if reason == 'billing' else '할당량 초과'} "
                       f"— {wait:.0f}초 동안 LLM 호출 중단")


# OCR 추출기와 챗봇이 공유하는 기본 차단기
shared_quota_guard = QuotaGuard()

_clients: Dict[str, object] = {}


def get_client(api_key: str):
    if api_key not in _clients:
        from google import genai
        from google.genai import types
        # system_instruction / response_schema 는 v1beta 에서 지원 (SDK 기본값 v1 은 거부)
        _clients[api_key] = genai.Client(
            api_key=api_key, http_options=types.HttpOptions(api_version="v1beta"),
        )
    return _clients[api_key]


def _handle_error(e: Exception, attempt: int, guard: QuotaGuard, model_name: str) -> float:
    """재시도할 대기 시간을 반환하거나, 재시도 불가면 예외를 다시 던진다"""
    code = getattr(e, "code", None)
    if code == 402:
        guard.start(BILLING_COOLDOWN_SEC, reason="billing")
        raise QuotaExceededError(guard.message(model_name) or "Gemini 결제 필요") from e
    if code == 429:
        retry_after = parse_retry_delay(str(e))
        if retry_after is None or retry_after > MAX_INLINE_RETRY_WAIT_SEC:
            guard.start(retry_after)
            raise QuotaExceededError(guard.message(model_name) or "Gemini 할당량 초과") from e
        delay = retry_after
    elif code in RETRYABLE_CODES:
        delay = RETRY_BASE_DELAY_SEC * (2 ** attempt)
    else:
        raise e
    if attempt == MAX_ATTEMPTS - 1:
        raise e
    logger.warning(f"Gemini {code} — {delay:.1f}초 후 재시도 ({attempt + 1}/{MAX_ATTEMPTS - 1})")
    return delay


async def call_with_retry(
    call: Callable[[], Awaitable[T]], *, guard: QuotaGuard, model_name: str, timeout: float,
) -> T:
    for attempt in range(MAX_ATTEMPTS):
        t0 = time.perf_counter()
        try:
            res = await asyncio.wait_for(call(), timeout=timeout)
            usage = getattr(res, "usage_metadata", None)
            logger.info(f"Gemini 호출 성공 model={model_name} ms={int((time.perf_counter() - t0) * 1000)} "
                        f"attempt={attempt + 1} in_tokens={getattr(usage, 'prompt_token_count', None)} "
                        f"out_tokens={getattr(usage, 'candidates_token_count', None)}")
            return res
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Gemini 호출 실패 model={model_name} ms={int((time.perf_counter() - t0) * 1000)} "
                           f"attempt={attempt + 1} error={type(e).__name__}: {str(e)[:300]}")
            delay = _handle_error(e, attempt, guard, model_name)
            await asyncio.sleep(delay)
    raise RuntimeError("unreachable")


async def stream_with_retry(
    start: Callable[[], Awaitable[AsyncIterator]], *, guard: QuotaGuard, model_name: str, timeout: float,
) -> AsyncIterator[str]:
    """
    스트리밍 응답의 텍스트 조각을 순서대로 내보낸다.
    첫 조각을 받기 전 오류만 재시도한다 (이미 일부를 내보낸 뒤에는 재시도하면 중복 출력됨).
    """
    for attempt in range(MAX_ATTEMPTS):
        started = False
        t0 = time.perf_counter()
        first_ms = None
        chunks = 0
        try:
            stream = await asyncio.wait_for(start(), timeout=timeout)
            async for chunk in stream:
                text = getattr(chunk, "text", None)
                if text:
                    if not started:
                        first_ms = int((time.perf_counter() - t0) * 1000)
                    started = True
                    chunks += 1
                    yield text
            logger.info(f"Gemini 스트리밍 완료 model={model_name} first_token_ms={first_ms} chunks={chunks} "
                        f"ms={int((time.perf_counter() - t0) * 1000)} attempt={attempt + 1}")
            return
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Gemini 스트리밍 실패 model={model_name} started={started} chunks={chunks} "
                           f"attempt={attempt + 1} error={type(e).__name__}: {str(e)[:300]}")
            if started:
                raise
            delay = _handle_error(e, attempt, guard, model_name)
            await asyncio.sleep(delay)
