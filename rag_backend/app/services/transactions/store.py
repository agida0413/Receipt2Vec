"""
거래 저장소 (OpenSearch) — 영수증·결제내역 거래의 저장/조회를 전담

색인 (v2)
- 별칭(alias) 'receipt-transactions' → 실제 인덱스 'receipt-transactions-v2'.
  매핑을 바꿀 때는 INDEX_VERSION 을 올리면 기동 시 새 인덱스를 만들고 기존 문서를 다시 보강해 옮긴 뒤 별칭을 전환한다.
- 저장 시 파생 필드: 업종·브랜드·정규화 가맹점명·요일·시각·검색문장·임베딩 (enrich.py)
- 가맹점명·품목·주소에 ngram 하위 필드 → '하나로' 처럼 형태소로 안 쪼개지는 부분 일치도 검색
- 인덱스 1개 + userId 필터 강제, dynamic=strict (정의하지 않은 필드 저장 거부)

검색 (단계적 완화)
  1) EXACT    : 모든 검색어 그룹 일치 (그룹 간 AND, 그룹 안은 동의어 OR)
  2) RELAXED  : 0건이면 그룹 하나는 빠져도 허용
  3) SEMANTIC : 그래도 0건이면 임베딩 의미 검색 (최소 점수 이상만)
  합계/건수는 항상 OpenSearch 집계로 계산 (LLM 이 숫자를 더하지 않게)
"""
import hashlib
import logging
import re
import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

from opensearchpy import OpenSearch, helpers

from app.core.config import settings
from app.services.transactions.enrich import (
    category_brands, embedding_text, enrich, to_category, to_item_category,
)
from app.services.transactions.schemas import (
    GroupBy, GroupOrder, MatchType, QueryMode, SortBy, TransactionQuery, TransactionQueryResult, TransactionRow,
    UserVocabulary,
)

logger = logging.getLogger(__name__)

APPROVED = "APPROVED"
INDEX_VERSION = 3  # v3: items[].category (품목 분류) 추가
DERIVED_FIELDS = ("dedupKey", "searchText", "createdAt", "embedding", "dayOfWeek", "transactedHour")
DERIVED_MERCHANT_FIELDS = ("category", "brand", "normalizedName")


class StoreUnavailableError(RuntimeError):
    """OpenSearch 에 연결할 수 없음 → 503"""


class InvalidTransactionError(ValueError):
    """매핑과 맞지 않는 문서 → 422"""


_KW = {"type": "keyword"}
_NGRAM = {"ngram": {"type": "text", "analyzer": "ngram_analyzer"}}
_NAME_TEXT = {"type": "text", "analyzer": "nori_analyzer",
              "fields": {"keyword": {"type": "keyword", "ignore_above": 256}, **_NGRAM}}


def index_body(dimension: int) -> Dict[str, Any]:
    return {
        "settings": {
            "index": {"number_of_shards": 1, "number_of_replicas": 0, "knn": True},
            "analysis": {
                "tokenizer": {
                    "nori_user_tokenizer": {"type": "nori_tokenizer", "decompound_mode": "mixed"},
                    "ngram_tokenizer": {"type": "ngram", "min_gram": 2, "max_gram": 3,
                                        "token_chars": ["letter", "digit"]},
                },
                "analyzer": {
                    "nori_analyzer": {"type": "custom", "tokenizer": "nori_user_tokenizer", "filter": ["lowercase"]},
                    "ngram_analyzer": {"type": "custom", "tokenizer": "ngram_tokenizer", "filter": ["lowercase"]},
                },
            },
        },
        "mappings": {
            "dynamic": "strict",
            "properties": {
                "schemaVersion": {"type": "integer"},
                "transactionId": _KW,
                "userId": _KW,
                "dedupKey": _KW,
                "source": {"properties": {
                    "type": _KW, "provider": _KW, "documentId": _KW,
                    "fileName": {"type": "keyword", "index": False}, "ocrScore": {"type": "float"},
                }},
                "merchant": {"properties": {
                    "name": _NAME_TEXT,
                    "normalizedName": _KW,                 # 집계(가맹점별)용
                    "brand": _KW,                          # GS25, 스타벅스 …
                    "category": _KW,                       # 편의점, 카페 …
                    "businessNumber": _KW,
                    "address": {"type": "text", "analyzer": "nori_analyzer", "fields": _NGRAM},
                    "phone": _KW,
                }},
                "transactedAt": {"type": "date"},
                "transactedDate": {"type": "date", "format": "yyyy-MM-dd"},
                "dayOfWeek": {"type": "byte"},             # 1=월 … 7=일
                "transactedHour": {"type": "byte"},
                "status": _KW,
                "amount": {"properties": {
                    "total": {"type": "long"}, "currency": _KW, "supply": {"type": "long"},
                    "vat": {"type": "long"}, "discount": {"type": "long"},
                }},
                "payment": {"properties": {
                    "method": _KW, "provider": _KW, "cardCompany": _KW, "cardLast4": _KW,
                    "installmentMonths": {"type": "integer"}, "approvalNumber": _KW,
                }},
                "items": {"type": "nested", "properties": {
                    "name": _NAME_TEXT,
                    "category": _KW,                       # 품목 분류 (담배, 주류, 고기 …) — 여러 개 가능
                    "quantity": {"type": "integer"}, "unitPrice": {"type": "long"}, "amount": {"type": "long"},
                }},
                "quality": {"properties": {
                    "needsReview": {"type": "boolean"}, "reviewReasons": _KW, "missingFields": _KW,
                    "itemsSumMatchesTotal": {"type": "boolean"}, "confirmed": {"type": "boolean"},
                }},
                "extra": {"type": "flat_object"},
                "raw": {"properties": {
                    "text": {"type": "text", "analyzer": "nori_analyzer"}, "parserName": _KW, "parserVersion": _KW,
                }},
                "searchText": {"type": "text", "analyzer": "nori_analyzer", "fields": _NGRAM},
                "embedding": {"type": "knn_vector", "dimension": dimension,
                              "method": {"name": "hnsw", "engine": "lucene", "space_type": "cosinesimil"}},
                "createdAt": {"type": "date"},
            },
        },
    }


