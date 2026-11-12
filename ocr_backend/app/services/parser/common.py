"""
파서 공통 유틸: 금액/날짜/시간/번호 추출

OCR 특성상 '12,000' 이 '12.000' 으로 읽히는 일이 잦다. 한국 원화 금액에는 소수점이 없으므로
'숫자 1~3자리 + (콤마|점 + 숫자 3자리) 반복' 은 천 단위 구분 금액으로 본다.
"""
import re
from dataclasses import dataclass
from datetime import date
from typing import List, Optional, Tuple

# ---------------------------------------------------------------------------
# 번호류 (금액으로 오인되지 않도록 금액 추출 전에 지운다)
# ---------------------------------------------------------------------------
BIZ_NO_RE = re.compile(r"(?<!\d)(\d{3})\s?-\s?(\d{2})\s?-\s?(\d{5})(?!\d)")
PHONE_RE = re.compile(
    r"(?<![\d*-])(0\d{1,2}|1\d{3})\s?[-)]\s?(\d{3,4})\s?-\s?(\d{4})(?!\d)(?!\s?-\s?[\d*])"
)
CARD_NO_RE = re.compile(r"(?<![\d*])[\d*]{4}[-\s]?[\d*]{2,4}[-\s]?[\d*]{2,4}[-\s]?[\d*]{3,4}(?![\d*])")
APPROVAL_RE = re.compile(r"승\s*인\s*번\s*호\s*[:：]?\s*(\d{6,12})")

DATE_RE = re.compile(
    r"(?<!\d)(?P<y>20\d{2}|\d{2})\s*[.\-/년]\s*(?P<m>\d{1,2})\s*[.\-/월]\s*(?P<d>\d{1,2})(?!\d)\s*일?"
)
MONTH_DAY_RE = re.compile(r"(?<!\d)(?P<m>\d{1,2})\s*월\s*(?P<d>\d{1,2})\s*일")
TIME_RE = re.compile(
    r"(?P<ampm>오전|오후|AM|PM|am|pm)?\s*(?<!\d)(?P<h>[01]?\d|2[0-3])\s*:\s*(?P<mi>[0-5]\d)"
    r"(?:\s*:\s*(?P<s>[0-5]\d))?(?!\d)"
)

AMOUNT_RE = re.compile(
    r"(?<![\d.,:])(?P<neg>-\s?)?(?P<num>\d{1,3}(?:[,.]\d{3})+|\d+)(?![\d:])(?:\s*(?P<won>원|₩))?"
)

HANGUL_OR_ALPHA_RE = re.compile(r"[가-힣A-Za-z]")


@dataclass
class Amount:
    value: int
    has_won: bool
    span: Tuple[int, int]


_DATE_TIME_GLUED_RE = re.compile(r"((?:20)?\d{2}[.\-/]\d{1,2}[.\-/]\d{2})(\d{1,2}:\d{2})")


def normalize_row(text: str) -> str:
    """OCR 이 자주 내는 붙음/오인식 보정 (파싱 전 1회 적용)"""
    text = text.replace("：", ":")
    text = _DATE_TIME_GLUED_RE.sub(r"\1 \2", text)        # 2025-03-1214:23 → 2025-03-12 14:23
    text = re.sub(r"(?<=[\d*])[_~](?=[\d*])", "-", text)  # 5310-12**_**** → 5310-12**-****
    return text.strip()


def is_card_number(raw: str) -> bool:
    """
    CARD_NO_RE 후보가 실제 카드번호인지.
    상품 바코드(EAN-13 등, 구분자 없는 8~14자리)를 카드번호로 오인하지 않도록
    구분자(-, 공백)로 나뉘어 있거나 정확히 16자리일 때만 인정한다.
    """
    body = re.sub(r"[-\s]", "", raw)
    has_separator = body != raw
    if not has_separator and len(body) != 16:
        return False
    return "*" in body or len(re.sub(r"\D", "", body)) >= 12


def mask_card_numbers(text: str) -> str:
    """카드번호는 끝 4자리만 남기고 마스킹 (LLM 전송 / 저장 전)"""
    def repl(m: re.Match) -> str:
        raw = m.group(0)
        if not is_card_number(raw):
            return raw  # 바코드 등 카드번호가 아닌 숫자열
        keep = len(raw) - 4
        return re.sub(r"\d", "*", raw[:keep]) + raw[keep:]
    return CARD_NO_RE.sub(repl, text)


def _mask(text: str, pattern: re.Pattern) -> str:
    return pattern.sub(lambda m: " " * (m.end() - m.start()), text)


def strip_non_amount_numbers(text: str) -> str:
    """날짜/시간/전화/사업자/카드/승인번호 등 금액이 아닌 숫자를 공백으로 치환(위치 유지)"""
    for pattern in (DATE_RE, TIME_RE, BIZ_NO_RE, PHONE_RE, CARD_NO_RE, APPROVAL_RE, MONTH_DAY_RE):
        text = _mask(text, pattern)
    return text


