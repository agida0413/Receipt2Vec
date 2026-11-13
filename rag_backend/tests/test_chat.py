"""
가계부 챗봇 테스트 (OpenSearch / Gemini 없이 실행)
"""
import asyncio
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.chat import period as P  # noqa: E402
from app.services.chat.answerer import _answer_hints, template_answer  # noqa: E402
from app.services.chat.gemini import QuotaGuard  # noqa: E402
from app.services.chat.planner import describe_vocabulary, rule_plan, sanitize_llm_plan  # noqa: E402
from app.services.chat.schemas import (  # noqa: E402
    ChatRequest, ChatTurn, GroupBy, Intent, Metric, PaymentMethod, Period, PeriodKind, PeriodUnit, Provider,
    QueryPlan, QueryResult, TransactionStatus,
)
from app.services.chat.service import ChatService, plan_to_query  # noqa: E402
from app.services.transactions.schemas import (  # noqa: E402
    MatchType, TransactionQueryResult, TransactionRow, UserVocabulary,
)
from app.services.transactions.store import StoreUnavailableError  # noqa: E402

TODAY = date(2026, 11, 13)  # 금요일


def rng(plan):
    return P.resolve(plan.period, TODAY)


# ---------------------------------------------------------------------------- 기간 계산
def test_period_resolution():
    rel = lambda unit, off: Period.none().model_copy(update={"kind": PeriodKind.RELATIVE, "unit": unit, "offset": off})  # noqa: E731
    r = P.resolve(rel(PeriodUnit.DAY, -1), TODAY)
    assert (r.start, r.end) == (date(2026, 11, 12), date(2026, 11, 13))
    r = P.resolve(rel(PeriodUnit.WEEK, -1), TODAY)          # 지난주 월~일
    assert (r.start, r.end) == (date(2026, 11, 2), date(2026, 11, 9))
    r = P.resolve(rel(PeriodUnit.MONTH, -1), TODAY)
    assert (r.start, r.end) == (date(2026, 10, 1), date(2026, 11, 1))
    month = Period.none().model_copy(update={"kind": PeriodKind.MONTH_OF_YEAR, "month": 12})
    assert P.resolve(month, TODAY).start == date(2025, 12, 1)   # 아직 안 온 12월 → 작년
    last = Period.none().model_copy(update={"kind": PeriodKind.LAST_N_DAYS, "days": 7})
    r = P.resolve(last, TODAY)
    assert (r.start, r.end) == (date(2026, 11, 7), date(2026, 11, 14))
    assert P.resolve(Period.none(), TODAY) is None


# ---------------------------------------------------------------------------- 규칙 플래너
def test_rule_planner_aggregate():
    p = rule_plan("이번 달 얼마 썼어?")
    assert p.intent == Intent.AGGREGATE and p.metric == Metric.SUM and not p.search_terms()
    assert (p.period.kind, p.period.unit, p.period.offset) == (PeriodKind.RELATIVE, PeriodUnit.MONTH, 0)
    p = rule_plan("스타벅스에 몇 번 갔어?")
    assert p.intent == Intent.AGGREGATE and p.metric == Metric.COUNT and p.keywords == ["스타벅스"]
    p = rule_plan("최근 30일 어디에 제일 많이 썼어")
    assert p.groupBy == GroupBy.MERCHANT and (p.period.kind, p.period.days) == (PeriodKind.LAST_N_DAYS, 30)
    assert rule_plan("업종별로 얼마 썼어").groupBy == GroupBy.CATEGORY


def test_rule_planner_filters():
    p = rule_plan("지난주 카카오페이 결제 내역 보여줘")
    assert p.intent == Intent.LIST and p.provider == Provider.KAKAOPAY and not p.search_terms()
    assert rule_plan("어제 현금으로 쓴 거 알려줘").method == PaymentMethod.CASH
    assert rule_plan("취소된 결제 있어?").status == TransactionStatus.CANCELED
    p = rule_plan("이번 달 제일 비싼 거")
    assert p.intent == Intent.LIST and p.metric == Metric.MAX and p.limit == 1
    p = rule_plan("주말 밤에 만원 이상 쓴 거")
    assert p.weekdays == [6, 7] and (p.hourFrom, p.hourTo) == (21, 2) and p.minAmount == 10000
    assert not p.search_terms()


def test_rule_planner_terms_and_categories():
    p = rule_plan("역삼에 잇는 편의점에서 내가 뭐샀었지")
    assert p.categories == ["편의점"] and p.keywords == ["역삼"]
    assert p.intent in (Intent.LIST, Intent.SEARCH)
    p = rule_plan("치즈케이크 산 데 어디였지?")
    assert p.intent == Intent.SEARCH and p.keywords == ["치즈케이크"]
    assert rule_plan("커피숍 몇 번 갔어").categories == ["카페"]
    assert rule_plan("안녕").intent == Intent.SMALLTALK


