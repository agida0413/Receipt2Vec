"""
종이 영수증(카드전표/매장 영수증) 파서

입력: 줄 단위 텍스트 목록 (layout.group_rows 결과의 text)
출력: 상호/사업자번호/일시/품목/합계/부가세/결제수단 등 구조화 데이터

영수증 양식이 매장마다 다르므로 '키워드 + 위치' 휴리스틱으로 최대한 뽑고,
못 찾은 필드는 None 으로 둔다. (원문 rawText 는 응답에 항상 같이 내려감)
"""
import re
from typing import List, Optional, Tuple

from app.models import ReceiptData, ReceiptItem
from app.services.parser import common as c

# "카드 11000", "현금 5,000" 처럼 결제수단 + 금액만 있는 줄 (공백 제거 후 매칭)
PAYMENT_LINE_KEY = r"^(카드|현금|결제|체크카드|카카오페이|네이버페이|토스페이|페이코|삼성페이)[:]?-?\d"

# 우선순위 순서 (앞에 있을수록 '최종 결제 금액' 에 가까운 표현)
TOTAL_KEYS = [
    r"받을금액", r"결제금액", r"결제합계", r"승인금액", r"합계금액", r"총결제", r"총합계",
    r"총금액", r"총구매액", r"구매금액", r"청구금액", r"판매금액", r"총액", r"합계", r"카드결제", r"신용카드",
    PAYMENT_LINE_KEY,
]
SUBTOTAL_KEYS = [r"소계", r"판매합계", r"상품합계"]
# '부가세 면세/과세 물품가액' 은 부가세가 아님. OCR 이 '물품' 을 '울품/몰품' 으로 읽는 경우 허용
_GOODS = r".품가액"
VAT_KEYS = [rf"^(?!.*{_GOODS}).*(부가세|부가가치세|세액)"]
SUPPLY_KEYS = [r"공급가액", rf"과세{_GOODS}", r"과세물품", r"과세금액", rf"^(?!.*면세){_GOODS}", r"공급가"]
DISCOUNT_KEYS = [r"총할인", r"할인합계", r"할인금액", r"할인액"]

# 품목 영역의 끝을 알리는 줄
ITEMS_END_RE = re.compile(
    r"합계|소계|총액|총금액|구매액|구매금액|과세|면세|부가세|부가가치세|공급가|결제금액|받을금액|판매금액|청구금액|"
    r"승인금액|카드번호|카드결제|신용카드|현금영수증|" + PAYMENT_LINE_KEY
)
ITEMS_HEADER_RE = re.compile(r"(상품|품명|품목|메뉴|내역|항목).*(수량|단가|금액)|(단가|수량).*금액")
# 품목으로 보면 안 되는 메타 정보 줄
META_RE = re.compile(
    r"사업자|대표|전화|TEL|tel|주소|영수증|매장|포스|POS|pos|계산원|캐셔|담당|일시|날짜|번호|"
    r"교환|환불|반품|감사|방문|고객|회원|포인트|적립|가맹|단말기|서명|NO\.|No\.|문의"
)
STORE_SKIP_RE = re.compile(
    r"영수증|고객용|매장용|가맹점용|카드사용|신용승인|승인|재발행|교환|환불|사업자|대표|전화|TEL|주소|\[|\]|\*{2,}|={2,}|-{3,}"
)
LABEL_STORE_RE = re.compile(r"(상\s*호|가\s*맹\s*점\s*명?|매\s*장\s*명|상\s*점\s*명)\s*[:：]?\s*(.*)")
LABEL_ADDR_RE = re.compile(r"주\s*소\s*[:：]?\s*(.*)")
LABEL_REP_RE = re.compile(r"대\s*표\s*자?\s*(?:명)?\s*[:：]?\s*([가-힣A-Za-z]{2,10})")
ADDR_RE = re.compile(r"[가-힣]+(시|도)\s+[가-힣]+(구|군|시)\s+\S*(로|길|동|읍|면)[^\n]*")
DATE_LABEL_RE = re.compile(r"거래\s*일\s*시|결제\s*일\s*시|판매\s*일|거래\s*일자|승인\s*일\s*시|일\s*시")
INSTALLMENT_RE = re.compile(r"일시불|(\d{1,2})\s*개월")
BARCODE_TOKEN_RE = re.compile(r"^\d{8,14}$")
# 품목에서 빼야 할 주소 줄 (ADDR_RE 는 '서울시' 처럼 시·도 접미사가 있어야 해서 '서울 강남구 …' 를 놓침)
ADDR_LINE_RE = re.compile(
    r"^(서울|부산|대구|인천|광주|대전|울산|세종|경기|강원|충북|충남|전북|전남|경북|경남|제주)\S*\s+[가-힣]+(구|군|시)\b"
    r"|[가-힣]+(구|군)\s+[가-힣0-9]+(로|길|대로)\s*\d"
)
ITEM_NO_PREFIX_RE =re.compile(r"^(?:\d{1,3}|P\d{0,3}|\*)\s+")