def _get(d: Optional[Dict[str, Any]], *path: str) -> Any:
    for key in path:
        if not isinstance(d, dict):
            return None
        d = d.get(key)
    return d


def dedup_key(user_id: str, tx: Dict[str, Any]) -> str:
    """같은 결제가 영수증·결제앱 캡처로 두 번 올라와도 한 건만 저장되도록"""
    total = _get(tx, "amount", "total")
    approval = _get(tx, "payment", "approvalNumber")
    if approval:
        basis = f"{user_id}|appr|{approval}|{total}"
    else:
        merchant = re.sub(r"\s+", "", _get(tx, "merchant", "name") or "")
        basis = f"{user_id}|{tx.get('transactedAt') or tx.get('transactedDate')}|{total}|{merchant}"
    return hashlib.sha1(basis.encode("utf-8")).hexdigest()


def strip_derived(doc: Dict[str, Any]) -> Dict[str, Any]:
    """저장소가 만든 파생 필드 제거 → 원본 거래 (재보강·이관 시 사용)"""
    out = {k: v for k, v in doc.items() if k not in DERIVED_FIELDS}
    if isinstance(out.get("merchant"), dict):
        out["merchant"] = {k: v for k, v in out["merchant"].items() if k not in DERIVED_MERCHANT_FIELDS}
    if isinstance(out.get("items"), list):
        out["items"] = [{k: v for k, v in i.items() if k != "category"} for i in out["items"] if isinstance(i, dict)]
    return out


def to_row(src: Dict[str, Any]) -> TransactionRow:
    at = src.get("transactedAt") or ""
    pay = src.get("payment") or {}
    merchant = src.get("merchant") or {}
    return TransactionRow(
        transactionId=src["transactionId"],
        date=src.get("transactedDate"),
        time=at[11:16] if len(at) >= 16 else None,
        merchant=merchant.get("name"),
        category=merchant.get("category"),
        amount=(src.get("amount") or {}).get("total"),
        status=src.get("status", APPROVED),
        method=pay.get("method"),
        provider=pay.get("provider"),
        cardCompany=pay.get("cardCompany"),
        items=[i.get("name", "") for i in (src.get("items") or [])][:10],
    )


# ============================================================================
# 쿼리 생성
# ============================================================================
def build_filters(q: TransactionQuery) -> List[dict]:
    f: List[dict] = [{"term": {"userId": q.userId}}]  # 사용자 격리: 호출 측 조건과 무관하게 항상 강제
    if q.startDate or q.endDate:
        rng: Dict[str, str] = {}
        if q.startDate:
            rng["gte"] = q.startDate.isoformat()
        if q.endDate:
            rng["lt"] = q.endDate.isoformat()
        f.append({"range": {"transactedDate": rng}})
    for field, value in (("payment.provider", q.provider), ("payment.method", q.method),
                         ("payment.cardCompany", q.cardCompany)):
        if value:
            f.append({"term": {field: value}})
    if q.statuses:
        f.append({"terms": {"status": sorted(set(q.statuses))}})
    elif q.status:
        f.append({"term": {"status": q.status}})
    elif q.mode == QueryMode.AGGREGATE or q.sortBy == SortBy.AMOUNT:
        # 합계/건수와 '제일 비싼 거' 는 정상 승인 건만 (취소·환불 제외)
        f.append({"term": {"status": APPROVED}})
    if q.minAmount is not None or q.maxAmount is not None:
        amt: Dict[str, int] = {}
        if q.minAmount is not None:
            amt["gte"] = q.minAmount
        if q.maxAmount is not None:
            amt["lte"] = q.maxAmount
        f.append({"range": {"amount.total": amt}})
    if q.weekdays:
        f.append({"terms": {"dayOfWeek": sorted(set(q.weekdays))}})
    if q.hourFrom is not None or q.hourTo is not None:
        lo = q.hourFrom if q.hourFrom is not None else 0
        hi = q.hourTo if q.hourTo is not None else 23
        if lo <= hi:
            f.append({"range": {"transactedHour": {"gte": lo, "lte": hi}}})
        else:  # 22시~2시처럼 자정을 넘는 구간
            f.append({"bool": {"should": [{"range": {"transactedHour": {"gte": lo}}},
                                          {"range": {"transactedHour": {"lte": hi}}}],
                               "minimum_should_match": 1}})
    return f


