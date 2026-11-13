"""
거래 저장소 테스트 (OpenSearch·임베딩 모델 없이: 보강 규칙, 쿼리 생성, 단계적 검색, 저장 흐름)

  docker run --rm -v "<rag_backend>:/app" -w /app --entrypoint sh rag-base-backend:latest \
      -c "pip install -q pytest && python -m pytest tests -q -p no:cacheprovider"
"""
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.transactions import enrich as E  # noqa: E402
from app.services.transactions import store as S  # noqa: E402
from app.services.transactions.schemas import (  # noqa: E402
    GroupBy, MatchType, QueryMode, SortBy, TransactionQuery,
)

TX = {
    "schemaVersion": 1, "transactionId": "t1", "userId": "spoofed",
    "merchant": {"name": "스타벅스 강남점"}, "transactedAt": "2026-11-12T14:23:00+09:00",
    "transactedDate": "2026-11-12", "status": "APPROVED",
    "amount": {"total": 5600, "currency": "KRW"},
    "payment": {"method": "PAY_MONEY", "provider": "KAKAOPAY", "approvalNumber": None},
    "items": [{"name": "아메리카노", "quantity": 1, "unitPrice": 5600, "amount": 5600}],
    "extra": {},
}


class FakeEmbedder:
    def __init__(self):
        self.calls = []

    def encode_passage(self, text):
        self.calls.append(("passage", text))
        return [0.1, 0.2]

    def encode_query(self, text):
        self.calls.append(("query", text))
        return [0.3, 0.4]


# ---------------------------------------------------------------------------- 저장 시 보강
def test_classify_merchant():
    assert E.classify_merchant("GS25 역삼점") == ("편의점", "GS25")
    assert E.classify_merchant("Q서서울농협하나로마트사직점") == ("마트", "하나로마트")
    assert E.classify_merchant("CU 강남역점")[0] == "편의점"
    assert E.classify_merchant("CUCKOO 서비스센터") == (None, None)       # 짧은 영문 브랜드는 단어 경계
    assert E.classify_merchant("이마트24 선릉점") == ("편의점", "이마트24")  # 긴 브랜드 우선
    assert E.classify_merchant("행복약국") == ("약국", None)                # 업종 단어
    assert E.classify_merchant("(주) 고메드갤") == (None, None)


def test_normalize_merchant():
    assert E.normalize_merchant("(주) 고메드갤") == "고메드갤"
    assert E.normalize_merchant("Q서서울농협하나로마트사직점") == "서서울농협하나로마트사직점"
    assert E.normalize_merchant("GS25 역삼점") == "GS25 역삼점"


def test_enrich_fields():
    doc = E.enrich(TX)
    assert doc["merchant"]["category"] == "카페" and doc["merchant"]["brand"] == "스타벅스"
    assert doc["dayOfWeek"] == 4 and doc["transactedHour"] == 14          # 2026-11-12 = 목요일
    for word in ("스타벅스", "카페", "아메리카노", "카카오페이", "페이머니"):
        assert word in doc["searchText"]
    assert "업종: 카페" in E.embedding_text(doc) and "요일: 목요일" in E.embedding_text(doc)
    assert "category" not in TX["merchant"]                                # 원본 불변


def test_to_category_aliases():
    assert E.to_category("편의점") == "편의점" and E.to_category("커피숍") == "카페"
    assert E.to_category("역삼") is None


# ---------------------------------------------------------------------------- 쿼리 생성
def test_filters():
    q = TransactionQuery(userId="user-a", mode=QueryMode.AGGREGATE, startDate=date(2026, 10, 1),
                         endDate=date(2026, 11, 1), provider="KAKAOPAY", minAmount=10000,
                         weekdays=[6, 7], hourFrom=21, hourTo=2)
    f = S.build_filters(q)
    assert {"term": {"userId": "user-a"}} in f
    assert {"term": {"payment.provider": "KAKAOPAY"}} in f
    assert {"term": {"status": "APPROVED"}} in f                           # 합계는 승인 건만
    assert {"range": {"transactedDate": {"gte": "2026-10-01", "lt": "2026-11-01"}}} in f
    assert {"range": {"amount.total": {"gte": 10000}}} in f
    assert {"terms": {"dayOfWeek": [6, 7]}} in f
    assert any("should" in c.get("bool", {}) for c in f)                   # 21시~2시: 자정 넘는 구간
    assert {"term": {"status": "APPROVED"}} not in S.build_filters(q.model_copy(update={"mode": QueryMode.LIST}))


