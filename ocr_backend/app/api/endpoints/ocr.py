"""
OCR 엔드포인트

POST /api/ocr/recognize : 이미지 → PaddleOCR → 규칙 파서 →(실패 시) Gemini → 검증 → Transaction[]
POST /api/ocr/parse     : 텍스트만 넣어서 위 파이프라인 실행 (OCR 미실행, 파서 튜닝용)
POST /api/ocr/confirm   : 사용자가 확인/수정한 Transaction[] 재검증 → 저장 가능한 형태로 확정
POST /api/ocr/transactions : confirm 과 같은 검증 후 rag_backend(거래 저장소)에 저장

OpenSearch 에는 직접 쓰지 않는다. 저장·검색·챗봇은 rag_backend 담당.
"""
import logging
import time
from typing import Optional

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool

from app.core.config import settings
from app.models import (
    ConfirmError, ConfirmRequest, ConfirmResponse, DocumentType, OcrLineOut, OcrResponse,
    ParseTextRequest, ParseTextResponse, ReceiptData, SaveTransactionsRequest, SaveTransactionsResponse,
)
from app.services.transaction_store import StoreRequestError, StoreUnavailableError, transaction_store
from app.services.ocr import ocr_engine
from app.services.ocr.image_utils import InvalidImageError, load_image
from app.services.ocr.layout import group_rows
from app.services.pipeline import PipelineResult, confirm_transactions, extract_transactions

logger = logging.getLogger(__name__)
router = APIRouter()


def _common_fields(result: PipelineResult) -> dict:
    is_receipt = isinstance(result.ruleResult, ReceiptData)
    return dict(
        documentType=result.documentType,
        needsReview=bool(result.documentReviewReasons and not result.transactions)
        or any(tx.quality.needsReview for tx in result.transactions),
        documentReviewReasons=result.documentReviewReasons,
        transactions=result.transactions,
        extraction=result.extraction,
        receipt=result.ruleResult if is_receipt else None,
        paymentApp=None if is_receipt else result.ruleResult,
    )


@router.post("/recognize", response_model=OcrResponse)
async def recognize(
    file: UploadFile = File(..., description="영수증 사진 또는 결제내역 캡처 (jpg/png/webp 등)"),
    document_type: DocumentType = Form(DocumentType.auto, description="auto/receipt/payment_app"),
    user_id: Optional[str] = Form(None, description="사용자 식별자 (Transaction.userId 로 전달)"),
    use_llm: bool = Form(True, description="false 면 LLM 을 전혀 사용하지 않음"),
    force_llm: Optional[bool] = Form(None, description="true 면 LLM 무조건 사용 (미지정 시 .env 의 LLM_MODE=always 여부)"),
):
    data = await file.read()
    logger.info(f"인식 요청 user={user_id} file={file.filename!r} bytes={len(data)} type={document_type.value} "
                f"use_llm={use_llm} force_llm={force_llm}")
    if not data:
        logger.warning(f"인식 거부: 빈 파일 user={user_id} file={file.filename!r}")
        raise HTTPException(status_code=400, detail="빈 파일입니다.")
    if len(data) > settings.max_upload_bytes:
        logger.warning(f"인식 거부: 파일 크기 초과 user={user_id} bytes={len(data)}")
        raise HTTPException(
            status_code=413,
            detail=f"파일이 너무 큽니다. (최대 {settings.max_upload_bytes // 1024 // 1024}MB)",
        )

    try:
        image = load_image(data, settings.image_max_side)
    except InvalidImageError as e:
        logger.warning(f"인식 거부: 이미지 아님 user={user_id} file={file.filename!r} error={e}")
        raise HTTPException(status_code=400, detail=str(e))

    started = time.perf_counter()
    try:
        lines = await run_in_threadpool(ocr_engine.recognize, image)
    except Exception as e:
        logger.exception(f"OCR 실패 user={user_id} file={file.filename!r} size={image.shape[1]}x{image.shape[0]}")
        raise HTTPException(status_code=500, detail=f"OCR 처리 중 오류: {e}")
    ocr_ms = int((time.perf_counter() - started) * 1000)

    rows = group_rows(lines)
    ocr_score = round(sum(l.score for l in lines) / len(lines), 4) if lines else None
    logger.info(f"OCR 완료 user={user_id} ms={ocr_ms} image={image.shape[1]}x{image.shape[0]} boxes={len(lines)} "
                f"rows={len(rows)} score={ocr_score}")
    result = await extract_transactions(
        [r.text for r in rows], doc_type=document_type, user_id=user_id,
        file_name=file.filename, ocr_score=ocr_score, use_llm=use_llm, force_llm=force_llm,
    )
    elapsed = int((time.perf_counter() - started) * 1000)
    ex = result.extraction
    summary = [f"{t.merchant.name}/{t.transactedDate}/{t.amount.total}" for t in result.transactions[:5]]
    (logger.info if result.transactions else logger.warning)(
        f"인식 완료 user={user_id} file={file.filename!r} type={result.documentType.value} "
        f"tx={len(result.transactions)} method={ex.method} llm_used={ex.llmUsed} llm_skipped={ex.llmSkippedReason} "
        f"llm_error={ex.llmError} unverified={ex.llmUnverifiedFields} ocr_ms={ocr_ms} total_ms={elapsed} "
        f"reasons={sorted({r.value for t in result.transactions for r in t.quality.reviewReasons})} tx_list={summary}"
    )

    return OcrResponse(
        userId=user_id,
        fileName=file.filename,
        imageWidth=image.shape[1],
        imageHeight=image.shape[0],
        elapsedMs=elapsed,
        ocrScore=ocr_score,
        rawText=result.rawText,
        lines=[OcrLineOut(text=l.text, score=l.score, box=list(l.box)) for l in lines],
        **_common_fields(result),
    )


