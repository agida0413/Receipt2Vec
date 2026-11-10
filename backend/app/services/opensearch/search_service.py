"""
하이브리드 검색 + Cross-encoder 리랭킹 + RAG 서비스.

  1) 하이브리드 검색   : 후보를 넓게 수집 (recall)
  2) Cross-encoder    : [질문, 문서] 쌍 채점으로 상위 N 건 선별 (precision, use_rerank)
  3) LLM 증강          : confidence/reason 부여 (설명 가능성, use_llm)

검색 쿼리/결과 매핑/리랭킹 입력 텍스트는 current_domain 이 만들고, 이 서비스는 단계를 연결한다.
"""
import time
import requests

from app.core.config import settings
from app.domains import current_domain
from app.services.opensearch.client import opensearch_client
from app.services.opensearch.embedding_service import embedding_service
from app.services.opensearch.query_processor import preprocess_query_with_log
from app.services.llm import llm_service
from app.services.rerank import reranker


class SearchService:
    """하이브리드 검색 + RAG 서비스"""

    def __init__(self, client=None):
        self.client = client or opensearch_client
        self.config = settings

    # ========================================================================
    # 검색 파이프라인 생성 (BM25/벡터 정규화·결합 가중치)
    # ========================================================================
    def create_search_pipeline(self, pipeline_name: str, vector_weight: float, bm25_weight: float):
        config = settings.opensearch
        search_pipeline_name = pipeline_name or config.search_pipeline_name

        url = f"http://{config.host}:{config.port}/_search/pipeline/{search_pipeline_name}"
        headers = {"Content-Type": "application/json"}

        # [bm25_weight, vector_weight] 순서 (쿼리 내 채널 순서와 일치)
        target_weights = [bm25_weight, vector_weight]

        pipeline_body = {
            "description": "BM25 + 벡터 점수 정규화/결합 파이프라인",
            "phase_results_processors": [
                {
                    "normalization-processor": {
                        "normalization": {"technique": "min_max"},
                        "combination": {
                            "technique": "arithmetic_mean",
                            "parameters": {"weights": target_weights}
                        }
                    }
                }
            ]
        }

        response = requests.put(url, json=pipeline_body, headers=headers)
        if response.status_code in [200, 201]:
            print("검색 파이프라인 생성 성공")
        else:
            print(f"생성 실패: {response.text}")

    # ========================================================================
    # 1단계: 하이브리드 검색 (N-gram + Keyword + KNN Vector) → 후보 수집(recall)
    # ========================================================================
    def hybrid_search(self, query_text: str, index_name: str = None, search_pipeline_name: str = None) -> list:
        idx = index_name or self.config.opensearch.index_name
        search_pipeline_name = search_pipeline_name or self.config.opensearch.search_pipeline_name

        # 검색어 전처리 (OpenSearch 검색에만 적용, 외부엔 원본 노출)
        normalized_query = preprocess_query_with_log(query_text)
        if not normalized_query:
            print("[Search] 전처리 후 빈 검색어 → 0건 반환")
            return []
        query_text = normalized_query

        # 쿼리 벡터 생성
        t0 = time.time()
        query_vector = embedding_service.encode_query(query_text)
        print(f"[TIMING] 임베딩 생성: {time.time() - t0:.3f}초")

        # 검색 쿼리 설계는 활성 도메인이 담당
        search_query = current_domain.build_search_query(query_text, query_vector)

        try:
            t1 = time.time()
            response = self.client.search(
                index=idx,
                body=search_query,
                params={"search_pipeline": search_pipeline_name}
            )
            print(f"[TIMING] OpenSearch 검색: {time.time() - t1:.3f}초")

            # hit → 결과 아이템 매핑도 도메인이 담당
            return [current_domain.map_search_hit(hit) for hit in response['hits']['hits']]

        except Exception as e:
            print(f"[Search Error] {e}")
            return []

    # ========================================================================
    # 2단계: Cross-encoder 리랭킹 → 상위 N 건 선별(precision)
    # ========================================================================
    def _rerank(self, original_query: str, items: list, use_rerank: bool) -> tuple:
        """(상위 N 건, 리랭킹 적용 여부) 반환. 미적용/실패 시 하이브리드 순서로 상위 N 건."""
        top_n = self.config.result_top_n
        if not items:
            return [], False

        if not use_rerank:
            return items[:top_n], False
        if not reranker.is_available:
            print("[Rerank] 리랭커 미가용 → 하이브리드 순서 유지")
            return items[:top_n], False

        try:
            t0 = time.time()
            # 리랭킹 입력 텍스트 구성은 도메인이 담당, 질문은 정규화 전 원본(자연어 그대로) 사용
            texts = [current_domain.compose_rerank_text(item) for item in items]
            reranked = reranker.rerank(original_query, items, texts, top_n)
            print(f"[TIMING] Cross-encoder 리랭킹({len(items)}건 → {len(reranked)}건): "
                  f"{time.time() - t0:.3f}초")
            return reranked, True
        except Exception as e:
            print(f"[Rerank Error] {e}, 하이브리드 순서로 대체")
            return items[:top_n], False

    # ========================================================================
    # 검색 (LLM 없음): 하이브리드 → (선택) Cross-encoder
    # ========================================================================
    def search(self, query_text: str, index_name: str = None,
               search_pipeline_name: str = None, use_rerank: bool = True) -> dict:
        original_query = query_text

        t_search = time.time()
        candidates = self.hybrid_search(original_query, index_name, search_pipeline_name)
        print(f"[TIMING] 하이브리드 검색 총합: {time.time() - t_search:.3f}초 ({len(candidates)}건)")

        results, reranked = self._rerank(original_query, candidates, use_rerank)
        return {
            "query": original_query,
            "results": results,
            "isReranked": reranked,
            "rerankModel": reranker.model_name if reranked else None,
        }

    # ========================================================================
    # RAG 검색: 하이브리드 → (선택) Cross-encoder → LLM 증강
    # ========================================================================
    def search_with_llm(self, query_text: str, index_name: str = None,
                        search_pipeline_name: str = None, use_rerank: bool = True) -> dict:
        # 원본 검색어는 LLM/응답에 그대로 노출 (정규화는 검색 내부에서만)
        result = self.search(query_text, index_name, search_pipeline_name, use_rerank)
        result["llmModel"] = llm_service.model_name
        result["isMock"] = not llm_service.is_available

        if not result["results"]:
            return result

        t_llm = time.time()
        enhanced_results = llm_service.rerank_candidates(result["query"], result["results"])
        print(f"[TIMING] LLM 증강: {time.time() - t_llm:.3f}초")

        # 신뢰도 기준 정렬
        enhanced_results.sort(key=lambda x: x["confidence"], reverse=True)
        result["results"] = enhanced_results
        return result


# 싱글톤 인스턴스
search_service = SearchService()
