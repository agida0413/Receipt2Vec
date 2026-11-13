"""
거래 저장 클라이언트 → rag_backend POST /api/transactions

ocr_backend 는 추출·검증·사용자 확인까지만 하고, 확정된 거래를 rag_backend(거래 저장소)로 넘긴다.
OpenSearch 인덱스(매핑·보강·검색)와 챗봇은 rag_backend 가 소유한다.
"""
from typing import Any, List, Optional, Tuple

import httpx

from app.core.config import settings
from app.domain.transaction import Transaction


class StoreUnavailableError(RuntimeError):
    """rag_backend(또는 그 뒤의 OpenSearch)에 연결할 수 없음"""


class StoreRequestError(RuntimeError):
    """rag_backend 가 요청을 거부함 (4xx)"""

    def __init__(self, status_code: int, detail: Any):
        super().__init__(f"{status_code}: {detail}")
        self.status_code = status_code
        self.detail = detail


class TransactionStoreClient:
    def __init__(self, base_url: Optional[str] = None, transport: Optional[httpx.AsyncBaseTransport] = None):
        self._url = (base_url or settings.store.url) + "/api/transactions"
        self._transport = transport

    async def save(self, user_id: str, txs: List[Transaction]) -> Tuple[List[str], List[str]]:
        body = {"userId": user_id, "transactions": [tx.model_dump(mode="json") for tx in txs]}
        try:
            async with httpx.AsyncClient(timeout=settings.store.timeout_sec, transport=self._transport) as c:
                res = await c.post(self._url, json=body)
        except httpx.HTTPError as e:
            raise StoreUnavailableError(f"거래 저장소({self._url}) 연결 실패: {type(e).__name__}") from e
        if res.status_code >= 500:
            raise StoreUnavailableError(f"거래 저장소 오류 {res.status_code}: {res.text[:200]}")
        if res.status_code >= 400:
            try:
                detail = res.json().get("detail")
            except ValueError:
                detail = res.text[:200]
            raise StoreRequestError(res.status_code, detail)
        data = res.json()
        return data["saved"], data.get("duplicates", [])


transaction_store = TransactionStoreClient()
