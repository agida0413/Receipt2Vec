"""
거래 저장소 API (/api/transactions)

POST   /transactions           확정된 거래 저장 (ocr_backend 가 확인을 마친 Transaction)
GET    /transactions?userId=   최근 거래 목록
DELETE /transactions/{id}?userId=
POST   /transactions/query     구조화된 조건으로 집계/목록/검색 (정확 → 완화 → 의미 검색)
GET    /transactions/vocabulary?userId=  사용자의 실제 가맹점·업종 목록
POST   /transactions/reenrich  파생 필드(업종·임베딩 …) 재생성 (관리자)

모든 API 는 Bearer 토큰 필요. userId 는 토큰 주인과 같아야 하며(다르면 403), 생략하면 토큰 주인으로 처리
"""
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.concurrency import run_in_threadpool

from app.services.transactions.schemas import (
    SaveTransactionsRequest, SaveTransactionsResponse, TransactionQuery, TransactionQueryResult,
    TransactionRow, UserVocabulary,
)
from app.core.auth import require_admin, resolve_user_id
from app.services.transactions.store import InvalidTransactionError, StoreUnavailableError, transaction_store

router = APIRouter()

_STORE_DOWN = "거래 저장소(OpenSearch)에 연결할 수 없습니다."


@router.post("", response_model=SaveTransactionsResponse)
async def save_transactions(request: SaveTransactionsRequest):
    request.userId = resolve_user_id(request.userId)   # 토큰 주인의 거래로만 저장
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
async def list_transactions(userId: Optional[str] = Query(None), limit: int = Query(20, ge=1, le=100)):
    userId = resolve_user_id(userId)
    try:
        return await run_in_threadpool(transaction_store.recent, userId, limit)
    except StoreUnavailableError:
        raise HTTPException(status_code=503, detail=_STORE_DOWN)


@router.delete("/{transaction_id}")
async def delete_transaction(transaction_id: str, userId: Optional[str] = Query(None)):
    userId = resolve_user_id(userId)
    try:
        deleted = await run_in_threadpool(transaction_store.delete, userId, transaction_id)
    except StoreUnavailableError:
        raise HTTPException(status_code=503, detail=_STORE_DOWN)
    if not deleted:
        raise HTTPException(status_code=404, detail="거래를 찾을 수 없습니다.")
    return {"deleted": deleted}


@router.post("/query", response_model=TransactionQueryResult)
async def query_transactions(request: TransactionQuery):
    request.userId = resolve_user_id(request.userId)   # 다른 사용자 조건으로 조회 불가
    try:
        return await run_in_threadpool(transaction_store.query, request)
    except StoreUnavailableError:
        raise HTTPException(status_code=503, detail=_STORE_DOWN)


@router.get("/vocabulary", response_model=UserVocabulary)
async def user_vocabulary(userId: Optional[str] = Query(None), size: int = Query(50, ge=1, le=200)):
    """사용자의 실제 가맹점·업종·간편결제 목록 (챗봇 질문 해석에 사용)"""
    userId = resolve_user_id(userId)
    try:
        return await run_in_threadpool(transaction_store.vocabulary, userId, size)
    except StoreUnavailableError:
        raise HTTPException(status_code=503, detail=_STORE_DOWN)


@router.post("/reenrich", dependencies=[Depends(require_admin)])
async def reenrich_transactions():
    """업종·브랜드 사전이나 보강 규칙을 바꾼 뒤 기존 거래 전체에 다시 적용 (관리용)"""
    try:
        n = await run_in_threadpool(transaction_store.reenrich)
    except StoreUnavailableError:
        raise HTTPException(status_code=503, detail=_STORE_DOWN)
    return {"reenriched": n}
