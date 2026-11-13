"""
조회 결과 → 답변 문장 (스트리밍)

- LLM: 조회 결과(JSON)만 근거로 답하게 하고, 숫자는 집계 결과를 그대로 쓰게 한다 (직접 더하지 않음)
- 템플릿: LLM 을 못 쓸 때(할당량 초과 등) 같은 결과로 정해진 문장을 만든다
"""
import asyncio
import re
import json
from typing import AsyncIterator, List, Optional

from app.services.chat import period as period_mod
from app.services.chat.schemas import ChatTurn, GroupBy, Intent, Metric, QueryPlan, QueryResult, TransactionStatus
from app.services.transactions.schemas import MatchType
from app.core.config import settings
from app.services.chat.gemini import QuotaGuard, get_client, stream_with_retry

ANSWER_INSTRUCTION = """\
당신은 사용자의 영수증·결제내역으로 답하는 한국어 가계부 챗봇입니다.

규칙:
1. 아래 '조회 결과' 만 근거로 답합니다. 결과에 없는 거래·금액을 지어내지 마십시오.
2. 합계·건수·평균은 aggregates 의 값을 그대로 씁니다. 거래 목록을 보고 직접 더하거나 계산하지 마십시오
   (목록은 일부만 포함될 수 있습니다).
3. 금액은 '12,300원' 형식, 날짜는 '11월 12일' 형식으로 씁니다.
4. 기간은 이번 조회 결과의 periodLabel 만 사용합니다. '전체 기간' 이면 특정 날짜를 말하지 마십시오.
5. 이전 대화는 질문 맥락을 이해하는 데만 씁니다. 이전 답변에 나온 날짜·금액·건수를 다시 쓰지 말고,
   반드시 이번 '조회 결과' 의 값만 쓰십시오.
6. 결과가 0건일 때: userTotal 이 0 이면 '아직 저장된 거래가 없다' 고 하고 영수증 업로드와 사용자 ID 확인을 안내합니다.
   userTotal 이 1 이상이면 검색어·기간 조건에 맞는 거래가 없다고 하고 저장된 전체 건수를 알려줍니다.
7. '뭐 샀어' 처럼 품목을 묻는데 거래의 items 가 비어 있으면, 품목 정보는 저장되어 있지 않다고 말하고
   가맹점·날짜·금액을 알려줍니다 (결제앱 캡처는 품목이 없는 경우가 많습니다). 품목을 지어내지 마십시오.
8. 2~5문장으로 간결하게. 목록이 필요하면 최대 5개까지 '- ' 로 나열합니다.
9. 상태가 CANCELED/REFUNDED 인 거래는 취소/환불이라고 표시합니다.
10. matchType 이 RELAXED 면 '조건에 딱 맞는 건 없지만 일부 조건이 맞는 거래' 라고, SEMANTIC 이면
    '정확히 일치하는 이름은 없지만 비슷한 거래' 라고 먼저 밝히고 답합니다. 단정하지 마십시오.
11. 거래의 category(업종)를 활용해 '편의점 GS25 역삼점' 처럼 자연스럽게 설명할 수 있습니다.
"""

_METHOD_KO = {"CARD": "카드", "CASH": "현금", "PAY_MONEY": "페이머니", "BANK_TRANSFER": "계좌",
              "POINT": "포인트", "UNKNOWN": "기타"}
_PROVIDER_KO = {"KAKAOPAY": "카카오페이", "TOSSPAY": "토스페이", "NAVERPAY": "네이버페이",
                "PAYCO": "페이코", "SAMSUNGPAY": "삼성페이", "APPLEPAY": "애플페이", "NONE": "간편결제 아님"}
_STATUS_KO = {"CANCELED": "취소", "REFUNDED": "환불", "FAILED": "실패"}

HELP_TEXT = (
    "안녕하세요! 업로드하신 영수증·결제내역으로 지출을 알려드려요.\n"
    "이렇게 물어보세요:\n"
    "- 이번 달 얼마 썼어?\n- 지난주 카카오페이 결제 내역 보여줘\n"
    "- 스타벅스에 몇 번 갔어?\n- 최근 30일 어디에 제일 많이 썼어?\n"
    "왼쪽 '영수증 업로드' 로 거래를 먼저 등록해 주세요."
)


def _won(v: Optional[float]) -> str:
    return f"{int(v or 0):,}원"


