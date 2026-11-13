"""
추출 파이프라인

  OCR 줄 텍스트
    → 규칙 파서 → Normalizer → 품질 평가
    → (LLM_TRIGGER_REASONS 가 있으면) Gemini 추출 → 원문 대조 검증 → Normalizer → 품질 평가
    → 더 나은 쪽 채택 → Transaction[] (needsReview 표시) → 클라이언트에서 사용자 확인
"""
import logging
import uuid
from dataclasses import dataclass
from typing import List, Optional

from app.core.config import settings
from app.domain.transaction import ReviewReason, SourceType, Transaction
from app.models import DocumentType, ExtractionInfo, PaymentAppData, ReceiptData
from app.services import normalizer, quality
from app.services.llm.gemini_extractor import Extractor, QuotaExceededError, gemini_extractor
from app.services.llm.verifier import verify
from app.services.parser import (
    detect_document_type_scored, normalize_rows, parse_payment_app, parse_receipt,
)
from app.services.parser.common import mask_card_numbers

logger = logging.getLogger(__name__)

_DOC_TO_SOURCE = {
    DocumentType.receipt: SourceType.RECEIPT,
    DocumentType.payment_app: SourceType.PAYMENT_APP,
}
_SOURCE_TO_DOC = {v: k for k, v in _DOC_TO_SOURCE.items()}


@dataclass
class PipelineResult:
    documentType: DocumentType
    ruleResult: ReceiptData | PaymentAppData
    rawText: str
    transactions: List[Transaction]
    documentReviewReasons: List[ReviewReason]
    extraction: ExtractionInfo


def _evaluate_all(txs: List[Transaction], ocr_score: Optional[float], extra=()) -> List[Transaction]:
    return [
        quality.evaluate(tx, ocr_score=ocr_score, min_ocr_score=settings.review_min_ocr_score,
                         extra_reasons=extra)
        for tx in txs
    ]


