"""
Normalizer: 파서별 출력 → 공통 Transaction

입력이 무엇이든(규칙 파서의 ReceiptData / PaymentAppData, LLM 추출 결과) 여기서
같은 enum, 같은 형식으로 맞춘다. 저장/검색 쪽은 이 결과만 알면 된다.
"""
import re
import uuid
from datetime import datetime
from typing import Iterable, List, Optional
from zoneinfo import ZoneInfo

from app.core.config import settings
from app.domain.transaction import (
    Amount, CardCompany, Item, Merchant, Payment, PaymentMethod, Provider, Raw, Source,
    SourceType, Transaction, TransactionStatus,
)
from app.models import PaymentAppData, ReceiptData
from app.services.parser import common as c

PARSER_VERSION = "1.0.0"

# ---------------------------------------------------------------------------
# 원문 표현 → enum 매핑 (규칙 파서/LLM/사용자 입력 모두 이 함수들을 거친다)
# ---------------------------------------------------------------------------
_PROVIDER_PATTERNS = [
    (Provider.KAKAOPAY, r"카카오\s*페이|kakao\s*pay|kakaopay|페이\s*머니"),
    (Provider.TOSSPAY, r"토스|toss"),
    (Provider.NAVERPAY, r"네이버\s*페이|naver\s*pay|naverpay|N\s*pay"),
    (Provider.PAYCO, r"페이코|payco"),
    (Provider.SAMSUNGPAY, r"삼성\s*페이|samsung\s*pay"),
    (Provider.APPLEPAY, r"애플\s*페이|apple\s*pay"),
]
_CARD_COMPANY_PATTERNS = [
    (CardCompany.SHINHAN, r"신한|shinhan"),
    (CardCompany.KB, r"KB|국민"),
    (CardCompany.SAMSUNG, r"삼성\s*카드|samsung\s*card"),
    (CardCompany.HYUNDAI, r"현대|hyundai"),
    (CardCompany.LOTTE, r"롯데|lotte"),
    (CardCompany.WOORI, r"우리|woori"),
    (CardCompany.HANA, r"하나|외환|hana"),
    (CardCompany.BC, r"\bBC\b|비씨"),
    (CardCompany.NH, r"\bNH\b|농협"),
    (CardCompany.CITI, r"씨티|citi"),
]


def _enum_value(enum_cls, value):
    """이미 enum 코드('KAKAOPAY')면 그대로 변환"""
    if value is None:
        return None
    if isinstance(value, enum_cls):
        return value
    try:
        return enum_cls(str(value).strip().upper())
    except ValueError:
        return None


def to_provider(text: Optional[str]) -> Optional[Provider]:
    if not text:
        return None
    direct = _enum_value(Provider, text)
    if direct:
        return direct
    for provider, pattern in _PROVIDER_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            return provider
    return None


def to_card_company(text: Optional[str]) -> Optional[CardCompany]:
    if not text:
        return None
    direct = _enum_value(CardCompany, text)
    if direct:
        return direct
    for company, pattern in _CARD_COMPANY_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            return company
    return None


def to_method(text: Optional[str], has_card: bool = False) -> PaymentMethod:
    direct = _enum_value(PaymentMethod, text)
    if direct:
        return direct
    t = text or ""
    if re.search(r"머니|money", t, re.IGNORECASE):
        return PaymentMethod.PAY_MONEY
    if re.search(r"포인트|point", t, re.IGNORECASE):
        return PaymentMethod.POINT
    if re.search(r"계좌|이체|은행|뱅크|bank", t, re.IGNORECASE):
        return PaymentMethod.BANK_TRANSFER
    if re.search(r"카드|신용|체크|card", t, re.IGNORECASE) or has_card or to_card_company(t):
        return PaymentMethod.CARD
    if re.search(r"현금|cash", t, re.IGNORECASE):
        return PaymentMethod.CASH
    return PaymentMethod.UNKNOWN


def to_status(text: Optional[str]) -> TransactionStatus:
    direct = _enum_value(TransactionStatus, text)
    if direct:
        return direct
    t = text or ""
    if "환불" in t:
        return TransactionStatus.REFUNDED
    if "취소" in t:
        return TransactionStatus.CANCELED
    if "실패" in t or "거절" in t:
        return TransactionStatus.FAILED
    return TransactionStatus.APPROVED


def to_card_last4(card_number: Optional[str]) -> Optional[str]:
    if not card_number:
        return None
    tail = re.sub(r"[^\d*]", "", card_number)[-4:]
    return tail if re.fullmatch(r"\d{4}", tail) else None


