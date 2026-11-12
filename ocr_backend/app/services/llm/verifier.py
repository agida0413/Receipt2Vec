"""
LLM 환각/오배치 검증

LLM 은 두 종류의 실수를 한다.
  1) 원문에 없는 값을 만들어냄            → '원문에 그 숫자가 있는가' 로 거름
  2) 원문에 있는 숫자를 엉뚱한 필드에 넣음 → '그 숫자와 같은 줄에 맞는 라벨이 있는가' 로 거름
     (예: POS 거래번호를 승인번호로, 면세물품가액을 부가세로, 가맹점명의 '농협'을 카드사로)

가계부 데이터에서 숫자가 틀리면 치명적이므로, 근거가 없는 값은 버리고 사용자 확인으로 넘긴다.
(가맹점명 오타 교정 같은 텍스트 보정은 허용 — 사용자 확인 단계에서 걸러짐)
"""
import re
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Set

from app.services.llm.schema import LlmExtraction, LlmTransaction
from app.services.parser.common import CARD_NO_RE, is_card_number

# 필드별: 값이 있는 줄에 반드시 있어야 하는 라벨 / 있으면 안 되는 표현 (공백 제거 후 매칭)
VAT_LABEL = re.compile(r"부가세|부가가치세|세액|VAT", re.IGNORECASE)
# OCR 이 '물품' 을 '울품/몰품' 으로 읽는 경우가 있어 '.품가액' 으로 매칭
VAT_EXCLUDE = re.compile(r".품가액|과세.품|면세.품|공급가")
SUPPLY_LABEL = re.compile(r"공급가|과세.품|과세금액|.품가액|과세가액")
SUPPLY_EXCLUDE = re.compile(r"면세")
DISCOUNT_LABEL = re.compile(r"할인|쿠폰|에누리")
APPROVAL_LABEL = re.compile(r"승인")
BIZ_LABEL = re.compile(r"사업자|등록번호|\d{3}-\d{2}-\d{5}")
CARD_CONTEXT = re.compile(r"카드|신용|체크|매입|발급|승인|CARD", re.IGNORECASE)
# 카드사 이름은 가맹점명에도 흔히 들어가므로(예: 농협하나로마트) '카드' 문맥이 있는 줄에서만 인정
CARD_COMPANY_WORDS = {
    "SHINHAN": r"신한", "KB": r"KB|국민", "SAMSUNG": r"삼성", "HYUNDAI": r"현대", "LOTTE": r"롯데",
    "WOORI": r"우리", "HANA": r"하나|외환", "BC": r"BC|비씨", "NH": r"NH|농협", "CITI": r"씨티|CITI",
}


def _compact(s: str) -> str:
    return re.sub(r"\s+", "", s)


def _line_numbers(line: str) -> Set[int]:
    """줄 안의 숫자 (천 단위 구분자 제거, 구분자로 나뉜 조각도 포함)"""
    numbers: Set[int] = set()
    for m in re.finditer(r"\d[\d,.]*\d|\d", line):
        token = m.group(0)
        joined = re.sub(r"[,.]", "", token)
        if len(joined) <= 12:
            numbers.add(int(joined))
        for piece in re.findall(r"\d+", token):
            if len(piece) <= 12:
                numbers.add(int(piece))
    return numbers