def _row_line(r, asks_items: bool = False) -> str:
    via = _PROVIDER_KO.get(r.provider or "", "") or _METHOD_KO.get(r.method or "", "")
    status = f" [{_STATUS_KO[r.status]}]" if r.status in _STATUS_KO else ""
    shown = r.matchedItems or r.items
    if shown:
        items = f" — {', '.join(shown[:3])}" + (" 외" if len(shown) > 3 else "")
    else:
        items = " (품목 정보 없음)" if asks_items else ""
    return (f"- {_date_ko(r.date)} {r.merchant or '가맹점 미상'} {_won(r.amount)}" + (f" ({via})" if via else "")
            + status + items)


def template_answer(plan: QueryPlan, result: Optional[QueryResult], message: str = "") -> str:
    """LLM 을 못 쓸 때의 정해진 문장 (숫자는 조회 결과 그대로)"""
    if plan.intent == Intent.SMALLTALK or result is None:
        return HELP_TEXT
    label = result.periodLabel
    terms = plan.search_terms()
    subject = f"'{' '.join(terms)}' " if terms else ""
    asks_items = bool(_ASKS_ITEMS.search(message))
    if result.total == 0:
        if result.userTotal == 0:
            return ("아직 저장된 거래가 없어요. 왼쪽 '영수증 업로드' 로 영수증이나 결제내역을 먼저 등록해 주세요.\n"
                    "(사용자 ID 별로 거래가 따로 저장돼요. 다른 ID 로 등록했다면 ID 를 바꿔 보세요.)")
        stored = f" (저장된 거래는 전체 {result.userTotal}건이에요)" if result.userTotal else ""
        return f"{label} {subject}조건에 맞는 거래가 없어요.{stored}"
    prefix = {MatchType.RELAXED: "조건에 딱 맞는 거래는 없어서, 일부 조건이 맞는 거래를 보여드려요.\n",
              MatchType.SEMANTIC: "이름이 정확히 일치하는 거래는 없어서, 비슷한 거래를 찾아봤어요.\n"}.get(result.matchType, "")

    if plan.intent == Intent.AGGREGATE:
        a = result.aggregates
        ia = result.itemAggregates
        groups = result.groups
        if plan.groupBy == GroupBy.ITEM and groups:
            lines = [f"{label} {subject}가장 많이 산 품목은 {groups[0]['key']}이에요 ({groups[0]['count']}개)."]
            lines += [f"- {g['key']}: {g['count']}개 ({_won(g['sum'])})" for g in groups[:5]]
            return prefix + "\n".join(lines)
        if plan.groupBy == GroupBy.DAY and plan.metric == Metric.MAX and groups:
            top = groups[0]
            lines = [f"{label} 가장 많이 쓴 날은 {_date_ko(str(top['key']))}이에요 ({_won(top['sum'])}, {top['count']}건)."]
            lines += [f"- {_date_ko(str(g['key']))}: {_won(g['sum'])} ({g['count']}건)" for g in groups[1:5]]
            return prefix + "\n".join(lines)
        if plan.groupBy == GroupBy.MERCHANT and plan.metric == Metric.COUNT and groups:
            top = groups[0]
            lines = [f"{label} {subject}가장 많이 간 곳은 {top['key']}이에요 ({top['count']}번)."]
            lines += [f"- {g['key']}: {g['count']}번 ({_won(g['sum'])})" for g in groups[:5]]
            return prefix + "\n".join(lines)
        if plan.itemTerms and ia:  # 품목 질문은 품목 금액·수량 (거래 전체 금액 아님)
            if plan.metric == Metric.COUNT:
                head = f"{label} {subject}품목은 총 {int(ia.get('quantity') or 0)}개예요 ({_won(ia.get('sum'))})."
            else:
                head = f"{label} {subject}품목 금액은 총 {_won(ia.get('sum'))}이에요 ({int(ia.get('quantity') or 0)}개)."
            rows = [r for r in result.transactions if r.matchedItems][:5]
            return prefix + "\n".join([head] + [_row_line(r) for r in rows])
        if plan.metric == Metric.COUNT:
            head = f"{label} {subject}결제는 총 {int(a.get('count') or 0)}번이에요 (합계 {_won(a.get('sum'))})."
        elif plan.metric == Metric.AVG and a.get("dailyAvg") is not None:
            head = f"{label} {subject}하루 평균 {_won(a.get('dailyAvg'))}을 썼어요 (지출한 날 {int(a.get('days') or 0)}일)."
        elif plan.metric == Metric.AVG:
            head = f"{label} {subject}평균 결제 금액은 {_won(a.get('avg'))}이에요 ({int(a.get('count') or 0)}건)."
        else:
            head = f"{label} {subject}지출은 총 {_won(a.get('sum'))}이에요 ({int(a.get('count') or 0)}건)."
        lines = [head]
        if groups:
            names = {GroupBy.METHOD: _METHOD_KO, GroupBy.PROVIDER: _PROVIDER_KO}.get(plan.groupBy, {})
            lines.append("")
            for g in groups[:5]:
                key = names.get(str(g["key"]), g["key"])
                if plan.groupBy == GroupBy.DAY:
                    key = _date_ko(str(key))
                lines.append(f"- {key}: {_won(g['sum'])} ({g['count']}건)")
        return prefix + "\n".join(lines)

    shown = result.transactions[:10]
    if plan.limit == 1 and shown:
        head = f"{label} {subject}가장 비싼 결제예요." if plan.metric == Metric.MAX else f"{subject}가장 최근 거래예요."
    elif plan.intent == Intent.SEARCH:
        head = f"{label} {subject}관련 거래 {result.total}건을 찾았어요."
    else:
        head = f"{label} {subject}거래 {result.total}건이에요."
    more = f"\n외 {result.total - len(shown)}건" if result.total > len(shown) else ""
    return prefix + head + "\n" + "\n".join(_row_line(r, asks_items) for r in shown) + more


