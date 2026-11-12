"""
OCR 엔드포인트

POST /api/ocr/recognize : 이미지 → PaddleOCR → 규칙 파서 →(실패 시) Gemini → 검증 → Transaction[]
POST /api/ocr/parse     : 텍스트만 넣어서 위 파이프라인 실행 (OCR 미실행, 파서 튜닝용)
POST /api/ocr/confirm   : 사용자가 확인/수정한 Transaction[] 재검증 → 저장 가능한 형태로 확정

OCR 백엔드는 저장하지 않는다. confirm 결과를 클라이언트가 RAG 백엔드 저장 API 로 보낸다.
"""
import logging
import time
from typing import Optional

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool

from app.core.config import settings
from app.models import (
    ConfirmError, ConfirmRequest, ConfirmResponse, DocumentType, OcrLineOut, OcrResponse,
    ParseTextRequest, ParseTextResponse, ReceiptData,
)
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
    if not data:
        raise HTTPException(status_code=400, detail="빈 파일입니다.")
    if len(data) > settings.max_upload_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"파일이 너무 큽니다. (최대 {settings.max_upload_bytes // 1024 // 1024}MB)",
        )

    try:
        image = load_image(data, settings.image_max_side)
    except InvalidImageError as e:
        raise HTTPException(status_code=400, detail=str(e))

    started = time.perf_counter()
    try:
        lines = await run_in_threadpool(ocr_engine.recognize, image)
    except Exception as e:
        logger.exception("OCR 실패")
        raise HTTPException(status_code=500, detail=f"OCR 처리 중 오류: {e}")
    ocr_ms = int((time.perf_counter() - started) * 1000)

    rows = group_rows(lines)
    ocr_score = round(sum(l.score for l in lines) / len(lines), 4) if lines else None
    result = await extract_transactions(
        [r.text for r in rows], doc_type=document_type, user_id=user_id,
        file_name=file.filename, ocr_score=ocr_score, use_llm=use_llm, force_llm=force_llm,
    )
    elapsed = int((time.perf_counter() - started) * 1000)
    logger.info(
        f"인식 완료: user={user_id} file={file.filename} type={result.documentType.value} "
        f"tx={len(result.transactions)} method={result.extraction.method} "
        f"ocr={ocr_ms}ms total={elapsed}ms"
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