def _any(clauses: List[dict]) -> dict:
    return {"bool": {"should": clauses, "minimum_should_match": 1}}


def _match(field: str, term: str, boost: float = 1.0) -> dict:
    """여러 형태소로 나뉘는 검색어는 모두 일치해야 함 (OR 이면 '사직점' 의 '점' 만으로도 다른 가게가 걸림)"""
    return {"match": {field: {"query": term, "operator": "and", "boost": boost}}}


def _merchant_clauses(term: str) -> List[dict]:
    return [
        _match("merchant.name", term, 3),
        _match("merchant.name.ngram", term, 2),   # '하나로' 처럼 형태소로 안 쪼개지는 부분 일치
        {"term": {"merchant.brand": {"value": term, "boost": 3}}},
    ]


def _item_inner(term: str) -> List[dict]:
    """품목 1개 조건 (nested 안쪽): 품목명 / 부분일치 / 품목 분류('담배' → 에쎄·말보로 …)"""
    clauses = [_match("items.name", term, 2), _match("items.name.ngram", term)]
    cat = to_item_category(term)
    if cat:
        clauses.append({"term": {"items.category": {"value": cat, "boost": 2}}})
    return clauses


def _item_clauses(term: str) -> List[dict]:
    return [{"nested": {"path": "items", "score_mode": "max", "query": _any(_item_inner(term))}}]


def item_filter(q: TransactionQuery) -> Optional[dict]:
    """품목 단위 집계·표시용 조건 (어떤 품목이 질문에 해당하는지)"""
    inner = [c for t in q.itemTerms for c in _item_inner(t)]
    return _any(inner) if inner else None


def _location_clauses(term: str) -> List[dict]:
    return [
        _match("merchant.name", term, 2), _match("merchant.name.ngram", term),
        _match("merchant.address", term), _match("merchant.address.ngram", term),
    ]


def _category_clauses(category: str) -> List[dict]:
    """업종: 저장 시 분류된 category + 브랜드 사전(분류 누락 대비) + 가맹점명에 업종 단어"""
    clauses: List[dict] = [{"term": {"merchant.category": {"value": category, "boost": 3}}},
                           {"match_phrase": {"merchant.name": {"query": category, "boost": 2}}}]
    clauses += [{"match_phrase": {"merchant.name": {"query": b, "boost": 2}}} for b in category_brands(category)]
    return clauses


def _with_category(term: str, clauses: List[dict]) -> List[dict]:
    """'편의점' 처럼 업종 단어가 가맹점/키워드 자리에 와도 업종으로도 찾는다"""
    category = to_category(term)
    return clauses + _category_clauses(category) if category else clauses


def text_groups(q: TransactionQuery) -> List[dict]:
    """검색어 그룹 목록 (그룹 간 AND). 그룹 안은 동의어/확장어 OR"""
    groups: List[dict] = []
    if q.merchantTerms:
        groups.append(_any([c for t in q.merchantTerms for c in _with_category(t, _merchant_clauses(t))]
                           + [_match("searchText", t) for t in q.merchantTerms]))
    if q.locationTerms:
        groups.append(_any([c for t in q.locationTerms for c in _location_clauses(t)]))
    # 품목과 업종은 '무엇을' 의 다른 표현 ('커피' → 아메리카노 품목 OR 카페 업종) → 한 그룹으로 OR
    # (결제앱 내역은 품목이 없어서 AND 로 묶으면 카페 거래가 빠짐)
    what: List[dict] = []
    flt = item_filter(q)
    if flt:
        what.append({"nested": {"path": "items", "score_mode": "max", "query": flt, "inner_hits": {
            "name": "matched_items", "size": 20, "_source": ["items.name", "items.quantity", "items.amount"]}}})
    what += [c for cat in q.categories for c in _category_clauses(to_category(cat) or cat)]
    if what:
        groups.append(_any(what))
    for kw in q.keywords:  # 성격을 모르는 단어: 각각 하나의 그룹, 모든 필드에서
        groups.append(_any(_with_category(kw, _merchant_clauses(kw) + _item_clauses(kw) + _location_clauses(kw)
                                          + [_match("searchText", kw), _match("searchText.ngram", kw)])))
    return groups