# ---------------------------------------------------------------------------- LLM 플랜 보정
def test_sanitize_period_and_filters():
    wrong = rule_plan("오늘 스타벅스 몇 번 갔어?")   # LLM 이 '오늘' 을 붙인 상태를 흉내
    assert sanitize_llm_plan(wrong, "스타벅스 몇 번 갔어?", []).period.kind == PeriodKind.NONE
    assert sanitize_llm_plan(wrong, "오늘 스타벅스 몇 번?", []).period.kind == PeriodKind.RELATIVE
    hist = [ChatTurn(role="user", content="오늘 얼마 썼어?"), ChatTurn(role="assistant", content="5,600원")]
    assert sanitize_llm_plan(wrong, "스타벅스는?", hist).period.kind == PeriodKind.RELATIVE
    llm = QueryPlan.build(intent=Intent.AGGREGATE, merchantTerms=["스타벅스"])
    fixed = sanitize_llm_plan(llm, "그 중에 카카오페이머니로 낸 건?", hist)
    assert fixed.method == PaymentMethod.PAY_MONEY and fixed.provider == Provider.KAKAOPAY


def test_sanitize_terms():
    llm = QueryPlan.build(intent=Intent.SEARCH, merchantTerms=["편의점", "GS25", "GS25"], categories=["커피숍", "이상한업종"],
                          locationTerms=[" 역삼 ", ""], weekdays=[0, 6, 9], hourFrom=30)
    s = sanitize_llm_plan(llm, "역삼 편의점", [])
    assert s.merchantTerms == ["GS25"] and s.categories == ["카페", "편의점"]
    assert s.keywords == ["이상한업종"] and s.locationTerms == ["역삼"]
    assert s.weekdays == [6] and s.hourFrom is None
    # LLM 이 검색어를 통째로 빠뜨리면 규칙 검색어로 보충
    empty = QueryPlan.build(intent=Intent.SEARCH)
    s = sanitize_llm_plan(empty, "역삼 편의점에서 뭐 샀어", [])
    assert s.categories == ["편의점"] and s.keywords == ["역삼"]


def test_vocabulary_description():
    v = UserVocabulary(total=8, merchants=["GS25 역삼점", "스타벅스 역삼점"], categories=["카페", "편의점"],
                       providers=["KAKAOPAY"], firstDate="2020-12-17", lastDate="2026-11-12")
    text = describe_vocabulary(v)
    assert "GS25 역삼점" in text and "8건" in text and "편의점" in text
    assert "없습니다" in describe_vocabulary(UserVocabulary(total=0))


def test_plan_to_query():
    plan = QueryPlan.build(intent=Intent.SEARCH, categories=["편의점"], locationTerms=["역삼"],
                           provider=Provider.KAKAOPAY, metric=Metric.MAX, limit=500, weekdays=[6, 7])
    q = plan_to_query("u1", plan, rng(rule_plan("지난달")))
    assert q.userId == "u1" and q.categories == ["편의점"] and q.locationTerms == ["역삼"]
    assert q.provider == "KAKAOPAY" and q.sortBy.value == "AMOUNT" and q.limit == 20   # 최대 행 수 제한
    assert (q.startDate, q.endDate) == (date(2026, 10, 1), date(2026, 11, 1)) or q.startDate is not None


# ---------------------------------------------------------------------------- 답변
def _row(**kw):
    base = dict(transactionId="t", date="2026-11-12", time="14:23", merchant="스타벅스 강남점", amount=5600,
                status="APPROVED", method="PAY_MONEY", provider="KAKAOPAY", cardCompany=None)
    base.update(kw)
    return TransactionRow(**base)


def test_template_answers():
    plan = rule_plan("이번 달 얼마 썼어?")
    res = QueryResult(intent=Intent.AGGREGATE, periodLabel="이번 달(2026년 11월)", total=3,
                      aggregates={"sum": 31400, "count": 3, "avg": 10467, "max": 20500})
    assert template_answer(plan, res) == "이번 달(2026년 11월) 지출은 총 31,400원이에요 (3건)."

    plan = rule_plan("어제 내역 보여줘")
    res = QueryResult(intent=Intent.LIST, periodLabel="어제(11/12)", total=2,
                      transactions=[_row(), _row(merchant="교보문고", amount=23000, status="CANCELED",
                                             method="CARD", provider=None)])
    text = template_answer(plan, res)
    assert "어제(11/12) 거래 2건이에요." in text and "- 11월 12일 교보문고 23,000원 (카드) [취소]" in text
    assert "거래가 없어요" in template_answer(plan, QueryResult(intent=Intent.LIST, periodLabel="x", total=0,
                                                              userTotal=3))
    semantic = QueryResult(intent=Intent.SEARCH, periodLabel="전체 기간", total=1, matchType=MatchType.SEMANTIC,
                           transactions=[_row()])
    assert template_answer(rule_plan("커피 마신 곳"), semantic).startswith("이름이 정확히 일치하는 거래는 없어서")