_ASKS_ITEMS = re.compile(r"뭐\s*(샀|구매|먹)|뭘\s*샀|무엇을?\s*샀|품목|산\s*거|산\s*게|구매한\s*거")


def _answer_hints(message: str, plan: QueryPlan, result: Optional[QueryResult]) -> str:
    """LLM 이 자주 틀리는 부분을 코드가 판단해 명시 (규칙만으로는 잘 안 지킴)"""
    hints = []
    if result and result.total and plan.intent != Intent.AGGREGATE:
        hints.append("이번 결과는 목록 조회라 합계가 계산되어 있지 않습니다. 금액을 더해서 '총 …원' 이라고 말하지 마십시오.")
    if result and result.total == 0 and result.userTotal:
        hints.append(f"조건에 맞는 거래는 0건입니다. userTotal({result.userTotal}건)은 조건과 무관한 '저장된 전체 거래 수' 이므로 "
                     "조건과 연결해서(예: '택시 이용 8건') 말하지 마십시오.")
    if result and result.total and result.matchType == MatchType.SEMANTIC:
        hints.append("이 거래들은 검색어와 의미가 비슷해 보여 찾은 후보일 뿐, 질문한 물건을 실제로 샀는지는 확인되지 않았습니다. "
                     "'관련 있을 수 있는 거래' 라고만 말하고, 품목 목록에 없는 물건을 샀다고 단정하지 마십시오.")
    if result and result.total and plan.status in (TransactionStatus.CANCELED, TransactionStatus.REFUNDED):
        found = {r.status for r in result.transactions}
        asked = _STATUS_KO[plan.status.value]
        other = _STATUS_KO["REFUNDED" if plan.status == TransactionStatus.CANCELED else "CANCELED"]
        if plan.status.value not in found:
            hints.append(f"사용자는 '{asked}' 을 물었지만 조회된 거래는 모두 '{other}' 입니다. "
                         f"'{asked} 내역은 없지만 {other}된 거래가 있다' 고 구분해서 말하십시오.")
        else:
            hints.append("취소(CANCELED)와 환불(REFUNDED)을 함께 조회했습니다. 거래마다 취소인지 환불인지 구분해서 말하십시오.")
    if result and result.itemAggregates and plan.itemTerms:
        ia = result.itemAggregates
        hints.append(f"품목 기준 집계: 질문한 품목의 금액 합계 {int(ia.get('sum') or 0):,}원, 수량 {int(ia.get('quantity') or 0)}개 "
                     f"(품목 {int(ia.get('lines') or 0)}줄). '담배에 쓴 돈', '몇 갑' 같은 품목 질문에는 반드시 이 값을 쓰십시오. "
                     "거래 전체 결제 금액(transactionSum, 다른 품목 포함)과 혼동하지 마십시오. "
                     "각 거래의 matchedItems 가 질문 품목에 해당하는 품목입니다.")
    if result and result.aggregates.get("dailyAvg") is not None:
        hints.append(f"'하루 평균' 은 aggregates.dailyAvg ({int(result.aggregates['dailyAvg']):,}원, 지출이 있었던 "
                     f"{int(result.aggregates.get('days') or 0)}일 기준) 입니다. avg(건당 평균)와 구분하십시오.")
    if result and plan.groupBy == GroupBy.ITEM and result.groups:
        hints.append("groups 는 품목별 순위입니다 (count=수량, sum=금액). 1위 품목을 먼저 말하십시오.")
    if result and plan.groupBy == GroupBy.MERCHANT and plan.metric == Metric.COUNT and result.groups:
        hints.append("groups 는 방문 횟수 순위입니다 (count=방문 횟수). 1위 가맹점과 횟수를 먼저 말하십시오.")
    if result and result.periodLabel == "전체 기간":
        hints.append("기간 조건이 없습니다. '이번 기간 동안' 같은 표현 대신 '저장된 거래 중에는' 처럼 말하십시오.")
    if result and result.transactions and _ASKS_ITEMS.search(message):
        shown = result.transactions[:10]
        with_items = [r for r in shown if r.items]
        without = [r for r in shown if not r.items]
        if not with_items:
            hints.append("조회된 거래에는 품목 정보가 없습니다 (결제앱 캡처 등). "
                         "'품목 정보는 저장되어 있지 않다' 고 먼저 말하고 가맹점·날짜·금액을 알려주십시오.")
        else:
            # 거래별 사실을 코드가 정리해서 전달 (이전 답변의 '품목 정보 없음' 을 따라 하는 사례가 있었음)
            facts = [f"{r.merchant}({_date_ko(r.date)}): {', '.join(r.items[:8])}" for r in with_items]
            hint = "품목 정보가 있는 거래 → 반드시 품목을 나열하십시오: " + " / ".join(facts)
            if without:
                hint += (". 품목 정보가 없는 거래(이 거래에 대해서만 '품목 정보 없음' 이라고 말함): "
                         + ", ".join(f"{r.merchant}({_date_ko(r.date)})" for r in without))
            hints.append(hint + ". 이전 답변 내용과 다르더라도 이번 조회 결과가 맞습니다.")
    years = {r.date[:4] for r in (result.transactions if result else []) if r.date}
    this_year = str(period_mod.today().year)
    if years - {this_year}:
        hints.append(f"올해는 {this_year}년입니다. 올해가 아닌 거래의 날짜는 '2021년 10월 31일' 처럼 연도를 붙이십시오.")
    return "".join(f"참고: {h}\n" for h in hints)