def _find_key_amount(rows: List[str], keys: List[str]) -> Tuple[Optional[int], Optional[int]]:
    """키워드 우선순위대로 줄을 찾아 그 줄(없으면 다음 줄)의 마지막 금액을 반환 → (금액, 줄 index)"""
    compacted = [c.compact(r) for r in rows]
    for key in keys:
        for i, row in enumerate(compacted):
            if not re.search(key, row):
                continue
            value = c.last_amount(rows[i], min_abs=10)
            if value is None and i + 1 < len(rows):
                nxt = rows[i + 1]
                if not c.has_text(c.strip_non_amount_numbers(nxt)):
                    value = c.last_amount(nxt, min_abs=10)
            if value is not None:
                return abs(value), i
    return None, None


def _label_value(rows: List[str], pattern: re.Pattern, group: int = 1) -> Optional[str]:
    for i, row in enumerate(rows):
        m = pattern.search(row)
        if not m:
            continue
        value = (m.group(group) or "").strip(" :：")
        if not value and i + 1 < len(rows):
            value = rows[i + 1].strip()
        if value:
            return value
    return None


def _store_name(rows: List[str]) -> Optional[str]:
    labeled = _label_value(rows, LABEL_STORE_RE, group=2)
    if labeled:
        # "상호: OO마트 사업자: 123-.." 처럼 한 줄에 여러 라벨이 붙은 경우 잘라냄
        labeled = re.split(r"\s{2,}|사업자|대표|전화|TEL", labeled)[0].strip()
        if labeled:
            return labeled
    for row in rows[:6]:
        candidate = row.strip()
        if STORE_SKIP_RE.search(candidate) or not c.has_text(candidate):
            continue
        if c.parse_date(candidate) or c.find_phone(candidate) or c.find_biz_number(candidate):
            continue
        letters = len(re.findall(r"[가-힣A-Za-z]", candidate))
        if letters >= 2:
            return candidate
    return None


_NO_PRICE_TOKENS = {"-", "–", "—", "―", "－"}


def _split_trailing_numbers(text: str) -> Tuple[str, List[int]]:
    """'아메리카노 2 4,500 9,000' → ('아메리카노', [2, 4500, 9000])"""
    tokens = text.split()
    numbers: List[int] = []
    while tokens:
        tok = tokens[-1]
        if BARCODE_TOKEN_RE.match(tok.replace(",", "")):
            tokens.pop()
            continue
        if tok in _NO_PRICE_TOKENS:
            # 가격 칸의 '-' = 가격 없음 (스타벅스 '일회용컵으로  1  -' 같은 옵션 줄) → 0원 품목으로
            numbers.insert(0, 0)
            tokens.pop()
            continue
        amounts = c.extract_amounts(tok)
        if len(amounts) == 1 and not c.has_text(tok):
            numbers.insert(0, amounts[0].value)
            tokens.pop()
        else:
            break
    name = " ".join(tokens)
    # 앞쪽 바코드 토큰 제거
    name = " ".join(t for t in name.split() if not BARCODE_TOKEN_RE.match(t))
    name = ITEM_NO_PREFIX_RE.sub("", name).strip(" *#")
    return name, numbers


