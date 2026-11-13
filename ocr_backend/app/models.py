"""
API 요청/응답 모델 (Pydantic)

금액은 모두 원 단위 정수. 인식하지 못한 필드는 None.
날짜는 'YYYY-MM-DD', 시간은 'HH:MM[:SS]', datetime 은 ISO 형식('YYYY-MM-DDTHH:MM:SS').
"""
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field

from app.domain.transaction import ReviewReason, Transaction


class DocumentType(str, Enum):
    auto = "auto"
    receipt = "receipt"            # 종이 영수증 / 카드전표
    payment_app = "payment_app"    # 카카오페이 등 결제앱 결제내역 캡처


# ============================================================================
# OCR 원본
# ============================================================================
class OcrLineOut(BaseModel):
    text: str
    score: float = Field(..., description="인식 신뢰도(0~1)")
    box: List[int] = Field(..., description="[x1, y1, x2, y2] (전처리 후 이미지 기준)")


# ============================================================================
# 영수증
# ============================================================================
class ReceiptItem(BaseModel):
    name: str
    quantity: Optional[int] = None
    unitPrice: Optional[int] = None
    amount: int = Field(..., description="품목 금액(할인 줄이면 음수)")


class ReceiptData(BaseModel):
    storeName: Optional[str] = None
    businessNumber: Optional[str] = Field(None, description="사업자등록번호 000-00-00000")
    representative: Optional[str] = None
    phone: Optional[str] = None
    address: Optional[str] = None
    date: Optional[str] = None
    time: Optional[str] = None
    datetime: Optional[str] = None
    items: List[ReceiptItem] = []
    subtotal: Optional[int] = None
    discount: Optional[int] = None
    supplyAmount: Optional[int] = Field(None, description="공급가액/과세물품가액")
    vat: Optional[int] = Field(None, description="부가세")
    totalAmount: Optional[int] = Field(None, description="최종 결제 금액")
    paymentMethod: Optional[str] = None
    cardCompany: Optional[str] = None
    cardNumber: Optional[str] = Field(None, description="마스킹된 카드번호(인쇄된 그대로)")
    installment: Optional[str] = None
    approvalNumber: Optional[str] = None
    totalInferred: bool = Field(False, description="총액 키워드를 못 찾아 추정한 값인지")
    itemsSumMatchesTotal: Optional[bool] = Field(
        None, description="품목 합계(-할인)가 총액과 일치하는지. 파싱 품질 점검용"
    )


# ============================================================================
# 결제앱 결제내역
# ============================================================================
class PaymentTransaction(BaseModel):
    merchant: Optional[str] = None
    amount: Optional[int] = Field(None, description="결제 금액(양수)")
    isCancel: bool = False
    status: Optional[str] = Field(None, description="완료/취소/환불/실패")
    date: Optional[str] = None
    time: Optional[str] = None
    datetime: Optional[str] = None
    paymentMethod: Optional[str] = None
    approvalNumber: Optional[str] = None


class PaymentAppData(BaseModel):
    provider: Optional[str] = Field(None, description="kakaopay/naverpay/tosspay/payco")
    viewType: str = Field(..., description="detail(상세 1건) / list(목록)")
    transactions: List[PaymentTransaction] = []
    totalAmount: Optional[int] = Field(None, description="취소 제외 결제 합계")


# ============================================================================
# 추출 과정 정보 (규칙/LLM 중 무엇을 썼는지)
# ============================================================================
class ExtractionInfo(BaseModel):
    method: str = Field(..., description="최종 채택: rule / llm")
    ruleParser: str
    llmTriggerReasons: List[ReviewReason] = Field([], description="규칙 파서 결과에서 LLM 폴백을 일으킨 사유")
    llmForced: bool = Field(False, description="LLM 무조건 사용 모드였는지 (force_llm / LLM_MODE=always)")
    llmAttempted: bool = False
    llmUsed: bool = False
    llmModel: Optional[str] = None
    llmSkippedReason: Optional[str] = None
    llmError: Optional[str] = None
    llmUnverifiedFields: List[str] = Field([], description="원문에 없어서 버린 LLM 값")


# ============================================================================
# 응답
# ============================================================================
class OcrResponse(BaseModel):
    userId: Optional[str] = None
    fileName: Optional[str] = None
    documentType: DocumentType
    imageWidth: int
    imageHeight: int
    elapsedMs: int
    ocrScore: Optional[float] = Field(None, description="OCR 평균 신뢰도")
    needsReview: bool = Field(..., description="사용자 확인이 필요한 거래가 하나라도 있는지")
    documentReviewReasons: List[ReviewReason] = []
    transactions: List[Transaction] = Field(..., description="공통 거래 형식 (저장 대상)")
    extraction: ExtractionInfo
    rawText: str = Field(..., description="줄 단위로 정렬한 전체 텍스트 (카드번호 마스킹)")
    lines: List[OcrLineOut]
    receipt: Optional[ReceiptData] = Field(None, description="규칙 파서 원본 결과 (디버깅용)")
    paymentApp: Optional[PaymentAppData] = Field(None, description="규칙 파서 원본 결과 (디버깅용)")


class ParseTextRequest(BaseModel):
    text: str = Field(..., description="줄바꿈으로 구분된 OCR 텍스트")
    documentType: DocumentType = DocumentType.auto
    useLlm: bool = True
    forceLlm: Optional[bool] = Field(None, description="true 면 LLM 무조건 사용 (미지정 시 LLM_MODE=always 여부)")
    userId: Optional[str] = None


class ParseTextResponse(BaseModel):
    documentType: DocumentType
    needsReview: bool
    documentReviewReasons: List[ReviewReason] = []
    transactions: List[Transaction]
    extraction: ExtractionInfo
    receipt: Optional[ReceiptData] = None
    paymentApp: Optional[PaymentAppData] = None


class ConfirmRequest(BaseModel):
    transactions: List[Transaction] = Field(..., description="사용자가 확인/수정한 거래 (recognize 응답 형식 그대로)")


class ConfirmError(BaseModel):
    index: int
    transactionId: str
    missingFields: List[str]


class ConfirmResponse(BaseModel):
    valid: bool = Field(..., description="모든 거래가 저장 가능한 상태인지")
    transactions: List[Transaction]
    errors: List[ConfirmError] = []


class SaveTransactionsRequest(BaseModel):
    userId: str = Field(..., min_length=1)
    transactions: List[Transaction] = Field(..., description="사용자가 확인/수정한 거래")


class SaveTransactionsResponse(BaseModel):
    saved: List[str] = Field(..., description="저장된 transactionId")
    duplicates: List[str] = Field([], description="이미 저장된 거래라 건너뛴 transactionId")
