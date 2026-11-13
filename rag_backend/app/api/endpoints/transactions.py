"""
거래 저장소 API (/api/transactions)

POST   /transactions           확정된 거래 저장 (ocr_backend 가 확인을 마친 Transaction)
GET    /transactions?userId=   최근 거래 목록
DELETE /transactions/{id}?userId=
POST   /transactions/query     구조화된 조건으로 집계/목록/검색 (정확 → 완화 → 의미 검색)
GET    /transactions/vocabulary?userId=  사용자의 실제 가맹점·업종 목록
POST   /transactions/reenrich  파생 필드(업종·임베딩 …) 재생성
"""
from typing import List

from fastapi import APIRouter, HTTPException, Query
from fastapi.concurrency import run_in_threadpool

from app.services.transactions.schemas import (
    SaveTransactionsRequest, SaveTransactionsResponse, TransactionQuery, TransactionQueryResult,
    TransactionRow, UserVocabulary,
)
from app.services.transactions.store import InvalidTransactionError, StoreUnavailableError, transaction_store

router = APIRouter()

_STORE_DOWN = "거래 저장소(OpenSearch)에 연결할 수 없습니다."


@router.post("", response_model=SaveTransactionsResponse)
async def save_transactions(request: SaveTransactionsRequest):
    missing = [i for i, tx in enumerate(request.transactions) if not tx.get("transactionId")]
    if missing:
        raise HTTPException(status_code=422, detail=f"transactionId 가 없는 거래: index {missing}")
    try:
        saved, duplicates = await run_in_threadpool(transaction_store.save, request.userId, request.transactions)
    except StoreUnavailableError:
        raise HTTPException(status_code=503, detail=_STORE_DOWN)
    except InvalidTransactionError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return SaveTransactionsResponse(saved=saved, duplicates=duplicates)


@router.get("", response_model=List[TransactionRow])
async def list_transactions(userId: str = Query(..., min_length=1), limit: int = Query(20, ge=1, le=100)):
    try:
        return await run_in_threadpool(transaction_store.recent, userId, limit)
    except StoreUnavailableError:
        raise HTTPException(status_code=503, detail=_STORE_DOWN)


@router.delete("/{transaction_id}")
async def delete_transaction(transaction_id: str, userId: str = Query(..., min_length=1)):
    try:
        deleted = await run_in_threadpool(transaction_store.delete, userId, transaction_id)
    except StoreUnavailableError:
        raise HTTPException(status_code=503, detail=_STORE_DOWN)
    if not deleted:
        raise HTTPException(status_code=404, detail="거래를 찾을 수 없습니다.")
    return {"deleted": deleted}


@router.post("/query", response_model=TransactionQueryResult)
async def query_transactions(request: TransactionQuery):
    try:
        return await run_in_threadpool(transaction_store.query, request)
    except StoreUnavailableError:
        raise HTTPException(status_code=503, detail=_STORE_DOWN)


@router.get("/vocabulary", response_model=UserVocabulary)
async def user_vocabulary(userId: str = Query(..., min_length=1), size: int = Query(50, ge=1, le=200)):
    """사용자의 실제 가맹점·업종·간편결제 목록 (챗봇 질문 해석에 사용)"""
    try:
        return await run_in_threadpool(transaction_store.vocabulary, userId, size)
    except StoreUnavailableError:
        raise HTTPException(status_code=503, detail=_STORE_DOWN)


@router.post("/reenrich")
async def reenrich_transactions():
    """업종·브랜드 사전이나 보강 규칙을 바꾼 뒤 기존 거래 전체에 다시 적용 (관리용)"""
    try:
        n = await run_in_threadpool(transaction_store.reenrich)
    except StoreUnavailableError:
        raise HTTPException(status_code=503, detail=_STORE_DOWN)
    return {"reenriched": n}
