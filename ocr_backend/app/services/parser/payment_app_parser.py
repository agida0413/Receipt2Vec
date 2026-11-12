"""
간편결제 앱(카카오페이 등) 결제내역 캡처 화면 파서

두 가지 화면을 처리한다.
  1) 상세 화면 (결제 1건)       : 가맹점명 / 금액 / 결제일시 / 결제수단 / 승인번호 ...
  2) 목록 화면 (결제내역 여러 건): 날짜 헤더 아래에 '가맹점 -금액원', '시간 | 결제수단' 이 반복
"""
import re
from datetime import date
from typing import List, Optional

from app.models import PaymentAppData, PaymentTransaction
from app.services.parser import common as c

PROVIDERS = [
    ("kakaopay", r"카카오\s*페이|kakao\s*pay|페이\s*머니"),
    ("naverpay", r"네이버\s*페이|naver\s*pay|N\s*pay"),
    ("tosspay", r"토스|toss"),
    ("payco", r"페이코|payco"),
]

LABEL_DATETIME = re.compile(r"(결\s*제|거\s*래|이\s*용|승\s*인|취\s*소)\s*일\s*시")
LABEL_METHOD = re.compile(r"결\s*제\s*(수\s*단|방\s*법)")
LABEL_MERCHANT = re.compile(r"가\s*맹\s*점\s*명?|사\s*용\s*처|상\s*호\s*명?")
LABEL_AMOUNT = re.compile(r"(총\s*)?(결\s*제|이\s*용|승\s*인)\s*금\s*액")
STATUS_RE = re.compile(r"결제\s*완료|결제\s*취소|취소\s*완료|부분\s*취소|환불\s*완료|환불|결제\s*실패|승인\s*거절")
YEAR_MONTH_RE = re.compile(r"(20\d{2})\s*[년.]\s*(\d{1,2})\s*월?")
DETAIL_HINT_RE = re.compile(r"결제\s*상세|상세\s*내역|거래\s*상세|이용\s*상세|결제\s*정보")
# 목록 화면에서 거래가 아닌 요약 줄
SUMMARY_RE = re.compile(r"총\s*(사용|결제|지출)|이번\s*달|합계|잔액|사용\s*금액|월\s*합계|필터|전체")
METHOD_HINT_RE = re.compile(
    r"페이\s*머니|머니|포인트|카드|계좌|체크|신용|은행|뱅크|Pay|pay|페이"
)


def detect_provider(text: str) -> Optional[str]:
    for name, pattern in PROVIDERS:
        if re.search(pattern, text, re.IGNORECASE):
            return name
    return None


def _value_after(rows: List[str], label: re.Pattern) -> Optional[str]:
    for i, row in enumerate(rows):
        m = label.search(row)
        if not m:
            continue
        value = row[m.end():].strip(" :：|")
        if not value and i + 1 < len(rows):
            value = rows[i + 1].strip()
        if value:
            return value
    return None


def _normalize_status(text: Optional[str]) -> Optional[str]:
    if not text:
        return None
    m = STATUS_RE.search(text)
    if not m:
        return None
    s = c.compact(m.group(0))
    if "취소" in s:
        return "취소"
    if "환불" in s:
        return "환불"
    if "실패" in s or "거절" in s:
        return "실패"
    return "완료"


def _method_from(text: str) -> Optional[str]:
    """'14:23 | 카카오페이머니' → '카카오페이머니'"""
    cleaned = c._mask(text, c.TIME_RE)
    cleaned = re.sub(r"[|·•ㆍ]", " ", cleaned)
    cleaned = STATUS_RE.sub(" ", cleaned).strip()
    if cleaned and METHOD_HINT_RE.search(cleaned):
        return re.sub(r"\s+", " ", cleaned)
    return None


def _won_amounts(row: str) -> List[c.Amount]:
    return [a for a in c.extract_amounts(row) if a.has_won]


def _default_year(rows: List[str]) -> int:
    for row in rows:
        m = YEAR_MONTH_RE.search(row)
        if m:
            return int(m.group(1))
        d = c.parse_date(row)
        if d:
            return int(d[:4])
    return date.today().year


