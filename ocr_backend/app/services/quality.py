"""
품질 평가

- LLM 폴백 여부 판단: 규칙 파서 결과에 LLM_TRIGGER_REASONS 가 있으면 LLM 시도
- 사용자 확인 여부 판단: reviewReasons 가 하나라도 있으면 needsReview=True
"""
from typing import Iterable, List, Optional, Set

from app.domain.transaction import (
    LLM_TRIGGER_REASONS, REQUIRED_REASONS, Quality, ReviewReason, Transaction,
)


def items_sum_matches(tx: Transaction) -> Optional[bool]:
    if not tx.items or tx.amount.total is None or tx.extra.get("totalInferred"):
        return None
    items_sum = sum(i.amount for i in tx.items)
    if items_sum == tx.amount.total:
        return True
    # 품목에 할인 줄이 없고 할인 총액만 따로 찍힌 영수증
    if tx.amount.discount and items_sum - tx.amount.discount == tx.amount.total:
        return True
    return False


def evaluate(
    tx: Transaction,
    *,
    ocr_score: Optional[float] = None,
    min_ocr_score: float = 0.0,
    extra_reasons: Iterable[ReviewReason] = (),
    confirmed: bool = False,
) -> Transaction:
    """tx.quality 를 다시 계산해서 채운 사본을 반환"""
    reasons: List[ReviewReason] = []
    missing: List[str] = []

    if not tx.merchant.name:
        reasons.append(ReviewReason.MISSING_MERCHANT)
        missing.append("merchant.name")
    if not tx.transactedDate:
        reasons.append(ReviewReason.MISSING_DATE)
        missing.append("transactedDate")
    if not tx.amount.total:
        reasons.append(ReviewReason.MISSING_AMOUNT)
        missing.append("amount.total")
    if not tx.transactedAt and tx.transactedDate:
        missing.append("transactedAt")  # 시간 누락은 확인 사유로 보지 않음 (결제앱 목록 등)

    if tx.amount.total and tx.extra.get("totalInferred"):
        reasons.append(ReviewReason.AMOUNT_INFERRED)

    match = items_sum_matches(tx)
    if match is False:
        reasons.append(ReviewReason.ITEMS_SUM_MISMATCH)
    if ocr_score is not None and ocr_score < min_ocr_score:
        reasons.append(ReviewReason.LOW_OCR_SCORE)
    for r in extra_reasons:
        if r not in reasons:
            reasons.append(r)

    if confirmed:
        # 사용자가 확인했으면 '확인해 달라'는 사유는 해소, 필수값 누락만 남김
        reasons = [r for r in reasons if r in REQUIRED_REASONS]

    out = tx.model_copy(deep=True)
    out.quality = Quality(
        needsReview=bool(reasons),
        reviewReasons=reasons,
        missingFields=missing,
        itemsSumMatchesTotal=match,
        confirmed=confirmed and not reasons,
    )
    return out


def document_reasons(txs: List[Transaction], doc_type_confident: bool) -> Set[ReviewReason]:
    """문서 단위 사유 (거래가 없거나, 문서 유형 판별이 불확실)"""
    reasons: Set[ReviewReason] = set()
    if not txs:
        reasons.add(ReviewReason.NO_TRANSACTIONS)
    if not doc_type_confident:
        reasons.add(ReviewReason.UNSURE_DOC_TYPE)
    return reasons


def trigger_reasons(txs: List[Transaction], doc_reasons: Set[ReviewReason]) -> Set[ReviewReason]:
    """LLM 폴백을 일으키는 사유 모음"""
    reasons = {r for r in doc_reasons if r in LLM_TRIGGER_REASONS}
    for tx in txs:
        reasons.update(r for r in tx.quality.reviewReasons if r in LLM_TRIGGER_REASONS)
    return reasons


def badness(txs: List[Transaction], doc_reasons: Set[ReviewReason]) -> int:
    """결과 비교용 점수 (낮을수록 좋음). 거래 0건은 큰 감점"""
    score = 100 if not txs else 0
    score += sum(10 for r in doc_reasons if r == ReviewReason.UNSURE_DOC_TYPE)
    for tx in txs:
        for r in tx.quality.reviewReasons:
            if r in REQUIRED_REASONS:
                score += 5
            elif r in LLM_TRIGGER_REASONS:
                score += 3
            else:
                score += 1
    return score