def _date_ko(d: Optional[str]) -> str:
    """'2026-11-12' → 올해면 '11월 12일', 아니면 '2021년 10월 31일'"""
    if not d or len(d) < 10:
        return "날짜 미상"
    y, m, day = d[:4], int(d[5:7]), int(d[8:10])
    prefix = "" if y == str(period_mod.today().year) else f"{y}년 "
    return f"{prefix}{m}월 {day}일"


async def stream_template(text: str) -> AsyncIterator[str]:
    """템플릿 답변도 스트리밍처럼 조금씩 전송 (프론트 처리 일원화)"""
    for i in range(0, len(text), 8):
        yield text[i:i + 8]
        await asyncio.sleep(0.01)


class LlmAnswerer:
    def __init__(self, guard: QuotaGuard):
        self._guard = guard
        self._cfg = settings.chat

    @property
    def model_name(self) -> str:
        return self._cfg.model_name

    def unavailable_reason(self) -> Optional[str]:
        if not settings.llm.api_key:
            return "GEMINI_API_KEY 미설정"
        return self._guard.message(self.model_name)

    async def stream(
        self, message: str, history: List[ChatTurn], plan: QueryPlan, result: Optional[QueryResult],
    ) -> AsyncIterator[str]:
        from google.genai import types

        context = {
            "plan": plan.model_dump(mode="json", exclude_none=True),
            "result": result.model_dump(mode="json") if result else None,
        }
        prompt = (f"조회 결과:\n```json\n{json.dumps(context, ensure_ascii=False)}\n```\n"
                  f"{_answer_hints(message, plan, result)}\n질문: {message}")
        turns = history[-self._cfg.history_turns:]
        contents = [
            types.Content(role="user" if h.role == "user" else "model", parts=[types.Part(text=h.content)])
            for h in turns
        ] + [types.Content(role="user", parts=[types.Part(text=prompt)])]
        config = types.GenerateContentConfig(
            system_instruction=ANSWER_INSTRUCTION, temperature=0.3,
            thinking_config=types.ThinkingConfig(thinking_budget=0),
        )
        client = get_client(settings.llm.api_key)
        async for text in stream_with_retry(
            lambda: client.aio.models.generate_content_stream(
                model=self.model_name, contents=contents, config=config),
            guard=self._guard, model_name=self.model_name, timeout=settings.chat.timeout_sec,
        ):
            yield text
