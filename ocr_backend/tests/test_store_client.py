"""
거래 저장 클라이언트 (→ rag_backend) 테스트
"""
import asyncio
import json
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.transaction_store import (  # noqa: E402
    StoreRequestError, StoreUnavailableError, TransactionStoreClient,
)


def _tx():
    from tests.test_pipeline import CAFE_RECEIPT, FakeLlm, run
    return run(CAFE_RECEIPT, user_id="u", llm=FakeLlm()).transactions[0]


def test_save_posts_transactions():
    seen = {}

    def handler(request: httpx.Request):
        seen["url"], seen["body"] = str(request.url), json.loads(request.content)
        return httpx.Response(200, json={"saved": ["a"], "duplicates": []})
    client = TransactionStoreClient("http://rag", transport=httpx.MockTransport(handler))
    tx = _tx()
    saved, dup = asyncio.run(client.save("user01", [tx]))
    assert seen["url"] == "http://rag/api/transactions" and seen["body"]["userId"] == "user01"
    assert seen["body"]["transactions"][0]["transactionId"] == tx.transactionId
    assert saved == ["a"] and dup == []


def test_save_errors():
    def refuse(request):
        raise httpx.ConnectError("refused")
    down = TransactionStoreClient("http://rag", transport=httpx.MockTransport(refuse))
    try:
        asyncio.run(down.save("u", [_tx()]))
        assert False
    except StoreUnavailableError:
        pass
    bad = TransactionStoreClient("http://rag", transport=httpx.MockTransport(
        lambda r: httpx.Response(422, json={"detail": "매핑 불일치"})))
    try:
        asyncio.run(bad.save("u", [_tx()]))
        assert False
    except StoreRequestError as e:
        assert e.status_code == 422 and e.detail == "매핑 불일치"
