"""
공통 거래(Transaction) 스키마 — OCR 백엔드와 RAG 백엔드 사이의 계약

- 거래 1건 = OpenSearch 문서 1개 (영수증 1장 = 1건, 결제내역 목록 캡처 1장 = N건)
- 값은 enum 코드로 정규화 (원문 표현은 extra / raw 에 보존)
- 금액은 항상 양수, 취소/환불은 status 로 표현
- embedding / dedupKey / merchant.category / createdAt 은 저장 시 RAG 백엔드가 채움
"""
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

SCHEMA_VERSION = 1


class SourceType(str, Enum):
    RECEIPT = "RECEIPT"
    PAYMENT_APP = "PAYMENT_APP"


class Provider(str, Enum):
    KAKAOPAY = "KAKAOPAY"
    TOSSPAY = "TOSSPAY"
    NAVERPAY = "NAVERPAY"
    PAYCO = "PAYCO"
    SAMSUNGPAY = "SAMSUNGPAY"
    APPLEPAY = "APPLEPAY"


class PaymentMethod(str, Enum):
    CARD = "CARD"
    CASH = "CASH"
    PAY_MONEY = "PAY_MONEY"            # 카카오페이머니, 토스머니 등 충전 잔액
    BANK_TRANSFER = "BANK_TRANSFER"    # 계좌 결제/이체
    POINT = "POINT"
    UNKNOWN = "UNKNOWN"


class CardCompany(str, Enum):
    SHINHAN = "SHINHAN"
    KB = "KB"
    SAMSUNG = "SAMSUNG"
    HYUNDAI = "HYUNDAI"
    LOTTE = "LOTTE"
    WOORI = "WOORI"
    HANA = "HANA"
    BC = "BC"
    NH = "NH"
    CITI = "CITI"
    OTHER = "OTHER"


class TransactionStatus(str, Enum):
    APPROVED = "APPROVED"
    CANCELED = "CANCELED"
    REFUNDED = "REFUNDED"
    FAILED = "FAILED"


class ReviewReason(str, Enum):
    """사용자 확인이 필요한 이유 (프론트에서 해당 필드를 강조 표시하는 용도)"""
    NO_TRANSACTIONS = "NO_TRANSACTIONS"            # 거래를 하나도 찾지 못함
    UNSURE_DOC_TYPE = "UNSURE_DOC_TYPE"            # 영수증/결제앱 판별이 불확실
    MISSING_MERCHANT = "MISSING_MERCHANT"
    MISSING_DATE = "MISSING_DATE"
    MISSING_AMOUNT = "MISSING_AMOUNT"
    AMOUNT_INFERRED = "AMOUNT_INFERRED"            # 총액 표기를 못 찾아 추정한 값
    ITEMS_SUM_MISMATCH = "ITEMS_SUM_MISMATCH"      # 품목 합계(-할인) ≠ 총액
    LOW_OCR_SCORE = "LOW_OCR_SCORE"                # OCR 인식 신뢰도 낮음
    LLM_EXTRACTED = "LLM_EXTRACTED"                # LLM 이 추출한 값 (원문 대조 필요)
    LLM_UNVERIFIED_VALUE = "LLM_UNVERIFIED_VALUE"  # LLM 값이 원문에 없어 버림


# 규칙 파서 결과에 이 사유가 있으면 LLM 추출을 시도
LLM_TRIGGER_REASONS = {
    ReviewReason.NO_TRANSACTIONS,
    ReviewReason.UNSURE_DOC_TYPE,
    ReviewReason.MISSING_MERCHANT,
    ReviewReason.MISSING_DATE,
    ReviewReason.MISSING_AMOUNT,
    ReviewReason.AMOUNT_INFERRED,
    ReviewReason.ITEMS_SUM_MISMATCH,
}

# 저장(확정) 전에 반드시 채워져 있어야 하는 필드
REQUIRED_REASONS = {
    ReviewReason.MISSING_MERCHANT,
    ReviewReason.MISSING_DATE,
    ReviewReason.MISSING_AMOUNT,
}


class Source(BaseModel):
    type: SourceType
    provider: Optional[Provider] = Field(None, description="결제앱 캡처일 때 어떤 앱 화면인지")
    documentId: str = Field(..., description="업로드 이미지 1장 단위 ID (목록 캡처면 여러 거래가 공유)")
    fileName: Optional[str] = None
    ocrScore: Optional[float] = Field(None, description="OCR 평균 신뢰도(0~1)")


class Merchant(BaseModel):
    name: Optional[str] = None
    businessNumber: Optional[str] = Field(None, description="000-00-00000")
    address: Optional[str] = None
    phone: Optional[str] = None


class Amount(BaseModel):
    total: Optional[int] = Field(None, description="최종 결제 금액(양수)")
    currency: str = "KRW"
    supply: Optional[int] = None
    vat: Optional[int] = None
    discount: Optional[int] = None


class Payment(BaseModel):
    method: PaymentMethod = PaymentMethod.UNKNOWN
    provider: Optional[Provider] = Field(None, description="거친 간편결제 (카카오페이에 연결된 카드 결제 등)")
    cardCompany: Optional[CardCompany] = None
    cardLast4: Optional[str] = Field(None, description="카드번호 끝 4자리만 (전체 번호는 저장하지 않음)")
    installmentMonths: Optional[int] = Field(None, description="0 = 일시불")
    approvalNumber: Optional[str] = None


class Item(BaseModel):
    name: str
    quantity: Optional[int] = None
    unitPrice: Optional[int] = None
    amount: int = Field(..., description="품목 금액 (할인 줄은 음수)")


class Quality(BaseModel):
    needsReview: bool = False
    reviewReasons: List[ReviewReason] = []
    missingFields: List[str] = []
    itemsSumMatchesTotal: Optional[bool] = None
    confirmed: bool = Field(False, description="사용자가 확인/수정 완료했는지 (/confirm 통과 시 true)")


class Raw(BaseModel):
    text: str = Field(..., description="OCR 원문 (카드번호 마스킹됨)")
    parserName: str = Field(..., description="rule:receipt / rule:payment_app / llm:<model>")
    parserVersion: str


class Transaction(BaseModel):
    schemaVersion: int = SCHEMA_VERSION
    transactionId: str
    userId: Optional[str] = None
    source: Source
    merchant: Merchant = Merchant()
    transactedAt: Optional[str] = Field(None, description="ISO 8601, 타임존 포함 (2025-03-12T14:23:11+09:00)")
    transactedDate: Optional[str] = Field(None, description="YYYY-MM-DD (시간을 모를 때도 채움)")
    status: TransactionStatus = TransactionStatus.APPROVED
    amount: Amount = Amount()
    payment: Payment = Payment()
    items: List[Item] = []
    quality: Quality = Quality()
    extra: Dict[str, Any] = Field(default_factory=dict, description="소스별 고유 정보 (RAG 에서 flat_object)")
    raw: Raw