def to_installment(text: Optional[str]) -> Optional[int]:
    if text is None:
        return None
    if isinstance(text, int):
        return max(0, text)
    if "일시불" in text:
        return 0
    m = re.search(r"(\d{1,2})", text)
    return int(m.group(1)) if m else None


def to_biz_number(text: Optional[str]) -> Optional[str]:
    if not text:
        return None
    digits = re.sub(r"\D", "", text)
    return f"{digits[:3]}-{digits[3:5]}-{digits[5:]}" if len(digits) == 10 else None


def to_datetime(date_str: Optional[str], time_str: Optional[str]) -> Optional[str]:
    """'2025-03-12' + '14:23' → '2025-03-12T14:23:00+09:00'"""
    if not date_str:
        return None
    try:
        d = datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        return None
    if not time_str:
        return None
    parts = time_str.split(":")
    try:
        h, mi = int(parts[0]), int(parts[1])
        s = int(parts[2]) if len(parts) > 2 else 0
        dt = d.replace(hour=h, minute=mi, second=s, tzinfo=ZoneInfo(settings.timezone))
    except (ValueError, IndexError):
        return None
    return dt.isoformat()


def valid_date(date_str: Optional[str]) -> Optional[str]:
    if not date_str:
        return None
    try:
        return datetime.strptime(date_str[:10], "%Y-%m-%d").date().isoformat()
    except ValueError:
        return None


def positive(value: Optional[int]) -> Optional[int]:
    return abs(int(value)) if value is not None else None


_ITEM_NAME_NOISE_RE = re.compile(r"\*{2,}\d*|(?<![\d.])\d{8,14}(?![\d.])")


def clean_items(items: Iterable[Item]) -> List[Item]:
    """
    품목명에 붙은 바코드/마스킹 숫자 제거, 빈 이름 제외
    -1 ~ -9원은 실제 할인이 아니라 '가격 없음(-)' 표기를 OCR 이 '-1' 처럼 읽은 것 → 0원(가격 없음)으로
    """
    result = []
    for i in items:
        name = re.sub(r"\s+", " ", _ITEM_NAME_NOISE_RE.sub(" ", i.name or "")).strip()
        if not name:
            continue
        update = {"name": name}
        if i.amount is not None and -10 < i.amount < 0:
            update.update(amount=0, unitPrice=None)
        result.append(i.model_copy(update=update))
    return result


# ---------------------------------------------------------------------------
# 파서 출력 → Transaction
# ---------------------------------------------------------------------------
def _new_tx(source: Source, raw: Raw, user_id: Optional[str]) -> dict:
    return dict(transactionId=str(uuid.uuid4()), userId=user_id, source=source, raw=raw)


def from_receipt(
    data: ReceiptData, *, source: Source, raw_text: str, user_id: Optional[str] = None,
    parser_name: str = "rule:receipt",
) -> List[Transaction]:
    raw = Raw(text=raw_text, parserName=parser_name, parserVersion=PARSER_VERSION)
    card_last4 = to_card_last4(data.cardNumber)
    payment_text = " ".join(filter(None, [data.paymentMethod, data.cardCompany]))
    date_str = valid_date(data.date)

    extra = {}
    if data.subtotal is not None:
        extra["subtotal"] = data.subtotal
    if data.representative:
        extra["representative"] = data.representative
    if data.paymentMethod:
        extra["paymentMethodText"] = data.paymentMethod
    if data.totalInferred:
        extra["totalInferred"] = True

    tx = Transaction(
        **_new_tx(source, raw, user_id),
        merchant=Merchant(
            name=data.storeName, businessNumber=to_biz_number(data.businessNumber),
            address=data.address, phone=data.phone,
        ),
        transactedAt=to_datetime(date_str, data.time),
        transactedDate=date_str,
        status=to_status(None),
        amount=Amount(
            total=positive(data.totalAmount), supply=positive(data.supplyAmount),
            vat=positive(data.vat), discount=positive(data.discount),
        ),
        payment=Payment(
            method=to_method(data.paymentMethod, has_card=bool(data.cardNumber)),
            provider=to_provider(data.paymentMethod),
            cardCompany=to_card_company(payment_text) if (card_last4 or data.cardCompany) else None,
            cardLast4=card_last4,
            installmentMonths=to_installment(data.installment),
            approvalNumber=data.approvalNumber,
        ),
        items=clean_items(
            Item(name=i.name, quantity=i.quantity, unitPrice=i.unitPrice, amount=i.amount)
            for i in data.items
        ),
        extra=extra,
    )
    return [tx]


