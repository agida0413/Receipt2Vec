"""
가계부 챗봇 오케스트레이션 → SSE 이벤트 스트림

  질문 → [저장소] 사용자 어휘(가맹점·업종) 조회
       → [플래너] 질문 전처리/증강 → QueryPlan (가맹점/품목/지역/업종/의미검색 문장 + 필터)
       → [코드] 날짜 계산·userId 강제 → TransactionQuery
       → [OpenSearch] 집계/목록/검색 (정확 → 완화 → 의미 검색)
       → [답변기] 스트리밍 답변

이벤트 (event 이름 / data JSON)
  status : {"step": "plan|query|answer", "message": "..."}  진행 상황
  plan   : {"plan": QueryPlan, "searchTerms": [...], "planner": "...", "range": {...}|null}
  result : QueryResult (집계 숫자 + 근거 거래 + matchType)
  token  : {"text": "..."}                                    답변 조각
  notice : {"message": "..."}                                 LLM 대체 사용 등 안내
  error  : {"message": "..."}
  done   : {"planner": "...", "answerer": "...", "elapsedMs": n}
"""
import json
import logging
import time
from typing import Any, AsyncIterator, Dict, List, Optional

from fastapi.concurrency import run_in_threadpool

from app.core.config import settings
from app.core.logging_setup import short
from app.services.chat import period as period_mod
from app.services.chat.answerer import LlmAnswerer, stream_template, template_answer
from app.services.chat.gemini import QuotaExceededError, QuotaGuard, shared_quota_guard
from app.services.chat.planner import LlmPlanner, make_plan
from app.services.chat.schemas import ChatRequest, GroupBy, Intent, Metric, QueryPlan, QueryResult, TransactionStatus
from app.services.transactions.enrich import CATEGORY_ALIASES, ITEM_CATEGORY_ALIASES
from app.services.transactions.schemas import (
    GroupOrder, MatchType, SortBy, TransactionQuery, TransactionQueryResult, UserVocabulary,
)
from app.services.transactions.store import StoreUnavailableError, TransactionStore, transaction_store

logger = logging.getLogger(__name__)