def test_text_groups_are_anded_and_category_expanded():
    q = TransactionQuery(userId="u", mode=QueryMode.SEARCH, locationTerms=["역삼"], categories=["편의점"])
    body = S.build_search_body(q)
    must = body["query"]["bool"]["must"]
    assert len(must) == 2                                                   # 역삼 AND 편의점
    conv = str(must[1])
    assert "merchant.category" in conv and "GS25" in conv and "세븐일레븐" in conv
    # 업종 단어가 가맹점 칸에 와도 업종으로도 찾음
    assert "merchant.category" in str(S.text_groups(TransactionQuery(userId="u", mode=QueryMode.SEARCH,
                                                                     merchantTerms=["편의점"])))
    relaxed = S.build_search_body(q, relaxed=True)["query"]["bool"]
    assert relaxed["minimum_should_match"] == 1 and "must" not in relaxed


def test_search_body_by_mode():
    agg = S.build_search_body(TransactionQuery(userId="u", mode=QueryMode.AGGREGATE, groupBy=GroupBy.CATEGORY))
    assert set(agg["aggs"]) == {"sum", "count", "avg", "max", "groups"}
    assert agg["aggs"]["groups"]["terms"]["field"] == "merchant.category"
    merchant = S.build_search_body(TransactionQuery(userId="u", mode=QueryMode.AGGREGATE, groupBy=GroupBy.MERCHANT))
    assert merchant["aggs"]["groups"]["terms"]["field"] == "merchant.normalizedName"
    lst = S.build_search_body(TransactionQuery(userId="u", mode=QueryMode.LIST, sortBy=SortBy.AMOUNT, limit=1))
    assert lst["size"] == 1 and "amount.total" in lst["sort"][0]
    srch = S.build_search_body(TransactionQuery(userId="u", mode=QueryMode.SEARCH, itemTerms=["치즈케이크"]))
    assert srch["sort"][0] == "_score"


def test_semantic_candidates_keep_user_filter():
    q = TransactionQuery(userId="u9", mode=QueryMode.SEARCH, keywords=["커피"], itemTerms=["라떼"],
                         semanticQuery="커피 마신 곳")
    knn = S.build_candidate_body(q, [0.1, 0.2])["query"]["knn"]["embedding"]
    assert {"term": {"userId": "u9"}} in knn["filter"]["bool"]["filter"]
    assert S.rerank_query(q) == "라떼"     # 짧은 명사 우선 (업종 → 품목 → 가맹점 → 키워드)
    assert S.rerank_text(E.enrich(TX)) == "스타벅스 강남점 / 카페 / 아메리카노"
    ids_body = S.build_search_body(S._without_text(q), ids=["a", "b"])
    assert {"ids": {"values": ["a", "b"]}} in ids_body["query"]["bool"]["filter"]
    assert "must" not in ids_body["query"]["bool"]


def test_dedup_key():
    a = S.dedup_key("u", TX)
    assert a == S.dedup_key("u", {**TX, "transactionId": "t2"})       # 같은 결제 → 같은 키
    assert a != S.dedup_key("other", TX)                               # 사용자가 다르면 별개
    with_appr = {**TX, "payment": {**TX["payment"], "approvalNumber": "12345678"}}
    moved = {**with_appr, "transactedAt": "2026-11-12T14:25:00+09:00"}
    assert S.dedup_key("u", with_appr) == S.dedup_key("u", moved)     # 승인번호 있으면 시각 오차 무시


# ---------------------------------------------------------------------------- 저장 / 단계적 검색
class FakeClient:
    def __init__(self, results=None):
        self.docs = {}
        self.searches = []
        self._results = list(results or [])

        class Indices:
            def refresh(_, index):
                pass
        self.indices = Indices()

    def index(self, index, id, body, op_type):
        if id in self.docs:
            err = Exception("conflict")
            err.status_code = 409
            raise err
        self.docs[id] = body

    def search(self, index, body):
        self.searches.append(body)
        total = self._results.pop(0) if self._results else 0
        hits = [{"_id": f"id{i}", "_source": {**E.enrich(TX), "transactionId": f"h{i}"}} for i in range(total)]
        return {"hits": {"total": {"value": total}, "hits": hits}}

    def count(self, index, body):
        return {"count": 8}


class FakeReranker:
    is_available = True

    def __init__(self, scores):
        self.scores = scores   # 후보 순서대로의 점수
        self.queries = []

    def score(self, query, texts):
        self.queries.append(query)
        return self.scores[:len(texts)]