def from_payment_app(
    data: PaymentAppData, *, source: Source, raw_text: str, user_id: Optional[str] = None,
    parser_name: str = "rule:payment_app",
) -> List[Transaction]:
    raw = Raw(text=raw_text, parserName=parser_name, parserVersion=PARSER_VERSION)
    screen_provider = to_provider(data.provider)
    result: List[Transaction] = []
    for t in data.transactions:
        date_str = valid_date(t.date)
        status = to_status(t.status) if t.status else (
            TransactionStatus.CANCELED if t.isCancel else TransactionStatus.APPROVED
        )
        extra = {"viewType": data.viewType}
        if t.paymentMethod:
            extra["paymentMethodText"] = t.paymentMethod
        result.append(Transaction(
            **_new_tx(source, raw, user_id),
            merchant=Merchant(name=t.merchant),
            transactedAt=to_datetime(date_str, t.time),
            transactedDate=date_str,
            status=status,
            amount=Amount(total=positive(t.amount)),
            payment=Payment(
                method=to_method(t.paymentMethod),
                # 결제수단 문구에 다른 페이가 적혀 있지 않으면 캡처한 앱으로 간주
                provider=to_provider(t.paymentMethod) or screen_provider,
                cardCompany=to_card_company(t.paymentMethod),
                approvalNumber=t.approvalNumber,
            ),
            extra=extra,
        ))
    return result


def make_source(
    source_type: SourceType, *, provider_text: Optional[str], document_id: str,
    file_name: Optional[str], ocr_score: Optional[float],
) -> Source:
    return Source(
        type=source_type,
        provider=to_provider(provider_text) if source_type == SourceType.PAYMENT_APP else None,
        documentId=document_id,
        fileName=file_name,
        ocrScore=round(ocr_score, 4) if ocr_score is not None else None,
    )


def sanitize(tx: Transaction) -> Transaction:
    """
    사용자 수정값 등 외부에서 들어온 Transaction 을 다시 정규화.
    (금액 양수화, 카드번호 끝 4자리, 사업자번호 형식, 날짜/일시 정합성)
    """
    data = tx.model_copy(deep=True)
    a = data.amount
    a.total, a.supply, a.vat, a.discount = (positive(a.total), positive(a.supply),
                                            positive(a.vat), positive(a.discount))
    if data.payment.cardLast4:
        data.payment.cardLast4 = to_card_last4(data.payment.cardLast4)
    if data.merchant.businessNumber:
        data.merchant.businessNumber = to_biz_number(data.merchant.businessNumber)
    if data.merchant.name:
        data.merchant.name = data.merchant.name.strip() or None
    data.items = clean_items(data.items)

    edited_date = valid_date(data.transactedDate)
    if data.transactedAt:
        try:
            dt = datetime.fromisoformat(data.transactedAt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=ZoneInfo(settings.timezone))
            if edited_date and edited_date != dt.date().isoformat():
                # 사용자가 날짜만 고친 경우: 날짜는 수정값, 시간은 기존값 유지
                d = datetime.strptime(edited_date, "%Y-%m-%d")
                dt = dt.replace(year=d.year, month=d.month, day=d.day)
            data.transactedAt = dt.isoformat()
            edited_date = dt.date().isoformat()
        except ValueError:
            data.transactedAt = None
    data.transactedDate = edited_date
    return data


def from_llm(
    extraction, *, source: Source, raw_text: str, model_name: str, user_id: Optional[str] = None,
) -> List[Transaction]:
    """LlmExtraction(검증 완료) → Transaction"""
    raw = Raw(text=raw_text, parserName=f"llm:{model_name}", parserVersion=PARSER_VERSION)
    result: List[Transaction] = []
    for t in extraction.transactions:
        date_str = valid_date(t.date)
        result.append(Transaction(
            **_new_tx(source, raw, user_id),
            merchant=Merchant(
                name=(t.merchantName or "").strip() or None,
                businessNumber=to_biz_number(t.businessNumber),
                address=t.address, phone=t.phone,
            ),
            transactedAt=to_datetime(date_str, t.time),
            transactedDate=date_str,
            status=to_status(t.status),
            amount=Amount(
                total=positive(t.totalAmount), supply=positive(t.supplyAmount),
                vat=positive(t.vatAmount), discount=positive(t.discountAmount) or None,  # 할인 0 은 '없음' 으로 통일
            ),
            payment=Payment(
                method=to_method(t.paymentMethod),
                provider=to_provider(t.paymentProvider),
                cardCompany=to_card_company(t.cardCompany),
                cardLast4=to_card_last4(t.cardLast4),
                installmentMonths=to_installment(t.installmentMonths),
                approvalNumber=t.approvalNumber,
            ),
            items=clean_items(
                Item(name=i.name, quantity=i.quantity, unitPrice=i.unitPrice, amount=i.amount)
                for i in t.items
            ),
        ))
    return result
