"""
파이프라인 테스트 (규칙 → LLM 폴백 → 검증 → 정규화 → 사용자 확인)
LLM 은 가짜 구현을 주입하므로 API 키/네트워크 없이 실행된다.
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.domain.transaction import (  # noqa: E402
    CardCompany, PaymentMethod, Provider, ReviewReason, SourceType, TransactionStatus,
)
from app.models import DocumentType  # noqa: E402
from app.services.llm.schema import LlmExtraction, LlmItem, LlmTransaction  # noqa: E402
from app.services.pipeline import confirm_transactions, extract_transactions  # noqa: E402
from tests.test_parsers import CAFE_RECEIPT, KAKAO_LIST  # noqa: E402


class FakeLlm:
    model_name = "fake-llm"

    def __init__(self, result=None, error=None, available=True):
        self.result, self.error, self.available = result, error, available
        self.calls = []

    @property
    def is_available(self):
        return self.available

    async def extract(self, text, hint):
        self.calls.append((text, hint))
        if self.error:
            raise self.error
        return self.result


def run(rows, **kw):
    return asyncio.run(extract_transactions(rows, **kw))


def llm_tx(**overrides):
    base = dict(
        merchantName="맛나분식", businessNumber=None, address=None, phone=None,
        date="2025-11-03", time="12:10", status=TransactionStatus.APPROVED,
        totalAmount=11000, supplyAmount=None, vatAmount=None, discountAmount=None,
        paymentMethod=PaymentMethod.CARD, paymentProvider=None, cardCompany=None,
        cardLast4=None, installmentMonths=None, approvalNumber=None,
        items=[LlmItem(name="떡볶이", quantity=1, unitPrice=4000, amount=4000),
               LlmItem(name="순대", quantity=1, unitPrice=4000, amount=4000),
               LlmItem(name="튀김", quantity=1, unitPrice=3000, amount=3000)],
    )
    base.update(overrides)
    return LlmTransaction(**base)


# 영수증 키워드가 거의 없는 간이 영수증 → 규칙 파서가 '카드 11000' 을 품목으로 오인 → LLM 폴백
SNACK_RECEIPT = ["맛나분식", "2025.11.03 12:10", "떡볶이 4000", "순대 4000", "튀김 3000", "카드 11000"]


def test_rule_success_does_not_call_llm():
    llm = FakeLlm()
    r = run(CAFE_RECEIPT, user_id="u1", llm=llm)
    assert llm.calls == []
    assert r.extraction.method == "rule" and not r.extraction.llmAttempted
    (tx,) = r.transactions
    assert tx.userId == "u1"
    assert tx.source.type == SourceType.RECEIPT
    assert tx.merchant.businessNumber == "214-86-12345"
    assert tx.transactedAt == "2025-03-12T14:23:05+09:00"
    assert tx.amount.total == 20500 and tx.amount.vat == 1864
    assert tx.payment.method == PaymentMethod.CARD
    assert tx.payment.cardCompany == CardCompany.SHINHAN
    assert tx.payment.cardLast4 == "1234"
    assert tx.payment.installmentMonths == 0
    assert tx.payment.approvalNumber == "30012345"
    assert len(tx.items) == 3
    assert tx.quality.needsReview is False
    assert tx.raw.parserName == "rule:receipt"


def test_kakaopay_list_normalized():
    r = run(KAKAO_LIST, llm=FakeLlm())
    assert [t.source.provider for t in r.transactions] == [Provider.KAKAOPAY] * 3
    a, b, c = r.transactions
    assert (a.payment.method, a.payment.provider) == (PaymentMethod.PAY_MONEY, Provider.KAKAOPAY)
    assert (b.payment.method, b.payment.cardCompany, b.payment.provider) == (
        PaymentMethod.CARD, CardCompany.SHINHAN, Provider.KAKAOPAY)
    assert c.status == TransactionStatus.CANCELED and c.amount.total == 32000
    assert len({t.source.documentId for t in r.transactions}) == 1   # 같은 캡처
    assert len({t.transactionId for t in r.transactions}) == 3


def test_rule_failure_uses_llm():
    llm = FakeLlm(LlmExtraction(documentType=SourceType.RECEIPT, screenProvider=None,
                                transactions=[llm_tx()]))
    r = run(SNACK_RECEIPT, llm=llm)
    assert ReviewReason.UNSURE_DOC_TYPE in r.extraction.llmTriggerReasons
    assert len(llm.calls) == 1
    assert r.extraction.method == "llm" and r.extraction.llmUsed
    (tx,) = r.transactions
    assert tx.amount.total == 11000
    assert tx.quality.itemsSumMatchesTotal is True
    assert tx.raw.parserName == "llm:fake-llm"
    # LLM 결과는 항상 사용자 확인 대상
    assert tx.quality.needsReview and ReviewReason.LLM_EXTRACTED in tx.quality.reviewReasons


def test_llm_hallucinated_numbers_are_dropped():
    fake = llm_tx(totalAmount=99000, approvalNumber="77778888", vatAmount=1000,
                  items=[LlmItem(name="떡볶이", quantity=1, unitPrice=4000, amount=4000),
                         LlmItem(name="김밥", quantity=1, unitPrice=None, amount=3500)])
    llm = FakeLlm(LlmExtraction(documentType=SourceType.RECEIPT, screenProvider=None, transactions=[fake]))
    r = run(SNACK_RECEIPT, llm=llm)
    assert set(r.extraction.llmUnverifiedFields) == {
        "totalAmount", "vatAmount", "approvalNumber", "items[김밥]"}
    # 총액이 버려진 LLM 결과(필수값 누락)는 규칙 결과보다 나을 수 없음 → 채택 여부와 무관하게 금액 99000 은 없어야 함
    for tx in r.transactions:
        assert tx.amount.total != 99000
        assert tx.payment.approvalNumber != "77778888"


def test_force_llm_always_calls_and_adopts():
    # 규칙 파서가 완벽히 처리하는 영수증이어도 force 면 LLM 호출 + 채택
    llm = FakeLlm(LlmExtraction(documentType=SourceType.RECEIPT, screenProvider=None, transactions=[
        llm_tx(merchantName="카페 모카하우스 강남점", date="2025-03-12", time="14:23:05", totalAmount=20500,
               items=[])]))
    r = run(CAFE_RECEIPT, force_llm=True, llm=llm)
    assert len(llm.calls) == 1
    assert r.extraction.llmForced and r.extraction.llmUsed and r.extraction.method == "llm"
    assert r.extraction.llmTriggerReasons == []
    # force 여도 LLM 이 거래를 못 찾으면 규칙 결과 사용
    empty = FakeLlm(LlmExtraction(documentType=SourceType.RECEIPT, screenProvider=None, transactions=[]))
    r = run(CAFE_RECEIPT, force_llm=True, llm=empty)
    assert r.extraction.method == "rule" and r.transactions[0].amount.total == 20500
    # force 여도 use_llm=false 가 우선
    r = run(CAFE_RECEIPT, force_llm=True, use_llm=False, llm=FakeLlm())
    assert not r.extraction.llmAttempted


# 실사용 사진(하나로마트, 하단 카드정보 잘림)에서 나온 OCR 원문
HANARO = [
    "서서울농협하나로마트사직점", "대표:조원민 전화:02-725-4021", "사업자번호:101-82-16155",
    "김미진 2020-12-17 12:43:27 0004-00123", "상품(코드) 단가 수량 금액",
    "001 P재사용종량제봉투 20L", "*2100010379979 490 1 490",
    "004 P맥심 모카골드 커피믹스 12g*200개 *8801037076295 22,600 1 22,600",
    "총 구매액: 27,600", "신 용 액: 27,600",
    "부가세면세물품가액: 490", "부가세과세물품가액: 24,646", "부 가 세: 2,464",
]


def test_llm_misplaced_values_are_dropped():
    """원문에 있는 숫자지만 엉뚱한 필드에 넣은 값은 라벨 검증으로 버림"""
    wrong = llm_tx(
        merchantName="서서울농협하나로마트사직점", date="2020-12-17", time="12:43:27",
        totalAmount=27600, vatAmount=490, supplyAmount=None,
        approvalNumber="0004-00123", cardCompany=CardCompany.NH, cardLast4="9979", installmentMonths=0,
        items=[LlmItem(name="P재사용종량제봉투 20L", quantity=1, unitPrice=490, amount=490),
               LlmItem(name="P맥심 모카골드 커피믹스 12g*200개 *********6295", quantity=1,
                       unitPrice=22600, amount=22600)],
    )
    r = run(HANARO, force_llm=True, llm=FakeLlm(
        LlmExtraction(documentType=SourceType.RECEIPT, screenProvider=None, transactions=[wrong])))
    (tx,) = r.transactions
    assert set(r.extraction.llmUnverifiedFields) == {
        "vatAmount", "approvalNumber", "cardCompany", "cardLast4", "installmentMonths"}
    assert tx.amount.total == 27600 and tx.amount.vat is None
    assert tx.payment.approvalNumber is None and tx.payment.cardCompany is None
    assert tx.payment.cardLast4 is None and tx.payment.installmentMonths is None
    assert tx.items[1].name == "P맥심 모카골드 커피믹스 12g*200개"
    assert ReviewReason.LLM_UNVERIFIED_VALUE in tx.quality.reviewReasons

    # 올바른 배치는 통과
    right = llm_tx(merchantName="서서울농협하나로마트사직점", date="2020-12-17", time="12:43:27",
                   totalAmount=27600, vatAmount=2464, supplyAmount=24646, items=[])
    ocr_misread = [r.replace("물품가액", "울품가액") for r in HANARO]  # 실제 OCR 결과
    r = run(ocr_misread, force_llm=True, llm=FakeLlm(
        LlmExtraction(documentType=SourceType.RECEIPT, screenProvider=None, transactions=[right])))
    assert r.extraction.llmUnverifiedFields == []
    assert (r.transactions[0].amount.vat, r.transactions[0].amount.supply) == (2464, 24646)


def test_barcode_is_not_card_number():
    r = run(HANARO, use_llm=False, llm=FakeLlm())
    assert "2100010379979" in r.rawText          # 바코드는 마스킹하지 않음
    assert r.transactions[0].payment.cardLast4 is None


def test_llm_error_falls_back_to_rule():
    r = run(SNACK_RECEIPT, llm=FakeLlm(error=TimeoutError("timeout")))
    assert r.extraction.llmAttempted and not r.extraction.llmUsed
    assert "TimeoutError" in r.extraction.llmError
    assert r.extraction.method == "rule"
    assert r.transactions and r.transactions[0].quality.needsReview


def test_llm_unavailable_is_reported():
    r = run(SNACK_RECEIPT, llm=FakeLlm(available=False))
    assert not r.extraction.llmAttempted
    assert "GEMINI_API_KEY" in r.extraction.llmSkippedReason
    r = run(SNACK_RECEIPT, use_llm=False, llm=FakeLlm())
    assert "use_llm=false" in r.extraction.llmSkippedReason


def test_card_number_masked_before_llm_and_storage():
    rows = SNACK_RECEIPT + ["카드번호 1234-5678-9012-3456"]
    llm = FakeLlm(LlmExtraction(documentType=SourceType.RECEIPT, screenProvider=None,
                                transactions=[llm_tx(cardLast4="3456")]))
    r = run(rows, llm=llm)
    sent_text = llm.calls[0][0]
    assert "1234-5678-9012" not in sent_text and "****-****-****-3456" in sent_text
    assert "1234-5678-9012" not in r.transactions[0].raw.text
    assert r.transactions[0].payment.cardLast4 == "3456"


def test_confirm_flow():
    r = run(KAKAO_LIST, llm=FakeLlm())
    tx = r.transactions[0].model_copy(deep=True)

    # 필수값(가맹점명)을 지우면 확정 불가
    tx.merchant.name = "  "
    (bad,) = confirm_transactions([tx])
    assert bad.quality.confirmed is False and "merchant.name" in bad.quality.missingFields

    # 사용자가 가맹점명 수정 + 날짜만 변경 → 시간은 유지, 확정
    tx.merchant.name = "스타벅스 강남R점"
    tx.transactedDate = "2025-03-13"
    tx.payment.cardLast4 = "5310-12**-****-9999"
    tx.amount.total = -5600
    (ok,) = confirm_transactions([tx])
    assert ok.quality.confirmed is True and ok.quality.needsReview is False
    assert ok.transactedAt == "2025-03-13T14:23:00+09:00" and ok.transactedDate == "2025-03-13"
    assert ok.payment.cardLast4 == "9999"
    assert ok.amount.total == 5600


def test_rule_payment_line_and_inferred_total():
    """실제 Gemini 테스트에서 발견: '카드 11000' 을 품목으로 오인, 카드번호 일부를 전화번호로 오인"""
    rows = ["맛나분식", "2025.11.03 12:10", "떡볶이 4000", "순대 4000", "튀김 3000",
            "카드 11000", "카드번호 1234-5678-9012-3456"]
    r = run(rows, use_llm=False, llm=FakeLlm())
    (tx,) = r.transactions
    assert [i.name for i in tx.items] == ["떡볶이", "순대", "튀김"]
    assert tx.amount.total == 11000
    assert tx.merchant.phone is None
    assert tx.payment.cardLast4 == "3456"

    # 총액 표기가 전혀 없으면 품목 합으로 추정 → '일치' 로 보지 않고 확인 요청
    r = run(["맛나분식", "2025.11.03 12:10", "떡볶이 4000", "순대 4000"], use_llm=False, llm=FakeLlm())
    (tx,) = r.transactions
    assert tx.amount.total == 8000
    assert tx.quality.itemsSumMatchesTotal is None
    assert ReviewReason.AMOUNT_INFERRED in tx.quality.reviewReasons


def test_rule_vat_not_confused_with_goods_amount():
    """'부가세면세물품가액' 을 부가세로 오인하지 않음 + OCR 의 '울품가액' 오인식 허용"""
    rows = [r.replace("물품가액", "울품가액") for r in HANARO]
    r = run(rows, use_llm=False, llm=FakeLlm())
    a = r.transactions[0].amount
    assert (a.total, a.vat, a.supply) == (27600, 2464, 24646)


def test_llm_mode_parsing(monkeypatch):
    from app.core import config
    for env, expected in [
        ({"LLM_MODE": "always"}, "always"),
        ({"LLM_MODE": "OFF"}, "off"),
        ({}, "fallback"),
        ({"LLM_FALLBACK_ENABLED": "false"}, "off"),
        # 예전 설정 조합: '전체 끄기' + '항상' → 의도대로 always
        ({"LLM_FALLBACK_ENABLED": "false", "LLM_ALWAYS": "true"}, "always"),
    ]:
        for k in ("LLM_MODE", "LLM_FALLBACK_ENABLED", "LLM_ALWAYS"):
            monkeypatch.delenv(k, raising=False)
        for k, v in env.items():
            monkeypatch.setenv(k, v)
        assert config._llm_mode() == expected, env
