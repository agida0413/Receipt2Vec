"""Gemini 429 처리: 긴 대기(일일 한도)는 재시도 없이 쿨다운, 짧은 대기(분당 한도)는 기다렸다 재시도"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from app.core.config import LlmConfig  # noqa: E402
from app.domain.transaction import SourceType  # noqa: E402
from app.services.llm import gemini_client as ge_client  # noqa: E402
from app.services.llm import gemini_extractor as ge  # noqa: E402

# 실제로 받은 429 메시지 (일부)
DAILY_429 = ("429 RESOURCE_EXHAUSTED. {'error': {'code': 429, 'message': 'You exceeded your current quota ... "
             "Please retry in 5m44.72765717s.', 'details': [{'@type': 'type.googleapis.com/google.rpc.RetryInfo', "
             "'retryDelay': '344s'}]}}")


class FakeApiError(Exception):
    def __init__(self, code, msg):
        super().__init__(msg)
        self.code = code


class FakeModels:
    def __init__(self, errors):
        self.errors, self.calls = list(errors), 0

    async def generate_content(self, **kw):
        self.calls += 1
        if self.errors:
            raise self.errors.pop(0)

        class R:
            text = ('{"documentType":"RECEIPT","screenProvider":null,"transactions":[]}')
        return R()


def make(errors):
    cfg = LlmConfig(mode="fallback", api_key="k", model_name="m", timeout_sec=5,
                    thinking_budget=0, max_input_chars=100)
    ex = ge.GeminiExtractor(cfg)
    models = FakeModels(errors)
    ex._client = type("C", (), {"aio": type("A", (), {"models": models})()})()
    ex._build_config = lambda: None
    return ex, models


def test_parse_retry_delay():
    assert ge.parse_retry_delay(DAILY_429) == 344.0
    assert ge.parse_retry_delay("Please retry in 5m44.5s.") == 344.5
    assert ge.parse_retry_delay("Please retry in 2.5s.") == 2.5
    assert ge.parse_retry_delay("no info") is None


def test_daily_quota_no_retry_and_cooldown():
    ex, models = make([FakeApiError(429, DAILY_429)])
    with pytest.raises(ge.QuotaExceededError):
        asyncio.run(ex.extract("text", SourceType.RECEIPT))
    assert models.calls == 1                      # 재시도하지 않음
    assert ex.cooldown_message and "재개" in ex.cooldown_message


def test_short_429_waits_and_retries(monkeypatch):
    slept = []

    async def fake_sleep(s):
        slept.append(s)
    monkeypatch.setattr(ge_client.asyncio, "sleep", fake_sleep)
    ex, models = make([FakeApiError(429, "Please retry in 2s.")])
    asyncio.run(ex.extract("text", SourceType.RECEIPT))
    assert models.calls == 2 and slept == [2.0] and ex.cooldown_message is None


def test_pipeline_skips_llm_during_cooldown():
    from app.services.pipeline import extract_transactions
    from tests.test_pipeline import SNACK_RECEIPT
    ex, models = make([FakeApiError(429, DAILY_429)])
    r1 = asyncio.run(extract_transactions(SNACK_RECEIPT, llm=ex))
    assert r1.extraction.method == "rule" and "할당량 초과" in r1.extraction.llmError
    r2 = asyncio.run(extract_transactions(SNACK_RECEIPT, llm=ex))
    assert not r2.extraction.llmAttempted and "할당량 초과" in r2.extraction.llmSkippedReason
    assert models.calls == 1                      # 쿨다운 중에는 호출 안 함
    assert r2.transactions                        # 규칙 결과는 정상 응답


def test_billing_402_cooldown():
    """실제 사례: 402 'Your prepayment credits are depleted' → 재시도 없이 쿨다운 + 결제 안내"""
    ex, models = make([FakeApiError(402, "402 RESOURCE_EXHAUSTED. prepayment credits are depleted")])
    with pytest.raises(ge.QuotaExceededError):
        asyncio.run(ex.extract("text", SourceType.RECEIPT))
    assert models.calls == 1
    assert "크레딧" in ex.cooldown_message and "ai.studio" in ex.cooldown_message