def _build_item(name: str, numbers: List[int]) -> Optional[ReceiptItem]:
    if not numbers:
        return None
    amount = numbers[-1]
    qty: Optional[int] = None
    unit: Optional[int] = None
    if len(numbers) >= 3:
        a, b = numbers[-3], numbers[-2]
        # 양식에 따라 '단가 수량 금액' 또는 '수량 단가 금액'
        if a * b == abs(amount) or b * a == abs(amount):
            unit, qty = (a, b) if a >= b else (b, a)
        else:
            unit, qty = a, b
    elif len(numbers) == 2:
        a = numbers[0]
        if 0 < a < 100 and a != abs(amount):
            qty = a
            unit = abs(amount) // a if abs(amount) % a == 0 else None
        else:
            unit, qty = a, (abs(amount) // a if a and abs(amount) % a == 0 else None)
    if qty is None and unit is None:
        qty = 1
        unit = abs(amount)
    if amount == 0:
        unit = None  # 가격 없음 ('-') → 단가도 없음
    return ReceiptItem(name=name, quantity=qty, unitPrice=unit, amount=amount)


def _items(rows: List[str]) -> List[ReceiptItem]:
    start = 0
    for i, row in enumerate(rows):
        if ITEMS_HEADER_RE.search(c.compact(row)):
            start = i + 1
            break
    end = len(rows)
    for i in range(start, len(rows)):
        if ITEMS_END_RE.search(c.compact(rows[i])):
            end = i
            break

    items: List[ReceiptItem] = []
    pending_name: Optional[str] = None
    for row in rows[start:end]:
        if META_RE.search(row) and "할인" not in row:
            pending_name = None
            continue
        if c.parse_date(row) or c.find_biz_number(row) or c.find_phone(row):
            continue
        # 주소 줄 ('서울 강남구 테헤란로 123') 의 끝 번지를 금액으로 읽어 품목이 되는 것 방지
        if ADDR_RE.search(row) or LABEL_ADDR_RE.search(row) or ADDR_LINE_RE.search(row):
            pending_name = None
            continue
        name, numbers = _split_trailing_numbers(row)
        if numbers and not c.has_text(name) and pending_name:
            # 상품명 줄 + 숫자 줄로 나뉜 양식 (대형마트 영수증 등)
            name = pending_name
        pending_name = None
        if not numbers:
            if c.has_text(name):
                pending_name = name
            continue
        if not c.has_text(name):
            continue
        item = _build_item(name, numbers)
        if item:
            items.append(item)
    return items


def _repair_merged_quantity(items: List[ReceiptItem], total: Optional[int], discount: Optional[int]) -> List[ReceiptItem]:
    """
    수량과 금액 칸이 가까우면 OCR 이 한 덩어리로 읽는다: '에쎄 1mg  1  4,500' → '14,500'
    품목 합계가 총액과 안 맞을 때만, 금액 앞 1~2자리를 수량으로 떼어 봐서 합계가 총액과 '정확히' 맞으면 고친다.
    (맞지 않으면 건드리지 않음 → 우연히 고쳐지는 오탐 방지)
    """
    if not items or not total:
        return items
    target = total + (discount or 0)
    current = sum(i.amount for i in items)
    if current == target or current == total:
        return items
    for idx, it in enumerate(items):
        if it.quantity not in (None, 1) or it.amount <= 0:
            continue
        s = str(it.amount)
        for k in (1, 2):
            if len(s) <= k + 2 or s[k] == "0":
                continue
            qty, amount = int(s[:k]), int(s[k:])
            if not 1 <= qty <= 20:
                continue
            if current - it.amount + amount in (target, total):
                fixed = ReceiptItem(name=it.name, quantity=qty,
                                    unitPrice=amount // qty if amount % qty == 0 else None, amount=amount)
                return items[:idx] + [fixed] + items[idx + 1:]
    return items


def parse_receipt(rows: List[str]) -> ReceiptData:
    full_text = "\n".join(rows)

    total, _ = _find_key_amount(rows, TOTAL_KEYS)
    subtotal, _ = _find_key_amount(rows, SUBTOTAL_KEYS)
    vat, _ = _find_key_amount(rows, VAT_KEYS)
    supply, _ = _find_key_amount(rows, SUPPLY_KEYS)
    discount, _ = _find_key_amount(rows, DISCOUNT_KEYS)
    items = _items(rows)

    total_inferred = total is None
    if total is None:
        # 키워드를 못 찾으면 '원' 이 붙은 금액 중 최댓값 → 그래도 없으면 품목 합
        # (추정값이므로 totalInferred=True → 사용자 확인/LLM 폴백 대상)
        won_amounts = [a.value for r in rows for a in c.extract_amounts(r) if a.has_won and a.value > 0]
        if won_amounts:
            total = max(won_amounts)
        elif items:
            total = sum(i.amount for i in items)

    # 일시: 라벨이 붙은 줄 우선, 없으면 첫 날짜
    date_str = time_str = None
    for row in rows:
        if DATE_LABEL_RE.search(row) and c.parse_date(row):
            date_str, time_str = c.parse_date(row), c.parse_time(row)
            break
    if not date_str:
        for i, row in enumerate(rows):
            d = c.parse_date(row)
            if d:
                date_str = d
                time_str = c.parse_time(row) or (c.parse_time(rows[i + 1]) if i + 1 < len(rows) else None)
                break

    installment = None
    m = INSTALLMENT_RE.search(full_text)
    if m:
        installment = "일시불" if m.group(0) == "일시불" else f"{m.group(1)}개월"

    address = _label_value(rows, LABEL_ADDR_RE)
    if not address:
        m = ADDR_RE.search(full_text)
        address = m.group(0).strip() if m else None

    rep = LABEL_REP_RE.search(full_text)

    if not total_inferred:
        items = _repair_merged_quantity(items, total, discount)
    items_sum = sum(i.amount for i in items) if items else None
    if total_inferred:
        items_match = None  # 추정한 총액과의 비교는 의미 없음
    elif discount and items_sum is not None and items_sum - discount == total:
        items_match = True
    else:
        items_match = (items_sum == total) if (items_sum is not None and total is not None) else None

    card_number = c.find_card_number(full_text)
    payment_method = c.find_payment_method(full_text)
    if payment_method is None and card_number:
        payment_method = "신용카드"

    return ReceiptData(
        storeName=_store_name(rows),
        businessNumber=c.find_biz_number(full_text),
        representative=rep.group(1) if rep else None,
        phone=c.find_phone(full_text),
        address=address,
        date=date_str,
        time=time_str,
        datetime=c.combine_datetime(date_str, time_str),
        items=items,
        subtotal=subtotal,
        discount=discount,
        supplyAmount=supply,
        vat=vat,
        totalAmount=total,
        paymentMethod=payment_method,
        cardCompany=c.find_card_company(full_text) if card_number or payment_method in ("신용카드", "체크카드") else None,
        cardNumber=card_number,
        installment=installment,
        approvalNumber=c.find_approval_number(full_text),
        itemsSumMatchesTotal=items_match,
        totalInferred=total_inferred,
    )