async def extract_transactions(
    rows: List[str],
    *,
    doc_type: DocumentType = DocumentType.auto,
    user_id: Optional[str] = None,
    file_name: Optional[str] = None,
    ocr_score: Optional[float] = None,
    use_llm: bool = True,
    force_llm: Optional[bool] = None,
    llm: Optional[Extractor] = None,
) -> PipelineResult:
    """
    force_llm: True 면 규칙 결과와 무관하게 LLM 을 호출하고 그 결과를 채택
               (LLM 실패 / 거래 0건이면 규칙 결과 사용). None 이면 .env 의 LLM_MODE=always 여부를 따름.
    """
    llm = llm or gemini_extractor
    force = settings.llm.always if force_llm is None else force_llm
    # 파서도 카드번호가 마스킹된 줄로 동작 (전화번호/금액 오인 방지, 원본 번호는 이후 단계로 안 넘어감)
    rows = [mask_card_numbers(r) for r in normalize_rows(rows)]
    raw_text = "\n".join(rows)
    document_id = str(uuid.uuid4())

    # ------------------------------------------------------------------ 규칙 파서
    if doc_type == DocumentType.auto:
        detected, confident = detect_document_type_scored(rows)
    else:
        detected, confident = doc_type, True

    if detected == DocumentType.payment_app:
        rule_result = parse_payment_app(rows)
        source = normalizer.make_source(SourceType.PAYMENT_APP, provider_text=rule_result.provider,
                                        document_id=document_id, file_name=file_name, ocr_score=ocr_score)
        rule_txs = normalizer.from_payment_app(rule_result, source=source, raw_text=raw_text, user_id=user_id)
    else:
        rule_result = parse_receipt(rows)
        source = normalizer.make_source(SourceType.RECEIPT, provider_text=None,
                                        document_id=document_id, file_name=file_name, ocr_score=ocr_score)
        rule_txs = normalizer.from_receipt(rule_result, source=source, raw_text=raw_text, user_id=user_id)

    rule_doc_reasons = quality.document_reasons(rule_txs, confident)
    rule_txs = _evaluate_all(rule_txs, ocr_score, extra=rule_doc_reasons - {ReviewReason.NO_TRANSACTIONS})
    triggers = quality.trigger_reasons(rule_txs, rule_doc_reasons)

    info = ExtractionInfo(
        method="rule",
        ruleParser=f"rule:{detected.value}",
        llmTriggerReasons=sorted(triggers, key=lambda r: r.value),
        llmForced=force,
    )
    final = PipelineResult(
        documentType=detected, ruleResult=rule_result, rawText=raw_text, transactions=rule_txs,
        documentReviewReasons=sorted(rule_doc_reasons, key=lambda r: r.value), extraction=info,
    )

    if not triggers and not force:
        return final
    if not use_llm:
        info.llmSkippedReason = "요청에서 use_llm=false"
        return final
    if not llm.is_available:
        info.llmSkippedReason = "LLM_MODE=off 또는 GEMINI_API_KEY 미설정"
        return final

    cooldown = getattr(llm, "cooldown_message", None)
    if cooldown:
        info.llmSkippedReason = cooldown
        return final

    # ------------------------------------------------------------------ LLM 폴백
    info.llmAttempted = True
    info.llmModel = llm.model_name
    try:
        extraction = await llm.extract(raw_text, hint=_DOC_TO_SOURCE[detected])
    except QuotaExceededError as e:
        logger.warning(f"LLM 할당량 초과 — 규칙 파서 결과 사용: {e}")
        info.llmError = str(e)
        return final
    except Exception as e:
        logger.exception("LLM 추출 실패 — 규칙 파서 결과 사용")
        info.llmError = f"{type(e).__name__}: {e}"
        return final

    checked = verify(extraction, raw_text)
    info.llmUnverifiedFields = [f for fields in checked.unverified for f in fields]
    llm_source = normalizer.make_source(
        checked.extraction.documentType,
        provider_text=checked.extraction.screenProvider.value if checked.extraction.screenProvider else None,
        document_id=document_id, file_name=file_name, ocr_score=ocr_score,
    )
    llm_txs = normalizer.from_llm(checked.extraction, source=llm_source, raw_text=raw_text,
                                  model_name=llm.model_name, user_id=user_id)
    llm_txs = [
        quality.evaluate(
            tx, ocr_score=ocr_score, min_ocr_score=settings.review_min_ocr_score,
            extra_reasons=[ReviewReason.LLM_EXTRACTED] + (
                [ReviewReason.LLM_UNVERIFIED_VALUE] if checked.unverified[i] else []
            ),
        )
        for i, tx in enumerate(llm_txs)
    ]
    llm_doc_reasons = quality.document_reasons(llm_txs, doc_type_confident=True)

    rule_bad = quality.badness(rule_txs, rule_doc_reasons)
    llm_bad = quality.badness(llm_txs, llm_doc_reasons)
    logger.info(f"규칙 점수={rule_bad}, LLM 점수={llm_bad} (낮을수록 좋음, force={force})")
    adopt = bool(llm_txs) if force else llm_bad < rule_bad
    if force and not llm_txs:
        info.llmError = "LLM 이 거래를 찾지 못해 규칙 파서 결과 사용"
    if adopt:
        info.method = "llm"
        info.llmUsed = True
        final.documentType = _SOURCE_TO_DOC[checked.extraction.documentType]
        final.transactions = llm_txs
        final.documentReviewReasons = sorted(llm_doc_reasons, key=lambda r: r.value)
    return final


def confirm_transactions(txs: List[Transaction]) -> List[Transaction]:
    """사용자가 확인/수정한 거래를 재정규화하고 확정 표시 (필수값이 비면 확정되지 않음)"""
    return [quality.evaluate(normalizer.sanitize(tx), confirmed=True) for tx in txs]