@dataclass
class TextEvidence:
    lines: List[str]
    numbers: Set[int]
    digits: str

    @classmethod
    def from_text(cls, text: str) -> "TextEvidence":
        lines = [l for l in text.splitlines() if l.strip()]
        numbers: Set[int] = set()
        for line in lines:
            numbers |= _line_numbers(line)
        return cls(lines=lines, numbers=numbers, digits=re.sub(r"\D", "", text))

    # ----------------------------------------------------------------- 존재 확인
    def has_number(self, value: int) -> bool:
        return abs(value) in self.numbers

    def has_digits(self, value: str) -> bool:
        d = re.sub(r"\D", "", value)
        return len(d) >= 4 and d in self.digits

    def has_date(self, date_str: str) -> bool:
        try:
            _, m, d = (int(x) for x in date_str.split("-"))
        except ValueError:
            return False
        return any(p in self.digits for p in (f"{m:02d}{d:02d}", f"{m}{d:02d}", f"{m}{d}"))

    # ----------------------------------------------------------------- 같은 줄 라벨 확인
    def number_on_labeled_line(
        self, value: int, label: re.Pattern, exclude: Optional[re.Pattern] = None,
    ) -> bool:
        """value 가 들어 있는 줄 중 label 이 있고 exclude 가 없는 줄이 있는가 (라벨 다음 줄 값 허용)"""
        for i, line in enumerate(self.lines):
            if abs(value) not in _line_numbers(line):
                continue
            for cand in (line, self.lines[i - 1] if i > 0 else ""):
                c = _compact(cand)
                if label.search(c) and not (exclude and exclude.search(c)):
                    return True
        return False

    def digits_on_labeled_line(self, value: str, label: re.Pattern) -> bool:
        d = re.sub(r"\D", "", value)
        if len(d) < 4:
            return False
        for i, line in enumerate(self.lines):
            if d not in re.sub(r"\D", "", line):
                continue
            for cand in (line, self.lines[i - 1] if i > 0 else ""):
                if label.search(_compact(cand)):
                    return True
        return False

    def card_last4_supported(self, last4: str) -> bool:
        """원문에 실제 카드번호(구분자/마스킹 형태)가 있고 그 끝 4자리가 일치하는가"""
        for line in self.lines:
            for m in CARD_NO_RE.finditer(line):
                if is_card_number(m.group(0)) and re.sub(r"\D", "", m.group(0)).endswith(last4):
                    return True
        return False

    def card_company_supported(self, company: str) -> bool:
        pattern = CARD_COMPANY_WORDS.get(company)
        if not pattern:
            return False
        rx = re.compile(pattern, re.IGNORECASE)
        return any(rx.search(l) and CARD_CONTEXT.search(l) for l in self.lines)

    def installment_supported(self, months: int) -> bool:
        text = _compact("\n".join(self.lines))
        if months == 0:
            return "일시불" in text
        return bool(re.search(rf"(?<!\d){months}개월|할부", text))


@dataclass
class VerifyResult:
    extraction: LlmExtraction
    unverified: List[List[str]] = field(default_factory=list)  # 거래별로 버린 필드 이름


def _drop_if(tx: LlmTransaction, name: str, bad: Callable[[object], bool], dropped: List[str]) -> None:
    value = getattr(tx, name)
    if value is not None and bad(value):
        setattr(tx, name, None)
        dropped.append(name)


def _verify_tx(tx: LlmTransaction, ev: TextEvidence) -> List[str]:
    dropped: List[str] = []

    # 1) 존재 확인 — 총액은 결제앱 화면처럼 라벨 없이 '5,600원' 만 있는 경우가 많아 존재만 확인
    _drop_if(tx, "totalAmount", lambda v: not ev.has_number(v), dropped)

    # 2) 같은 줄 라벨 확인
    _drop_if(tx, "vatAmount", lambda v: not ev.number_on_labeled_line(v, VAT_LABEL, VAT_EXCLUDE), dropped)
    _drop_if(tx, "supplyAmount",
             lambda v: not ev.number_on_labeled_line(v, SUPPLY_LABEL, SUPPLY_EXCLUDE), dropped)
    _drop_if(tx, "discountAmount",
             lambda v: v != 0 and not ev.number_on_labeled_line(v, DISCOUNT_LABEL), dropped)
    _drop_if(tx, "approvalNumber", lambda v: not ev.digits_on_labeled_line(v, APPROVAL_LABEL), dropped)
    _drop_if(tx, "businessNumber", lambda v: not ev.digits_on_labeled_line(v, BIZ_LABEL), dropped)
    _drop_if(tx, "cardLast4", lambda v: not ev.card_last4_supported(re.sub(r"\D", "", v)), dropped)
    _drop_if(tx, "cardCompany",
             lambda v: not ev.card_company_supported(getattr(v, "value", v)), dropped)
    _drop_if(tx, "installmentMonths", lambda v: not ev.installment_supported(int(v)), dropped)

    if tx.date and not ev.has_date(tx.date):
        tx.date = None
        tx.time = None
        dropped.append("date")

    kept = []
    for item in tx.items:
        if not ev.has_number(item.amount):
            dropped.append(f"items[{item.name}]")
            continue
        if item.unitPrice is not None and not ev.has_number(item.unitPrice):
            item.unitPrice = None
        kept.append(item)
    tx.items = kept
    return dropped


def verify(extraction: LlmExtraction, raw_text: str) -> VerifyResult:
    ev = TextEvidence.from_text(raw_text)
    cleaned = extraction.model_copy(deep=True)
    unverified = [_verify_tx(tx, ev) for tx in cleaned.transactions]
    return VerifyResult(extraction=cleaned, unverified=unverified)