def keyword_query(keyword: str) -> dict:
    """공백으로 나뉜 단어들 → 단어별 그룹 AND (단건 조회·테스트용 편의 함수)"""
    q = TransactionQuery(userId="_", mode=QueryMode.SEARCH, keywords=[t for t in keyword.split() if t])
    groups = text_groups(q)
    if not groups:
        return {"match_all": {}}
    return groups[0] if len(groups) == 1 else {"bool": {"must": groups}}


def _shape(body: Dict[str, Any], q: TransactionQuery, scored: bool) -> Dict[str, Any]:
    if q.mode == QueryMode.AGGREGATE:
        body["size"] = min(q.limit, 10)
        body["sort"] = _AMOUNT_SORT
        body["aggs"] = {
            "sum": {"sum": {"field": "amount.total"}},
            "count": {"value_count": {"field": "transactionId"}},
            "avg": {"avg": {"field": "amount.total"}},
            "max": {"max": {"field": "amount.total"}},
        }
        group = group_agg(q.groupBy, q.groupOrder, by_amount=q.sortBy == SortBy.AMOUNT)
        if group:
            body["aggs"]["groups"] = group
    if q.itemTerms or q.groupBy == GroupBy.ITEM:
        body.setdefault("aggs", {})["items"] = item_aggs(q)
    if q.mode != QueryMode.AGGREGATE:
        body["size"] = q.limit
        if q.sortBy == SortBy.AMOUNT:
            body["sort"] = _AMOUNT_SORT
        elif q.mode == QueryMode.SEARCH and scored:
            body["sort"] = ["_score"] + _DATE_SORT
        else:
            body["sort"] = _DATE_SORT
    return body


_DATE_SORT = [{"transactedDate": {"order": "desc", "missing": "_last"}},
              {"transactedAt": {"order": "desc", "missing": "_last"}}]
_AMOUNT_SORT = [{"amount.total": {"order": "desc", "missing": "_last"}}]


def build_search_body(q: TransactionQuery, relaxed: bool = False,
                      ids: Optional[List[str]] = None) -> Dict[str, Any]:
    bool_q: Dict[str, Any] = {"filter": build_filters(q)}
    if ids is not None:
        bool_q["filter"].append({"ids": {"values": ids}})
    groups = text_groups(q)
    if groups:
        if relaxed and len(groups) > 1:
            bool_q["should"] = groups
            bool_q["minimum_should_match"] = len(groups) - 1
        else:
            bool_q["must"] = groups
    body: Dict[str, Any] = {"query": {"bool": bool_q}, "track_total_hits": True}
    return _shape(body, q, scored=bool(groups))


SEMANTIC_CANDIDATES = 15  # 리랭커(CPU) 비용: 후보 × 질의 수


def build_candidate_body(q: TransactionQuery, vector: List[float]) -> Dict[str, Any]:
    """의미 검색 1단계: 벡터 근접 후보 (사용자·기간·결제수단 필터는 그대로 적용)"""
    return {
        "size": SEMANTIC_CANDIDATES,
        "query": {"knn": {"embedding": {"vector": vector, "k": SEMANTIC_CANDIDATES,
                                        "filter": {"bool": {"filter": build_filters(q)}}}}},
        "_source": {"excludes": ["embedding"]},
    }


def semantic_text(q: TransactionQuery) -> Optional[str]:
    if q.semanticQuery:
        return q.semanticQuery
    words = q.categories + q.merchantTerms + q.itemTerms + q.locationTerms + q.keywords
    return " ".join(words) or None


def rerank_query(q: TransactionQuery) -> str:
    """
    리랭커 채점 질의 1개. 실측상 짧은 명사('커피')가 문장('커피 마신 곳')보다 변별력이 높고,
    CPU 에서 질의 1개 × 후보 15건이 1~2초라 여러 질의를 돌리지 않는다.
    """
    for words in (q.categories, q.itemTerms, q.merchantTerms, q.keywords, q.locationTerms):
        if words:
            return words[0]
    return q.semanticQuery or ""


