"""
파서 단위 테스트 (PaddleOCR 없이 실행 가능)
    cd ocr_backend && python -m pytest tests -q
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.models import DocumentType  # noqa: E402
from app.services.parser import detect_document_type, parse_document  # noqa: E402
from app.services.parser import common as c  # noqa: E402


def rows(text: str):
    return [r.strip() for r in text.strip().splitlines() if r.strip()]


CAFE_RECEIPT = rows("""
[고객용]
카페 모카하우스 강남점
사업자번호 : 214-86-12345   대표자 : 김철수
서울특별시 강남구 테헤란로 123 1층
TEL 02-555-1234
판매일 : 2025-03-12 14:23:05  POS:01
상품명 단가 수량 금액
아메리카노 4,500 2 9,000
카페라떼 5.000 1 5,000
치즈케이크 6,500 1 6,500
합계 20,500
과세물품가액 18,636
부가세 1,864
받을금액 20,500원
신용카드 20,500
카드번호 : 5310-12**-****-1234
신한카드 일시불
승인번호 : 30012345
""")

MART_RECEIPT = rows("""
OO마트 역삼점
사업자 123-45-67890
2025.11.02 18:40
001 서울우유 1L
8801115114154 2,950 1 2,950
002 신라면 5입
8801043014809 4,480 2 8,960
003 행사할인 -500
총 구매액 11,410
부가세 1,037
결제금액 11,410
현금 20,000
거스름돈 8,590
""")

KAKAO_DETAIL = rows("""
결제 상세
스타벅스 강남R점
-5,600원
결제완료
결제일시 2025.03.12 14:23:11
결제수단 카카오페이머니
승인번호 12345678
""")

KAKAO_LIST = rows("""
카카오페이 결제내역
2025년 3월
3월 12일 수요일
스타벅스 강남R점 -5,600원
14:23 | 카카오페이머니
GS25 역삼점 -2,300원
09:10 | 신한카드
3월 11일 화요일
쿠팡 -32,000원
21:05 | 카카오페이머니 결제취소
""")


def test_amount_extraction_ignores_dates_and_ids():
    assert [a.value for a in c.extract_amounts("2025-03-12 14:23 합계 20,500원")] == [20500]
    assert [a.value for a in c.extract_amounts("사업자 214-86-12345 TEL 02-555-1234")] == []
    assert c.extract_amounts("카페라떼 5.000 1 5,000")[0].value == 5000
    assert c.extract_amounts("-5,600원")[0].value == -5600


def test_date_time():
    assert c.parse_date("판매일 : 25/03/12") == "2025-03-12"
    assert c.parse_date("2025년 3월 12일") == "2025-03-12"
    assert c.parse_date("3월 12일 수요일", default_year=2025) == "2025-03-12"
    assert c.parse_time("오후 2:23") == "14:23"
    assert c.parse_time("14:23:05") == "14:23:05"


def test_detect_types():
    assert detect_document_type(CAFE_RECEIPT) == DocumentType.receipt
    assert detect_document_type(MART_RECEIPT) == DocumentType.receipt
    assert detect_document_type(KAKAO_DETAIL) == DocumentType.payment_app
    assert detect_document_type(KAKAO_LIST) == DocumentType.payment_app


def test_cafe_receipt():
    t, r = parse_document(CAFE_RECEIPT, DocumentType.auto)
    assert t == DocumentType.receipt
    assert r.storeName == "카페 모카하우스 강남점"
    assert r.businessNumber == "214-86-12345"
    assert r.representative == "김철수"
    assert r.phone == "02-555-1234"
    assert r.address.startswith("서울특별시 강남구 테헤란로")
    assert r.datetime == "2025-03-12T14:23:05"
    assert [(i.name, i.unitPrice, i.quantity, i.amount) for i in r.items] == [
        ("아메리카노", 4500, 2, 9000),
        ("카페라떼", 5000, 1, 5000),
        ("치즈케이크", 6500, 1, 6500),
    ]
    assert r.totalAmount == 20500
    assert r.supplyAmount == 18636
    assert r.vat == 1864
    assert r.paymentMethod == "신용카드"
    assert r.cardCompany == "신한카드"
    assert r.cardNumber == "5310-12**-****-1234"
    assert r.installment == "일시불"
    assert r.approvalNumber == "30012345"
    assert r.itemsSumMatchesTotal is True


def test_mart_receipt_two_line_items():
    _, r = parse_document(MART_RECEIPT, DocumentType.receipt)
    assert r.storeName == "OO마트 역삼점"
    assert r.date == "2025-11-02" and r.time == "18:40"
    assert [(i.name, i.quantity, i.amount) for i in r.items] == [
        ("서울우유 1L", 1, 2950),
        ("신라면 5입", 2, 8960),
        ("행사할인", 1, -500),
    ]
    assert r.totalAmount == 11410
    assert r.paymentMethod == "현금"
    assert r.itemsSumMatchesTotal is True


def test_kakaopay_detail():
    t, p = parse_document(KAKAO_DETAIL, DocumentType.auto)
    assert t == DocumentType.payment_app
    assert p.viewType == "detail"
    (tx,) = p.transactions
    assert tx.merchant == "스타벅스 강남R점"
    assert tx.amount == 5600
    assert tx.status == "완료" and tx.isCancel is False
    assert tx.datetime == "2025-03-12T14:23:11"
    assert tx.paymentMethod == "카카오페이머니"
    assert tx.approvalNumber == "12345678"


def test_kakaopay_list():
    _, p = parse_document(KAKAO_LIST, DocumentType.auto)
    assert p.provider == "kakaopay"
    assert p.viewType == "list"
    got = [(t.merchant, t.amount, t.datetime, t.paymentMethod, t.isCancel) for t in p.transactions]
    assert got == [
        ("스타벅스 강남R점", 5600, "2025-03-12T14:23:00", "카카오페이머니", False),
        ("GS25 역삼점", 2300, "2025-03-12T09:10:00", "신한카드", False),
        ("쿠팡", 32000, "2025-03-11T21:05:00", "카카오페이머니", True),
    ]
    assert p.totalAmount == 7900


def test_real_ocr_quirks():
    """실제 PaddleOCR 출력에서 나온 패턴: 날짜·시간 붙음, 카드번호 '_' 오인식, 주소 번지"""
    quirky = [r.replace("판매일 : 2025-03-12 14:23:05", "판매일: 2025-03-1214:23:05")
               .replace("5310-12**-****-1234", "5310-12**_****-1234") for r in CAFE_RECEIPT]
    _, r = parse_document(quirky, DocumentType.auto)
    assert r.datetime == "2025-03-12T14:23:05"
    assert r.cardNumber == "5310-12**-****-1234"
    assert r.address == "서울특별시 강남구 테헤란로 123 1층"