def _is_detail(rows: List[str]) -> bool:
    text = "\n".join(rows)
    won_rows = sum(1 for r in rows if _won_amounts(r))
    datetime_labels = sum(1 for r in rows if LABEL_DATETIME.search(r))
    return bool(DETAIL_HINT_RE.search(text)) or (datetime_labels == 1 and won_rows <= 4)


def _parse_detail(rows: List[str], year: int) -> PaymentTransaction:
    text = "\n".join(rows)

    amount: Optional[int] = None
    amount_row: Optional[int] = None
    labeled = _value_after(rows, LABEL_AMOUNT)
    if labeled:
        amount = c.last_amount(labeled)
    # 큰 글씨로 단독 표시된 금액 줄 ('-5,600원')
    for i, row in enumerate(rows):
        won = _won_amounts(row)
        if won and not c.has_text(re.sub(r"원", "", c.strip_non_amount_numbers(row))):
            amount_row = i
            if amount is None:
                amount = won[-1].value
            break
    if amount is None:
        candidates = [a.value for r in rows for a in _won_amounts(r)]
        amount = max(candidates, key=abs) if candidates else None

    merchant = _value_after(rows, LABEL_MERCHANT)
    if not merchant and amount_row is not None:
        # 금액 바로 위 줄(제목 줄 제외)이 가맹점명인 화면이 일반적
        for j in range(amount_row - 1, -1, -1):
            cand = rows[j].strip()
            if cand and c.has_text(cand) and not DETAIL_HINT_RE.search(cand) and not detect_provider(cand):
                merchant = cand
                break

    dt_text = _value_after(rows, LABEL_DATETIME) or text
    d = c.parse_date(dt_text, default_year=year)
    t = c.parse_time(dt_text)

    method_text = _value_after(rows, LABEL_METHOD)
    status = _normalize_status(text)

    return PaymentTransaction(
        merchant=merchant,
        amount=abs(amount) if amount is not None else None,
        isCancel=status in ("취소", "환불"),
        status=status,
        date=d,
        time=t,
        datetime=c.combine_datetime(d, t),
        paymentMethod=method_text,
        approvalNumber=c.find_approval_number(text),
    )


def _parse_list(rows: List[str], year: int) -> List[PaymentTransaction]:
    transactions: List[PaymentTransaction] = []
    current_date: Optional[str] = None

    for i, row in enumerate(rows):
        won = _won_amounts(row)
        row_date = c.parse_date(row, default_year=year)
        if row_date and not won:
            current_date = row_date  # 날짜 헤더 줄
            continue
        if not won or SUMMARY_RE.search(row):
            continue

        amount = won[-1]
        # 금액 앞부분 = 가맹점명
        cleaned = c.strip_non_amount_numbers(row)
        merchant = cleaned[: amount.span[0]].strip(" |·-")
        merchant = re.sub(r"\s+", " ", merchant)
        if not c.has_text(merchant) and i > 0 and c.has_text(rows[i - 1]) and not _won_amounts(rows[i - 1]):
            merchant = rows[i - 1].strip()

        # 시간/결제수단/상태는 같은 줄 또는 바로 다음 줄에 있음
        neighbor = rows[i + 1] if i + 1 < len(rows) and not _won_amounts(rows[i + 1]) else ""
        t = c.parse_time(row) or (c.parse_time(neighbor) if neighbor else None)
        method = _method_from(neighbor) if neighbor else None
        status = _normalize_status(row) or _normalize_status(neighbor)
        d = row_date or current_date

        transactions.append(PaymentTransaction(
            merchant=merchant or None,
            amount=abs(amount.value),
            isCancel=status in ("취소", "환불") or (amount.value > 0 and "+" in row),
            status=status,
            date=d,
            time=t,
            datetime=c.combine_datetime(d, t),
            paymentMethod=method,
            approvalNumber=None,
        ))
    return transactions


def parse_payment_app(rows: List[str]) -> PaymentAppData:
    text = "\n".join(rows)
    year = _default_year(rows)
    if _is_detail(rows):
        view_type = "detail"
        transactions = [_parse_detail(rows, year)]
    else:
        view_type = "list"
        transactions = _parse_list(rows, year)
    return PaymentAppData(
        provider=detect_provider(text),
        viewType=view_type,
        transactions=transactions,
        totalAmount=sum(t.amount or 0 for t in transactions if not t.isCancel) if transactions else None,
    )