def rerank_text(doc: Dict[str, Any]) -> str:
    """리랭커 입력 문장 (짧을수록 빠름): 가맹점 · 업종 · 품목 5개"""
    m = doc.get("merchant") or {}
    items = [i.get("name") for i in (doc.get("items") or []) if i.get("name")][:5]
    parts = [m.get("name") or "", m.get("category") or ""] + items
    return " / ".join(p for p in parts if p)


def _without_text(q: TransactionQuery) -> TransactionQuery:
    return q.model_copy(update={"keywords": [], "merchantTerms": [], "itemTerms": [], "locationTerms": [],
                                "categories": [], "semanticQuery": None})


def item_aggs(q: TransactionQuery) -> dict:
    """
    품목 단위 집계: '담배에 쓴 돈' 은 편의점 결제 전체가 아니라 담배 품목 금액의 합,
    '뭐 많이 폈지' 는 품목별 수량 순위
    """
    order = ([{"qty": "desc"}, {"_count": "desc"}] if q.groupOrder == GroupOrder.COUNT
             else [{"sum": "desc"}, {"qty": "desc"}])
    sums = {"sum": {"sum": {"field": "items.amount"}}, "qty": {"sum": {"field": "items.quantity"}}}
    return {"nested": {"path": "items"}, "aggs": {"m": {
        "filter": item_filter(q) or {"match_all": {}},
        "aggs": {**sums, "names": {"terms": {"field": "items.name.keyword", "size": 10, "order": order},
                                   "aggs": sums}},
    }}}


def group_agg(group_by: GroupBy, group_order: "GroupOrder" = None, by_amount: bool = False) -> Optional[dict]:
    sum_agg = {"sum": {"sum": {"field": "amount.total"}}}
    # '어디를 제일 많이 갔지' 는 횟수, '어디에 제일 많이 썼지' 는 금액
    order = [{"_count": "desc"}, {"sum": "desc"}] if group_order == GroupOrder.COUNT else {"sum": "desc"}
    terms_field = {
        GroupBy.MERCHANT: "merchant.normalizedName", GroupBy.CATEGORY: "merchant.category",
        GroupBy.METHOD: "payment.method", GroupBy.PROVIDER: "payment.provider", GroupBy.WEEKDAY: "dayOfWeek",
    }.get(group_by)
    if terms_field:
        missing = {GroupBy.PROVIDER: "NONE", GroupBy.CATEGORY: "기타"}.get(group_by)
        terms = {"field": terms_field, "size": 10, "order": order}
        if missing:
            terms["missing"] = missing
        return {"terms": terms, "aggs": sum_agg}
    if group_by in (GroupBy.DAY, GroupBy.MONTH):
        interval = "day" if group_by == GroupBy.DAY else "month"
        fmt = "yyyy-MM-dd" if group_by == GroupBy.DAY else "yyyy-MM"
        hist = {"field": "transactedDate", "calendar_interval": interval, "format": fmt, "min_doc_count": 1}
        if by_amount:  # '제일 많이 쓴 날' → 금액 순, 아니면 날짜 순
            hist["order"] = {"sum": "desc"}
        return {"date_histogram": hist, "aggs": sum_agg}
    return None


_WEEKDAY_KO = "월화수목금토일"


def _matched_items(hit: Dict[str, Any]) -> List[str]:
    inner = ((hit.get("inner_hits") or {}).get("matched_items") or {}).get("hits", {}).get("hits", [])
    out = []
    for h in inner:
        src = h.get("_source") or {}
        name = src.get("name") or (src.get("items") or {}).get("name")
        qty = src.get("quantity") or (src.get("items") or {}).get("quantity")
        amount = src.get("amount") or (src.get("items") or {}).get("amount")
        if name:
            # 품목 가격을 함께 ('새우깡 얼마였지' 에 영수증 전체 금액이 아니라 품목 금액으로 답하도록)
            label = f"{name} x{qty}" if qty and qty != 1 else name
            out.append(f"{label} {int(amount):,}원" if amount else label)  # 0원 = 가격 없음 → 금액 생략
    return out


def _parse_result(res: Dict[str, Any], group_by: GroupBy) -> TransactionQueryResult:
    hits = res["hits"]
    rows = []
    for h in hits["hits"]:
        row = to_row(h["_source"])
        row.matchedItems = _matched_items(h)
        rows.append(row)
    result = TransactionQueryResult(total=hits["total"]["value"], transactions=rows)
    aggs = res.get("aggregations") or {}
    if "items" in aggs:
        m = aggs["items"]["m"]
        result.itemAggregates = {"sum": m["sum"]["value"] or 0, "quantity": m["qty"]["value"] or 0,
                                 "lines": m["doc_count"]}
        if group_by == GroupBy.ITEM:
            result.groups = [{"key": b["key"], "count": int(b["qty"]["value"] or b["doc_count"]),
                              "lines": b["doc_count"], "sum": b["sum"]["value"]} for b in m["names"]["buckets"]]
    if "sum" in aggs:
        result.aggregates = {
            "sum": aggs["sum"]["value"] or 0, "count": aggs["count"]["value"],
            "avg": round(aggs["avg"]["value"]) if aggs["avg"]["value"] is not None else None,
            "max": aggs["max"]["value"],
        }
        if "groups" in aggs:
            groups = []
            for b in aggs["groups"]["buckets"]:
                key = b.get("key_as_string") or b["key"]
                if group_by == GroupBy.WEEKDAY and isinstance(key, int) and 1 <= key <= 7:
                    key = f"{_WEEKDAY_KO[key - 1]}요일"
                groups.append({"key": key, "count": b["doc_count"], "sum": b["sum"]["value"]})
            result.groups = groups
    return result