def extract_amounts(text: str) -> List[Amount]:
    cleaned = strip_non_amount_numbers(text)
    amounts: List[Amount] = []
    for m in AMOUNT_RE.finditer(cleaned):
        digits = re.sub(r"[,.]", "", m.group("num"))
        if len(digits) > 10:  # 바코드 등
            continue
        value = int(digits)
        if m.group("neg"):
            value = -value
        amounts.append(Amount(value=value, has_won=bool(m.group("won")), span=m.span()))
    return amounts


def last_amount(text: str, min_abs: int = 1) -> Optional[int]:
    amounts = [a for a in extract_amounts(text) if abs(a.value) >= min_abs]
    return amounts[-1].value if amounts else None


def parse_date(text: str, default_year: Optional[int] = None) -> Optional[str]:
    """첫 번째로 발견된 날짜를 'YYYY-MM-DD' 로. (월/일만 있으면 default_year 사용)"""
    for m in DATE_RE.finditer(text):
        y = int(m.group("y"))
        if y < 100:
            y += 2000
        result = _safe_date(y, int(m.group("m")), int(m.group("d")))
        if result:
            return result
    m = MONTH_DAY_RE.search(text)
    if m and default_year:
        return _safe_date(default_year, int(m.group("m")), int(m.group("d")))
    return None


def _safe_date(y: int, m: int, d: int) -> Optional[str]:
    try:
        return date(y, m, d).isoformat()
    except ValueError:
        return None


def parse_time(text: str) -> Optional[str]:
    """첫 번째로 발견된 시간을 'HH:MM' 또는 'HH:MM:SS' 로. 오전/오후 반영"""
    m = TIME_RE.search(text)
    if not m:
        return None
    h, mi, s = int(m.group("h")), int(m.group("mi")), m.group("s")
    ampm = (m.group("ampm") or "").lower()
    if ampm in ("오후", "pm") and h < 12:
        h += 12
    elif ampm in ("오전", "am") and h == 12:
        h = 0
    return f"{h:02d}:{mi:02d}" + (f":{s}" if s else "")


def combine_datetime(d: Optional[str], t: Optional[str]) -> Optional[str]:
    if not d:
        return None
    if not t:
        return d
    return f"{d}T{t if t.count(':') == 2 else t + ':00'}"


def find_biz_number(text: str) -> Optional[str]:
    m = BIZ_NO_RE.search(text)
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else None


def find_phone(text: str) -> Optional[str]:
    m = PHONE_RE.search(text)
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else None


def find_approval_number(text: str) -> Optional[str]:
    m = APPROVAL_RE.search(text)
    return m.group(1) if m else None


def find_card_number(text: str) -> Optional[str]:
    for m in CARD_NO_RE.finditer(text):
        raw = m.group(0)
        if is_card_number(raw):
            return re.sub(r"\s", "", raw)
    return None


def has_text(s: str) -> bool:
    return bool(HANGUL_OR_ALPHA_RE.search(s))


def compact(s: str) -> str:
    """공백 제거 (OCR 이 '합 계' 처럼 글자 사이를 띄우는 경우 대비 키워드 매칭용)"""
    return re.sub(r"\s+", "", s)


PAYMENT_METHODS = [
    ("카카오페이", r"카카오\s*페이|kakao\s*pay"),
    ("네이버페이", r"네이버\s*페이|naver\s*pay|N\s*pay"),
    ("토스페이", r"토스\s*페이|toss\s*pay"),
    ("삼성페이", r"삼성\s*페이|samsung\s*pay"),
    ("애플페이", r"애플\s*페이|apple\s*pay"),
    ("페이코", r"페이코|payco"),
    ("신용카드", r"신용\s*카드|신용\s*승인|카드\s*승인"),
    ("체크카드", r"체크\s*카드"),
    ("현금", r"현금(?!\s*영수증\s*\()"),
    ("계좌이체", r"계좌\s*이체"),
]

CARD_COMPANIES = [
    ("신한카드", r"신한"),
    ("KB국민카드", r"KB|국민"),
    ("삼성카드", r"삼성\s*카드"),
    ("현대카드", r"현대\s*카드"),
    ("롯데카드", r"롯데\s*카드"),
    ("우리카드", r"우리\s*카드"),
    ("하나카드", r"하나\s*카드|외환"),
    ("BC카드", r"BC|비씨"),
    ("NH농협카드", r"NH|농협"),
    ("씨티카드", r"씨티|citi"),
]


def find_payment_method(text: str) -> Optional[str]:
    for name, pattern in PAYMENT_METHODS:
        if re.search(pattern, text, re.IGNORECASE):
            return name
    return None


def find_card_company(text: str) -> Optional[str]:
    for name, pattern in CARD_COMPANIES:
        if re.search(pattern, text, re.IGNORECASE):
            return name
    return None