def _store(client, embedder=None, reranker=None):
    store = S.TransactionStore(embedder=embedder or FakeEmbedder(), reranker=reranker or FakeReranker([0.5]))
    store._client = client
    store._index_ready = True
    return store


def test_save_enriches_forces_user_and_skips_duplicates():
    emb = FakeEmbedder()
    store = _store(FakeClient(), emb)
    saved, dup = store.save("user01", [TX])
    assert saved == ["t1"] and dup == []
    doc = next(iter(store._client.docs.values()))
    assert doc["userId"] == "user01"                     # 본문의 userId 는 무시
    assert doc["merchant"]["category"] == "카페" and doc["embedding"] == [0.1, 0.2]
    assert doc["dedupKey"] and doc["createdAt"]
    assert emb.calls[0][0] == "passage" and "스타벅스" in emb.calls[0][1]
    saved, dup = store.save("user01", [{**TX, "transactionId": "t9"}])
    assert saved == [] and dup == ["t9"]


def test_query_cascade_exact_relaxed_semantic():
    q = TransactionQuery(userId="u", mode=QueryMode.SEARCH, locationTerms=["역삼"], categories=["편의점"],
                         semanticQuery="역삼 편의점")
    # 정확 1건 → EXACT, 추가 검색 없음
    client = FakeClient([1])
    assert _store(client).query(q).matchType == MatchType.EXACT and len(client.searches) == 1
    # 정확 0 → 완화 2건
    client = FakeClient([0, 2])
    r = _store(client).query(q)
    assert r.matchType == MatchType.RELAXED and r.total == 2
    # 정확 0 → 완화 0 → 의미: 벡터 후보 4건 → 리랭크 → 기준(0.02)·상대(1등×0.3=0.15) 통과 2건
    emb = FakeEmbedder()
    rr = FakeReranker([0.5, 0.2, 0.1, 0.01])
    client = FakeClient([0, 0, 4, 2])
    r = _store(client, emb, rr).query(q)
    assert r.matchType == MatchType.SEMANTIC and ("query", "역삼 편의점") in emb.calls
    assert "knn" in client.searches[2]["query"]
    # 0.1 은 상대 기준(0.15) 미달, 0.01 은 절대 기준(0.02) 미달 → 탈락
    assert {"ids": {"values": ["id0", "id1"]}} in client.searches[3]["query"]["bool"]["filter"]
    assert rr.queries == ["편의점"]
    # 리랭크 점수가 모두 낮으면(무관한 후보) 의미 검색 결과 없음
    r = _store(FakeClient([0, 0, 3]), reranker=FakeReranker([0.001, 0.001, 0.0])).query(q)
    assert r.total == 0 and r.userTotal == 8
    # 리랭커 없으면 의미 검색 건너뜀 (벡터 점수만으로는 거를 수 없음)
    off = FakeReranker([0.9])
    off.is_available = False
    client = FakeClient([0, 0])
    assert _store(client, reranker=off).query(q).total == 0 and len(client.searches) == 2
    # 검색어 없는 조회는 단계 없음
    r = _store(FakeClient([3])).query(TransactionQuery(userId="u", mode=QueryMode.LIST))
    assert r.matchType == MatchType.NONE


def test_strip_derived_roundtrip():
    doc = E.enrich(TX)
    doc.update(embedding=[1.0], dedupKey="k", createdAt="x")
    raw = S.strip_derived(doc)
    assert "embedding" not in raw and "category" not in raw["merchant"] and "dayOfWeek" not in raw
    assert raw["merchant"]["name"] == "스타벅스 강남점"


def test_semantic_item_only_query_skips_itemless_candidates():
    q = TransactionQuery(userId="u", mode=QueryMode.SEARCH, itemTerms=["과자"], semanticQuery="과자")
    client = FakeClient([0, 2])   # 정확 검색 0건 → 벡터 후보 2건
    orig = client.search

    def search(index, body):
        res = orig(index, body)
        if "knn" in body.get("query", {}):
            res["hits"]["hits"][0]["_source"]["items"] = []        # 1건은 품목 없는 결제앱 내역
            res["hits"]["hits"][1]["_source"]["items"] = [{"name": "새우깡"}]
        return res
    client.search = search
    rr = FakeReranker([0.9])
    _store(client, reranker=rr).query(q)
    assert {"ids": {"values": ["id1"]}} in client.searches[-1]["query"]["bool"]["filter"]