# ============================================================================
# 저장소
# ============================================================================
class TransactionStore:
    def __init__(self, embedder: Optional[Any] = None, reranker: Optional[Any] = None):
        self._client: Optional[OpenSearch] = None
        self._index_ready = False
        self._lock = threading.Lock()
        self._embedder = embedder
        self._reranker = reranker

    # ------------------------------------------------------------------ 연결/색인
    @property
    def alias(self) -> str:
        return settings.transactions.index_name

    @property
    def physical_index(self) -> str:
        return f"{self.alias}-v{INDEX_VERSION}"

    @property
    def embedder(self):
        if self._embedder is None and settings.transactions.embed_on_save:
            from app.services.opensearch.embedding_service import embedding_service  # 모델 로딩은 서버 기동 시
            self._embedder = embedding_service
        return self._embedder

    @property
    def reranker(self):
        """의미 검색 후보 재채점용 cross-encoder (없거나 비활성이면 의미 검색 단계를 건너뜀)"""
        if self._reranker is None:
            from app.services.rerank import reranker  # 모델 로딩은 서버 기동 시
            self._reranker = reranker
        return self._reranker if getattr(self._reranker, "is_available", False) else None

    def _get_client(self) -> OpenSearch:
        if self._client is None:
            cfg = settings.opensearch
            # 공용 클라이언트(timeout 3000)와 달리 짧은 timeout: 저장소가 죽었으면 빨리 503 을 돌려준다
            self._client = OpenSearch(
                hosts=[{"host": cfg.host, "port": cfg.port}],
                use_ssl=False, verify_certs=False, timeout=30, max_retries=1, retry_on_timeout=False,
                # 기본 풀 1개 → 동시 요청(거래 목록·요약·챗봇) 때마다 'Connection pool is full' 경고와 재연결
                pool_maxsize=10,
            )
        return self._client

    def _embed(self, text: str, query: bool = False) -> Optional[List[float]]:
        emb = self.embedder
        if emb is None or not text:
            return None
        try:
            return emb.encode_query(text) if query else emb.encode_passage(text)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"임베딩 실패 → 벡터 없이 진행: {e}")
            return None

    def prepare(self, user_id: str, tx: Dict[str, Any], now: str) -> Tuple[str, Dict[str, Any]]:
        """원본 거래 → (문서 ID, 저장할 문서). 저장과 재보강·이관이 같은 경로를 쓴다"""
        doc = enrich(strip_derived(tx))
        doc["userId"] = user_id  # 요청 본문의 userId 가 아니라 API 파라미터로 강제
        key = dedup_key(user_id, doc)
        doc["dedupKey"] = key
        doc["createdAt"] = tx.get("createdAt") or now
        vector = self._embed(embedding_text(doc))
        if vector is not None:
            doc["embedding"] = vector
        return key, doc

    def ensure_index(self):
        if self._index_ready:
            return
        with self._lock:
            if self._index_ready:
                return
            client = self._get_client()
            try:
                self._ensure_index(client)
            except Exception as e:  # noqa: BLE001
                cfg = settings.opensearch
                if e.__class__.__name__ in ("ConnectionError", "ConnectionTimeout"):
                    raise StoreUnavailableError(f"OpenSearch({cfg.host}:{cfg.port}) 연결 실패: {e}") from e
                raise
            self._index_ready = True

    def _ensure_index(self, client: OpenSearch):
        target = self.physical_index
        if not client.indices.exists(index=target):
            client.indices.create(index=target, body=index_body(settings.embedding.dimension))
            logger.info(f"거래 인덱스 생성: {target}")
        current = list(client.indices.get_alias(name=self.alias).keys()) \
            if client.indices.exists_alias(name=self.alias) else []
        if target in current:
            return
        sources = current or ([settings.transactions.legacy_index]
                              if client.indices.exists(index=settings.transactions.legacy_index) else [])
        for src in sources:
            n = self._copy(client, src, target)
            logger.info(f"거래 이관: {src} → {target} ({n}건, 파생 필드·임베딩 재생성)")
        actions = [{"remove": {"index": i, "alias": self.alias}} for i in current]
        actions.append({"add": {"index": target, "alias": self.alias}})
        client.indices.update_aliases(body={"actions": actions})
        logger.info(f"별칭 전환: {self.alias} → {target} (이전 인덱스 {sources} 는 보존)")

    def _copy(self, client: OpenSearch, src: str, dst: str) -> int:
        now = datetime.now(timezone.utc).isoformat()
        actions = []
        for hit in helpers.scan(client, index=src, query={"query": {"match_all": {}}}):
            source = hit["_source"]
            _, doc = self.prepare(source["userId"], source, now)
            actions.append({"_index": dst, "_id": hit["_id"], "_source": doc})
        if actions:
            helpers.bulk(client, actions, refresh=True)
        return len(actions)

    def reenrich(self) -> int:
        """사전·보강 규칙을 바꾼 뒤 기존 거래 전체에 다시 적용 (같은 ID 로 덮어씀)"""
        def run(client):
            return self._copy(client, self.physical_index, self.physical_index)
        return self._call(run)

    def _call(self, fn: Callable[[OpenSearch], Any]):
        try:
            self.ensure_index()
            return fn(self._get_client())
        except (StoreUnavailableError, InvalidTransactionError):
            raise
        except Exception as e:  # noqa: BLE001
            if e.__class__.__name__ in ("ConnectionError", "ConnectionTimeout"):
                raise StoreUnavailableError(f"OpenSearch 연결 실패: {e}") from e
            raise

    # ------------------------------------------------------------------ 저장/조회/삭제
    def save(self, user_id: str, txs: List[Dict[str, Any]]) -> Tuple[List[str], List[str]]:
        saved, duplicates = [], []
        now = datetime.now(timezone.utc).isoformat()
        started = time.perf_counter()

        def run(client):
            for tx in txs:
                key, doc = self.prepare(user_id, {k: v for k, v in tx.items() if k != "createdAt"}, now)
                tx_id = doc.get("transactionId")
                m = doc.get("merchant") or {}
                desc = (f"tx={tx_id} merchant={m.get('name')!r} category={m.get('category')} "
                        f"date={doc.get('transactedDate')} amount={(doc.get('amount') or {}).get('total')} "
                        f"items={len(doc.get('items') or [])} embedding={'embedding' in doc}")
                try:
                    client.index(index=self.alias, id=key, body=doc, op_type="create")
                    saved.append(tx_id)
                    logger.info(f"거래 저장 user={user_id} {desc}")
                except Exception as e:  # noqa: BLE001
                    status = getattr(e, "status_code", None)
                    if status == 409:
                        duplicates.append(tx_id)
                        logger.info(f"중복 거래 건너뜀 user={user_id} {desc} dedupKey={key[:12]}")
                    elif status == 400:
                        logger.error(f"거래 저장 거부(매핑 불일치) user={user_id} {desc} error={e}")
                        raise InvalidTransactionError(f"거래 {tx_id} 저장 거부 (매핑 불일치): {e}") from e
                    else:
                        raise
            client.indices.refresh(index=self.alias)

        self._call(run)
        logger.info(f"저장 완료 user={user_id} 요청={len(txs)} saved={len(saved)} duplicates={len(duplicates)} "
                    f"ms={int((time.perf_counter() - started) * 1000)}")
        return saved, duplicates

    def recent(self, user_id: str, limit: int = 20) -> List[TransactionRow]:
        body = {"size": limit, "query": {"term": {"userId": user_id}}, "sort": _DATE_SORT,
                "_source": {"excludes": ["embedding"]}}
        res = self._call(lambda c: c.search(index=self.alias, body=body))
        return [to_row(h["_source"]) for h in res["hits"]["hits"]]

    def count(self, user_id: str) -> int:
        res = self._call(lambda c: c.count(index=self.alias, body={"query": {"term": {"userId": user_id}}}))
        return int(res["count"])

    def delete(self, user_id: str, transaction_id: str) -> int:
        body = {"query": {"bool": {"filter": [
            {"term": {"userId": user_id}}, {"term": {"transactionId": transaction_id}},
        ]}}}
        res = self._call(lambda c: c.delete_by_query(index=self.alias, body=body, refresh=True))
        deleted = int(res.get("deleted", 0))
        (logger.info if deleted else logger.warning)(f"거래 삭제 user={user_id} tx={transaction_id} deleted={deleted}")
        return deleted

    def vocabulary(self, user_id: str, size: int = 50) -> "UserVocabulary":
        """이 사용자의 실제 가맹점·업종·간편결제 목록 (질문 해석 LLM 이 검색어를 실제 값에 맞추도록)"""
        body = {"size": 0, "query": {"term": {"userId": user_id}}, "aggs": {
            "merchants": {"terms": {"field": "merchant.normalizedName", "size": size}},
            "categories": {"terms": {"field": "merchant.category", "size": 20}},
            "providers": {"terms": {"field": "payment.provider", "size": 10}},
            "first": {"min": {"field": "transactedDate", "format": "yyyy-MM-dd"}},
            "last": {"max": {"field": "transactedDate", "format": "yyyy-MM-dd"}},
        }, "track_total_hits": True}
        res = self._call(lambda c: c.search(index=self.alias, body=body))
        aggs = res["aggregations"]
        keys = lambda name: [b["key"] for b in aggs[name]["buckets"]]  # noqa: E731
        return UserVocabulary(
            total=res["hits"]["total"]["value"], merchants=keys("merchants"), categories=keys("categories"),
            providers=keys("providers"),
            firstDate=aggs["first"].get("value_as_string"), lastDate=aggs["last"].get("value_as_string"),
        )

    # ------------------------------------------------------------------ 질의
    def _semantic_ids(self, q: TransactionQuery) -> List[str]:
        """
        의미 검색: 벡터 근접 후보 → cross-encoder 재채점 → 기준 점수 이상만
        (e5 임베딩 점수는 관련/무관 차이가 0.01 수준이라 단독으로는 거를 수 없음. 리랭커는 무관한 거래에
         0.001 수준을 줘서 구분이 됨. 실측: 커피→스타벅스 0.06, 편의점→GS25 0.45, 택시→무관 0.001)
        """
        text = semantic_text(q)
        rr = self.reranker
        vector = self._embed(text, query=True) if text and rr else None
        if vector is None:
            return []
        hits = self._call(lambda c: c.search(index=self.alias, body=build_candidate_body(q, vector)))["hits"]["hits"]
        if q.itemTerms and not (q.categories or q.merchantTerms or q.keywords):
            # 품목만 묻는 질문: 품목 정보가 없는 거래(결제앱 내역)는 그 물건을 샀는지 알 수 없으므로 제외
            # (실측: '과자' → 품목 없는 쿠팡 거래가 채택되고 답변이 '쿠팡에서 과자 구매' 로 단정)
            hits = [h for h in hits if h["_source"].get("items")]
        if not hits:
            return []
        query = rerank_query(q)
        best = rr.score(query, [rerank_text(h["_source"]) for h in hits])
        top = max(best)
        min_score = settings.transactions.semantic_min_score
        # 절대 기준 + 1등 대비 상대 기준 (꼬리의 애매한 후보 제거)
        keep = [h["_id"] for h, s in zip(hits, best) if s >= min_score and s >= top * 0.3]
        logger.info(f"의미 검색 query={query!r} candidates={len(hits)} kept={len(keep)} top={top:.3f} "
                    f"min={min_score} scores={[round(x, 3) for x in sorted(best, reverse=True)[:5]]}")
        return keep

    def query(self, q: TransactionQuery) -> TransactionQueryResult:
        started = time.perf_counter()
        stages: List[str] = []

        def search(body):
            body["_source"] = {"excludes": ["embedding"]}
            return self._call(lambda c: c.search(index=self.alias, body=body))

        result = _parse_result(search(build_search_body(q)), q.groupBy)
        stages.append(f"exact={result.total}")
        if q.has_text():
            result.matchType = MatchType.EXACT
            if result.total == 0 and len(text_groups(q)) > 1:
                relaxed = _parse_result(search(build_search_body(q, relaxed=True)), q.groupBy)
                stages.append(f"relaxed={relaxed.total}")
                if relaxed.total:
                    relaxed.matchType = MatchType.RELAXED
                    result = relaxed
            if result.total == 0:
                ids = self._semantic_ids(q)
                stages.append(f"semantic={len(ids)}")
                if ids:
                    semantic = _parse_result(search(build_search_body(_without_text(q), ids=ids)), q.groupBy)
                    if semantic.total:
                        semantic.matchType = MatchType.SEMANTIC
                        result = semantic
        if result.total == 0:
            # '이 조건에 없음' 과 '아직 저장한 거래가 없음' 을 구분해서 안내하기 위함
            result.userTotal = self.count(q.userId)
        cond = {k: v for k, v in q.model_dump(mode="json", exclude={"userId", "mode", "limit"}).items()
                if v not in (None, [], "", "NONE", "DATE", "SUM")}
        logger.info(f"거래 질의 user={q.userId} mode={q.mode.value} total={result.total} match={result.matchType.value} "
                    f"stages=[{', '.join(stages)}] ms={int((time.perf_counter() - started) * 1000)} cond={cond}")
        return result


transaction_store = TransactionStore()
