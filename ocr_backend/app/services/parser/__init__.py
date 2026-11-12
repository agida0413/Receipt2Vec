"""
문서 유형 판별 + 유형별 파서 진입점
"""
__all__ = [
    "detect_document_type", "detect_document_type_scored", "normalize_rows",
    "parse_document", "parse_payment_app", "parse_receipt",
]
import re
from typing import List, Tuple

from app.models import DocumentType, PaymentAppData, ReceiptData
from app.services.parser.payment_app_parser import detect_provider, parse_payment_app
from app.services.parser.common import normalize_row
from app.services.parser.receipt_parser import parse_receipt

# 종이 영수증에만 주로 등장하는 표현
RECEIPT_MARKERS = [
    r"사업자", r"부가세", r"부가가치세", r"과세", r"면세", r"공급가", r"단가", r"수량",
    r"영수증", r"대표자?", r"카드번호", r"할부", r"일시불", r"POS", r"포스", r"계산원", r"가맹점번호",
]
# 결제앱 캡처 화면에 주로 등장하는 표현
APP_MARKERS = [
    r"결제상세", r"결제내역", r"이용내역", r"거래내역", r"페이머니", r"결제수단", r"결제완료",
    r"송금", r"충전", r"내역", r"혜택",
]


def detect_document_type_scored(rows: List[str]) -> Tuple[DocumentType, bool]:
    """
    (문서유형, 확신여부)
    카카오페이로 결제한 종이 영수증에도 '카카오페이' 글자가 찍히므로
    결제앱 이름만으로 판단하지 않고, 영수증 고유 표현이 2개 이상이면 영수증으로 본다.
    어느 쪽 표현도 충분하지 않으면 영수증으로 두되 확신 없음(False) → LLM 폴백 대상.
    """
    joined = "\n".join(rows)
    text = re.sub(r"\s+", "", joined)
    receipt_score = sum(1 for m in RECEIPT_MARKERS if re.search(m, text))
    app_score = sum(1 for m in APP_MARKERS if re.search(m, text))
    if detect_provider(joined):
        app_score += 1
    if receipt_score >= 2 and receipt_score >= app_score:
        return DocumentType.receipt, True
    if app_score >= 2:
        return DocumentType.payment_app, True
    return DocumentType.receipt, False


def detect_document_type(rows: List[str]) -> DocumentType:
    return detect_document_type_scored(rows)[0]


def normalize_rows(rows: List[str]) -> List[str]:
    return [r for r in (normalize_row(x) for x in rows) if r]


def parse_document(rows: List[str], doc_type: DocumentType) -> Tuple[DocumentType, ReceiptData | PaymentAppData]:
    rows = normalize_rows(rows)
    if doc_type == DocumentType.auto:
        doc_type = detect_document_type(rows)
    if doc_type == DocumentType.payment_app:
        return doc_type, parse_payment_app(rows)
    return DocumentType.receipt, parse_receipt(rows)