def sse(event: str, data: Dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"


_REVERSED = (TransactionStatus.CANCELED, TransactionStatus.REFUNDED)


def plan_to_query(user_id: str, plan: QueryPlan, rng: Optional[period_mod.DateRange]) -> TransactionQuery:
    """챗봇 QueryPlan → 저장소 조회 조건 (userId 는 요청값으로 강제)"""
    max_rows = settings.chat.max_result_rows
    enum = lambda v: v.value if v is not None else None  # noqa: E731
    return TransactionQuery(
        userId=user_id, mode=plan.intent.value,
        startDate=rng.start if rng else None, endDate=rng.end if rng else None,
        keywords=plan.keywords, merchantTerms=plan.merchantTerms, itemTerms=plan.itemTerms,
        locationTerms=plan.locationTerms, categories=plan.categories, semanticQuery=plan.semanticQuery,
        provider=enum(plan.provider), method=enum(plan.method), cardCompany=enum(plan.cardCompany),
        # 사용자는 취소·환불을 섞어 말함 ('환불한 거 있어?' 인데 데이터는 결제 취소) → 둘 다 조회, 답변에서 구분
        status=None if plan.status in _REVERSED else enum(plan.status),
        statuses=[s.value for s in _REVERSED] if plan.status in _REVERSED else [],
        minAmount=plan.minAmount, maxAmount=plan.maxAmount,
        weekdays=plan.weekdays, hourFrom=plan.hourFrom, hourTo=plan.hourTo,
        sortBy=SortBy.AMOUNT if plan.metric == Metric.MAX else SortBy.DATE,
        groupBy=plan.groupBy.value,
        groupOrder=GroupOrder.COUNT if plan.metric == Metric.COUNT else GroupOrder.SUM, limit=max(1, min(plan.limit or max_rows, max_rows)),
    )


_TERM_FIELDS = ("keywords", "merchantTerms", "itemTerms", "locationTerms", "categories")


def inferred_only_terms(plan: QueryPlan, message: str) -> List[str]:
    """검색어가 모두 질문에 없는 단어(LLM 이 추론해 덧붙인 것)면 그 목록, 하나라도 질문에 있으면 []"""
    text = message.replace(" ", "")
    terms = plan.search_terms()
    if not terms:
        return []
    for t in terms:
        if t.replace(" ", "") in text:
            return []
        # 별칭으로 질문에 있는 경우 ('커피숍' → 카페, '술' → 주류) 도 질문에 있는 것으로 본다
        for alias, cat in list(CATEGORY_ALIASES.items()) + list(ITEM_CATEGORY_ALIASES.items()):
            if cat == t and alias in text:
                return []
    return terms


def item_level(plan: QueryPlan, found: TransactionQueryResult) -> TransactionQueryResult:
    """
    품목 질문의 합계는 품목 기준으로 확정 ('담배에 쓴 돈' = 담배 품목 금액, 편의점 결제 전체가 아님).
    답변 LLM 이 거래 합계를 고르는 실수를 막으려고 aggregates 자체를 품목 값으로 바꾸고 거래 합계는 따로 둔다.
    """
    ia = found.itemAggregates
    if plan.intent != Intent.AGGREGATE or not plan.itemTerms or not ia or plan.groupBy != GroupBy.NONE:
        return found
    agg = dict(found.aggregates)
    found.aggregates = {"sum": ia.get("sum"), "quantity": ia.get("quantity"), "count": agg.get("count"),
                        "transactionSum": agg.get("sum")}
    return found


def daily_average(plan: QueryPlan, found: TransactionQueryResult) -> TransactionQueryResult:
    """'하루 평균' = 합계 ÷ 지출이 있었던 날 수 (aggregates.avg 는 건당 평균이라 다름)"""
    if plan.metric == Metric.AVG and plan.groupBy == GroupBy.DAY and found.groups:
        days = len(found.groups)
        found.aggregates = {**found.aggregates, "dailyAvg": round((found.aggregates.get("sum") or 0) / days),
                            "days": days}
    return found


def _filters(plan: QueryPlan) -> Dict[str, Any]:
    """로그용: 값이 있는 필터만"""
    keys = ("provider", "method", "cardCompany", "status", "minAmount", "maxAmount", "weekdays", "hourFrom", "hourTo",
            "limit")
    out = {}
    for k in keys:
        v = getattr(plan, k)
        if v not in (None, []):
            out[k] = v.value if hasattr(v, "value") else v
    return out


class ChatService:
    def __init__(self, store: TransactionStore, guard: QuotaGuard):
        self.store = store
        self.planner = LlmPlanner(guard)
        self.answerer = LlmAnswerer(guard)

    async def _vocabulary(self, user_id: str) -> Optional[UserVocabulary]:
        """LLM 플래너를 쓸 때만 조회 (규칙 플래너는 필요 없음). 실패해도 질문 처리는 계속"""
        if settings.chat.planner == "rule" or self.planner.unavailable_reason():
            return None
        try:
            return await run_in_threadpool(self.store.vocabulary, user_id, settings.chat.vocabulary_size)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"사용자 어휘 조회 실패 → 어휘 없이 해석: {e}")
            return None

    async def stream(self, req: ChatRequest) -> AsyncIterator[str]:
        started = time.perf_counter()
        logger.info(f"질문 접수 user={req.userId} history={len(req.history)} q={short(req.message, 200)}")
        try:
            # 1) 질문 전처리 (사용자 데이터 어휘로 증강)
            yield sse("status", {"step": "plan", "message": "질문을 이해하는 중…"})
            t0 = time.perf_counter()
            vocab = await self._vocabulary(req.userId)
            plan, planner_name, notice = await make_plan(req.message, req.history, self.planner, vocab)
            if notice:
                logger.warning(f"질문 해석 대체 사용 user={req.userId} planner={planner_name} reason={short(notice)}")
                yield sse("notice", {"message": notice})
            rng = period_mod.resolve(plan.period)
            logger.info(
                f"질문 해석 user={req.userId} planner={planner_name} ms={int((time.perf_counter() - t0) * 1000)} "
                f"intent={plan.intent.value} period={rng.label if rng else '전체'} metric={plan.metric.value} "
                f"groupBy={plan.groupBy.value} terms={plan.search_terms()} "
                f"filters={_filters(plan)} vocab={vocab.total if vocab else '-'}")
            yield sse("plan", {
                "plan": plan.model_dump(mode="json"), "searchTerms": plan.search_terms(), "planner": planner_name,
                "range": {"start": rng.start.isoformat(), "end": rng.end.isoformat(), "label": rng.label}
                if rng else None,
            })

            # 2) 조회
            result: Optional[QueryResult] = None
            if plan.intent != Intent.SMALLTALK:
                yield sse("status", {"step": "query", "message": "거래를 조회하는 중…"})
                try:
                    t0 = time.perf_counter()
                    found = await run_in_threadpool(self.store.query, plan_to_query(req.userId, plan, rng))
                    inferred = inferred_only_terms(plan, req.message)
                    if found.total == 0 and inferred and not req.history:
                        # 질문에 없는 검색어를 LLM 이 덧붙여 0건 (실측: '어제 뭐 먹었지' → 업종 '음식점' 추가 → 편의점 누락)
                        logger.info(f"질문에 없는 검색어 {inferred} 로 0건 → 검색어 빼고 재조회")
                        loose = plan.model_copy(update={k: [] for k in _TERM_FIELDS} | {"semanticQuery": None})
                        retry = await run_in_threadpool(self.store.query, plan_to_query(req.userId, loose, rng))
                        if retry.total:
                            retry.matchType = MatchType.RELAXED
                            found = retry
                    found = daily_average(plan, item_level(plan, found))
                except StoreUnavailableError as e:
                    logger.error(f"거래 저장소 연결 실패 user={req.userId} error={e}")
                    yield sse("error", {"message": "거래 저장소(OpenSearch)에 연결할 수 없어요. "
                                                   "docker compose 로 opensearch 가 떠 있는지 확인해 주세요."})
                    return
                result = QueryResult(**found.model_dump(), intent=plan.intent,
                                     periodLabel=rng.label if rng else "전체 기간")
                logger.info(
                    f"거래 조회 user={req.userId} ms={int((time.perf_counter() - t0) * 1000)} total={result.total} "
                    f"match={result.matchType.value} sum={result.aggregates.get('sum')} "
                    f"itemSum={result.itemAggregates.get('sum') if result.itemAggregates else None} "
                    f"groups={[g.get('key') for g in result.groups[:3]]} userTotal={result.userTotal}")
                if result.total == 0:
                    logger.warning(f"조회 결과 0건 user={req.userId} q={short(req.message)} terms={plan.search_terms()} "
                                   f"period={rng.label if rng else '전체'} stored={result.userTotal}")
                yield sse("result", result.model_dump(mode="json"))

            # 3) 답변
            yield sse("status", {"step": "answer", "message": "답변을 작성하는 중…"})
            used: Dict[str, str] = {}
            t0 = time.perf_counter()
            chars = 0
            async for ev in self._answer(req, plan, result, used):
                if ev.startswith("event: token"):
                    chars += len(json.loads(ev.split("data: ", 1)[1])["text"])
                yield ev
            total_ms = int((time.perf_counter() - started) * 1000)
            logger.info(f"답변 완료 user={req.userId} answerer={used.get('answerer', 'template')} chars={chars} "
                        f"answer_ms={int((time.perf_counter() - t0) * 1000)} total_ms={total_ms}")
            yield sse("done", {"planner": planner_name, "answerer": used.get("answerer", "template"),
                               "elapsedMs": total_ms})
        except Exception as e:  # noqa: BLE001
            logger.exception(f"챗봇 처리 실패 user={req.userId} q={short(req.message)} error={type(e).__name__}: {e}")
            yield sse("error", {"message": f"처리 중 오류가 발생했어요: {type(e).__name__}"})

    async def _answer(self, req: ChatRequest, plan, result, used: Dict[str, str]) -> AsyncIterator[str]:
        """LLM 스트리밍 답변. 못 쓰거나 첫 조각 전에 실패하면 템플릿 답변"""
        mode = settings.chat.answerer
        smalltalk = plan.intent == Intent.SMALLTALK
        reason = None if mode == "template" else self.answerer.unavailable_reason()

        if mode != "template" and reason is None and not smalltalk:
            sent = False
            try:
                async for text in self.answerer.stream(req.message, req.history, plan, result):
                    sent = True
                    yield sse("token", {"text": text})
                used["answerer"] = f"llm:{self.answerer.model_name}"
                return
            except Exception as e:  # noqa: BLE001
                logger.warning(f"LLM 답변 실패 → 템플릿 사용 user={req.userId} sent={sent} error={type(e).__name__}: {e}")
                if sent:
                    used["answerer"] = f"llm:{self.answerer.model_name}(중단)"
                    yield sse("token", {"text": "\n\n(답변 생성이 중단되었어요)"})
                    return
                msg = str(e) if isinstance(e, QuotaExceededError) else f"답변 LLM 실패로 기본 답변 사용 ({type(e).__name__})"
                yield sse("notice", {"message": msg})
        elif reason and mode == "auto" and not smalltalk:
            yield sse("notice", {"message": reason})

        used["answerer"] = "template"
        async for text in stream_template(template_answer(plan, result, req.message)):
            yield sse("token", {"text": text})


chat_service = ChatService(transaction_store, shared_quota_guard)
