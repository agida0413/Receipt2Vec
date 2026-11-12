"""
LLM(Gemini) 구조화 출력 스키마

Gemini 의 response_schema 로 그대로 전달되므로 단순하게 유지한다.
- 모든 필드를 '필수 + null 허용' 으로 둬서 모델이 모르는 값은 null 로 명시하게 함
- enum 은 공통 Transaction 의 enum 을 그대로 사용 → 정규화 단계에서 재검증
"""
from typing import List, Optional

from pydantic import BaseModel, Field

from app.domain.transaction import (
    CardCompany, PaymentMethod, Provider, SourceType, TransactionStatus,
)


class LlmItem(BaseModel):
    name: str
    quantity: Optional[int]
    unitPrice: Optional[int]
    amount: int = Field(..., description="품목 금액. 할인 줄은 음수")


class LlmTransaction(BaseModel):
    merchantName: Optional[str]
    businessNumber: Optional[str]
    address: Optional[str]
    phone: Optional[str]
    date: Optional[str] = Field(..., description="YYYY-MM-DD")
    time: Optional[str] = Field(..., description="HH:MM 또는 HH:MM:SS (24시간)")
    status: TransactionStatus
    totalAmount: Optional[int] = Field(..., description="최종 결제 금액(양수)")
    supplyAmount: Optional[int]
    vatAmount: Optional[int]
    discountAmount: Optional[int]
    paymentMethod: PaymentMethod
    paymentProvider: Optional[Provider]
    cardCompany: Optional[CardCompany]
    cardLast4: Optional[str]
    installmentMonths: Optional[int] = Field(..., description="0 = 일시불")
    approvalNumber: Optional[str]
    items: List[LlmItem]


class LlmExtraction(BaseModel):
    documentType: SourceType
    screenProvider: Optional[Provider] = Field(..., description="결제앱 캡처일 때 어떤 앱 화면인지")
    transactions: List[LlmTransaction]