@router.post("/parse", response_model=ParseTextResponse)
async def parse_text(request: ParseTextRequest):
    rows = [r.strip() for r in request.text.splitlines() if r.strip()]
    result = await extract_transactions(
        rows, doc_type=request.documentType, user_id=request.userId, use_llm=request.useLlm,
        force_llm=request.forceLlm,
    )
    return ParseTextResponse(**_common_fields(result))


@router.post("/confirm", response_model=ConfirmResponse)
async def confirm(request: ConfirmRequest):
    """
    사용자 확인 단계에서 수정한 값을 다시 정규화/검증한다.
    가맹점명·거래일·금액이 모두 있으면 quality.confirmed=true → 저장 가능.
    """
    confirmed = confirm_transactions(request.transactions)
    errors = [
        ConfirmError(index=i, transactionId=tx.transactionId, missingFields=tx.quality.missingFields)
        for i, tx in enumerate(confirmed)
        if not tx.quality.confirmed
    ]
    return ConfirmResponse(valid=not errors, transactions=confirmed, errors=errors)


@router.post("/transactions", response_model=SaveTransactionsResponse)
async def save_transactions(request: SaveTransactionsRequest):
    """
    사용자 확인을 마친 거래 저장: confirm 과 같은 재검증(클라이언트가 /confirm 을 건너뛰어도 안전) 후
    rag_backend 거래 저장소로 전달. 중복 거래는 저장소가 건너뛰고 duplicates 로 알려준다.
    """
    confirmed = confirm_transactions(request.transactions)
    invalid = [
        {"transactionId": tx.transactionId, "missingFields": tx.quality.missingFields}
        for tx in confirmed if not tx.quality.confirmed
    ]
    logger.info(f"저장 요청 user={request.userId} tx={len(confirmed)} invalid={len(invalid)}")
    if invalid:
        logger.warning(f"저장 거부: 필수 항목 누락 user={request.userId} invalid={invalid}")
        raise HTTPException(status_code=422, detail={"message": "필수 항목(가맹점·날짜·금액)이 비었습니다.",
                                                     "invalid": invalid})
    try:
        saved, duplicates = await transaction_store.save(request.userId, confirmed)
        logger.info(f"저장 완료 user={request.userId} saved={len(saved)} duplicates={len(duplicates)}")
    except StoreUnavailableError as e:
        logger.error(f"저장 실패: 거래 저장소 연결 불가 user={request.userId} error={e}")
        raise HTTPException(status_code=503, detail="거래 저장소(rag_backend)에 연결할 수 없습니다. "
                                                    "rag_backend 가 시작 중이면 잠시 후 다시 시도해 주세요.")
    except StoreRequestError as e:
        logger.error(f"저장 실패: 거래 저장소 거부 user={request.userId} status={e.status_code} detail={e.detail}")
        raise HTTPException(status_code=e.status_code, detail=e.detail)
    return SaveTransactionsResponse(saved=saved, duplicates=duplicates)