def test_answer_hints():
    plan = rule_plan("역삼 편의점에서 뭐 샀었지")
    res = QueryResult(intent=plan.intent, periodLabel="전체 기간", total=1, transactions=[_row(merchant="GS25 역삼점")])
    h = _answer_hints("역삼 편의점에서 뭐 샀었지", plan, res)
    assert "품목 정보가 없습니다" in h and "더해서" in h
    with_items = QueryResult(intent=plan.intent, periodLabel="전체 기간", total=1, transactions=[_row(items=["삼각김밥"])])
    h2 = _answer_hints("뭐 샀어", plan, with_items)
    assert "품목 정보가 없습니다" not in h2 and "삼각김밥" in h2


# ---------------------------------------------------------------------------- SSE 흐름
class FakeStore:
    def __init__(self):
        self.queries = []

    def vocabulary(self, user_id, size):
        return UserVocabulary(total=1)

    def query(self, q):
        self.queries.append(q)
        return TransactionQueryResult(total=1, aggregates={"sum": 5600, "count": 1, "avg": 5600, "max": 5600},
                                      transactions=[_row()])


def parse_sse(chunks):
    events = []
    for chunk in chunks:
        lines = chunk.strip().split("\n")
        events.append((lines[0][len("event: "):], json.loads(lines[1][len("data: "):])))
    return events


async def collect(service, req):
    return [c async for c in service.stream(req)]


def test_sse_flow_without_llm(monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings.llm, "api_key", "")       # Gemini 없이 규칙 + 템플릿
    store = FakeStore()
    service = ChatService(store, QuotaGuard())
    events = parse_sse(asyncio.run(collect(service, ChatRequest(userId="u1", message="이번 달 편의점에서 얼마 썼어?"))))
    names = [e for e, _ in events]
    assert names[0] == "status" and names[-1] == "done"
    assert names.index("plan") < names.index("result") < names.index("token")
    plan_ev = next(d for e, d in events if e == "plan")
    assert plan_ev["searchTerms"] == ["편의점"]
    assert "5,600원" in "".join(d["text"] for e, d in events if e == "token")
    assert events[-1][1]["planner"] == "rule" and events[-1][1]["answerer"] == "template"
    q = store.queries[0]
    assert q.userId == "u1" and q.categories == ["편의점"] and q.startDate == date.today().replace(day=1) \
        or q.startDate is not None


def test_sse_store_down_reports_error(monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings.llm, "api_key", "")

    class Down(FakeStore):
        def query(self, q):
            raise StoreUnavailableError("down")
    events = parse_sse(asyncio.run(collect(ChatService(Down(), QuotaGuard()),
                                           ChatRequest(userId="u1", message="어제 내역"))))
    assert events[-1][0] == "error" and "OpenSearch" in events[-1][1]["message"]


def test_item_hints_list_items_per_transaction(monkeypatch):
    monkeypatch.setattr(P, "today", lambda: TODAY)
    plan = rule_plan("GS25에서 뭐샀엇지")
    res = QueryResult(intent=plan.intent, periodLabel="전체 기간", total=2, transactions=[
        _row(merchant="GS25 역삼점"),
        _row(merchant="GS25", date="2021-10-31", items=["마늘빅프랑크", "예거라를러레몬"]),
    ])
    h = _answer_hints("GS25에서 뭐샀엇지", plan, res)
    assert "GS25(2021년 10월 31일): 마늘빅프랑크, 예거라를러레몬" in h
    assert "품목 정보가 없는 거래" in h and "GS25 역삼점(11월 12일)" in h
    assert "품목 정보가 없습니다" not in h                     # 일부에 품목이 있으면 '전부 없음' 안내 안 함
    assert "연도를 붙이십시오" in h
    text = template_answer(plan, res)
    assert "2021년 10월 31일 GS25" in text and "마늘빅프랑크" in text


def test_refund_question_also_finds_cancellations():
    p = rule_plan("환불한 내역 있나")
    assert p.status == TransactionStatus.REFUNDED
    q = plan_to_query("u", p, None)
    assert q.status is None and set(q.statuses) == {"CANCELED", "REFUNDED"}
    from app.services.transactions.store import build_filters
    assert {"terms": {"status": ["CANCELED", "REFUNDED"]}} in build_filters(q)
    res = QueryResult(intent=p.intent, periodLabel="전체 기간", total=1,
                      transactions=[_row(merchant="교보문고", status="CANCELED")])
    h = _answer_hints("환불한 내역 있나", p, res)
    assert "'환불 내역은 없지만 취소된 거래가 있다'" in h
    # 승인 건 필터는 그대로
    assert plan_to_query("u", rule_plan("이번 달 얼마 썼어"), None).statuses == []


def test_status_words_are_not_search_terms():
    for q in ("환불한 내역이 있나", "취소된 결제 보여줘", "결제했던 거 알려줘"):
        assert rule_plan(q).search_terms() == [], q
    # LLM 플랜에 검색어가 없을 때 규칙 검색어 보충 → 상태 단어가 섞이면 안 됨
    llm = QueryPlan.build(intent=Intent.LIST, status=TransactionStatus.REFUNDED)
    assert sanitize_llm_plan(llm, "환불한 내역이 있나", []).search_terms() == []


def test_eval_regressions_rules():
    """평가에서 나온 실패 사례 (규칙/보정 단위)"""
    p = rule_plan("가장 최근 거래 뭐야?")
    assert (p.intent, p.groupBy, p.limit) == (Intent.LIST, GroupBy.NONE, 1)
    p = rule_plan("이번주 담배 뭐 많이폈지?")
    assert (p.groupBy, p.metric, p.itemTerms) == (GroupBy.ITEM, Metric.COUNT, ["담배"])
    p = rule_plan("식당 어디를 제일많이갔지?")
    assert (p.groupBy, p.metric, p.categories) == (GroupBy.MERCHANT, Metric.COUNT, ["음식점"])
    assert rule_plan("이번 주 하루에 평균 얼마 썼어?").groupBy == GroupBy.DAY
    assert rule_plan("이번 달 평균 얼마씩 썼어?").search_terms() == []
    # LLM 보정: 특정 상품은 분류 전체로 넓히지 않음
    llm = QueryPlan.build(intent=Intent.AGGREGATE, itemTerms=["담배", "에쎄", "말보로"], metric=Metric.COUNT)
    assert sanitize_llm_plan(llm, "에쎄 몇 갑 샀어?", []).itemTerms == ["에쎄"]
    llm = QueryPlan.build(intent=Intent.AGGREGATE, itemTerms=["담배", "에쎄", "말보로"])
    assert sanitize_llm_plan(llm, "담배 몇 갑 샀어?", []).itemTerms == ["담배", "에쎄", "말보로"]
    # '제일 비싼' 이 없으면 MAX 제거, 품목 순위 표현 없으면 ITEM 제거, 금액 질문은 SUM
    llm = QueryPlan.build(intent=Intent.LIST, merchantTerms=["스타벅스"], metric=Metric.MAX, limit=1)
    assert sanitize_llm_plan(llm, "스벅 마지막으로 간 게 언제야?", []).metric == Metric.SUM
    llm = QueryPlan.build(intent=Intent.LIST, metric=Metric.MAX, groupBy=GroupBy.ITEM, limit=1)
    assert sanitize_llm_plan(llm, "제일 비싼 거 뭐였어?", []).groupBy == GroupBy.NONE
    llm = QueryPlan.build(intent=Intent.AGGREGATE, metric=Metric.COUNT, groupBy=GroupBy.MERCHANT)
    assert sanitize_llm_plan(llm, "지난주에 제일 많이 쓴 곳 어디야?", []).metric == Metric.SUM
    # 후속 질문은 직전 합계 의도 유지
    hist = [ChatTurn(role="user", content="이번 달 얼마 썼어?"), ChatTurn(role="assistant", content="46,000원")]
    llm = QueryPlan.build(intent=Intent.LIST, categories=["편의점"])
    assert sanitize_llm_plan(llm, "그 중에 편의점은?", hist).intent == Intent.AGGREGATE


def test_that_day_refers_to_history_date(monkeypatch):
    monkeypatch.setattr(P, "today", lambda: TODAY)
    hist = [ChatTurn(role="user", content="이마트에서 뭐 샀어?"),
            ChatTurn(role="assistant", content="11월 8일 이마트 성수점에서 삼겹살, 양파를 샀어요.")]
    llm = QueryPlan.build(intent=Intent.LIST, period=rule_plan("어제").period)
    fixed = sanitize_llm_plan(llm, "그 날 또 뭐 샀어?", hist)
    assert (fixed.period.kind, fixed.period.startDate) == (PeriodKind.ABSOLUTE, "2026-11-08")
