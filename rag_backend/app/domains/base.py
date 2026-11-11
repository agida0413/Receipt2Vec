"""
도메인 인터페이스 (DomainSpec).

각 도메인은 이 ABC 를 구현한 클래스로 정의한다(예: PersonDomain). 필드 매핑·정규화·
임베딩 구성·인덱스 스키마·검색 쿼리·LLM 프롬프트를 도메인이 보유하고, 서비스 계층은
이 인터페이스를 통해서만 호출한다.

도메인 추가/교체:
  1) app/domains/<name>.py 에 DomainSpec 구현 클래스 작성
  2) app/domains/registry.py 의 DOMAIN_REGISTRY 에 등록
  3) .env 의 ACTIVE_DOMAIN 변경

문서/결과에서 공통으로 요구하는 키는 'id' 하나이며, 나머지 필드는 도메인이 정의한다.
"""
from abc import ABC, abstractmethod
from typing import List, Optional


class DomainSpec(ABC):
    """도메인별 동작(매핑/임베딩/인덱스/쿼리/리랭킹)을 제공하는 인터페이스."""

    # ========================================================================
    # 데이터 적재 파이프라인
    # ========================================================================
    @abstractmethod
    def to_document(self, row: dict) -> Optional[dict]:
        """원천 데이터(CSV row 등) → 내부 제너릭 문서. 색인 제외 대상이면 None."""
        ...

    @abstractmethod
    def compose_embedding_text(self, doc: dict) -> str:
        """내부 문서 → 임베딩(벡터) 입력 텍스트."""
        ...

    @property
    @abstractmethod
    def vector_field(self) -> str:
        """벡터를 저장/조회하는 knn_vector 필드명 (loader 가 벡터를 채울 위치)."""
        ...

    # ========================================================================
    # 인덱스 스키마 설계 (index_service 가 호출)
    # ========================================================================
    @abstractmethod
    def build_index_body(self, embedding_dimension: int) -> dict:
        """OpenSearch 인덱스 settings+mappings 전체(body)를 설계해 반환한다.

        분석기/필드 매핑/벡터 method 등 인덱스 스키마 전부를 도메인이 직접 설계한다.
        embedding_dimension 은 인프라 설정(config)에서 주입받는다.
        """
        ...

    # ========================================================================
    # 검색 쿼리 설계 (search_service 가 호출)
    # ========================================================================
    @abstractmethod
    def build_search_query(self, query_text: str, query_vector: List[float]) -> dict:
        """OpenSearch 검색 쿼리 body 전체를 설계해 반환한다.

        채널 구성/필드/가중치(boost)/size/k 등 검색 전략 전부를 도메인이 직접 설계한다.
        """
        ...

    @abstractmethod
    def map_search_hit(self, hit: dict) -> dict:
        """OpenSearch hit(_source + _score) → 결과 아이템 dict.

        결과 아이템은 반드시 'id' 키를 포함해야 하며(식별·LLM 매핑·중복 제거에 사용),
        그 외 표시/속성 필드는 도메인이 자유롭게 정의한다. 검색 점수는 'score' 키로 담는다.
        """
        ...

    # ========================================================================
    # Cross-encoder 리랭킹 (search_service 가 호출)
    # ========================================================================
    def compose_rerank_text(self, item: dict) -> str:
        """검색 결과 아이템 → cross-encoder 에 넣을 문서 텍스트.

        기본 구현은 임베딩 텍스트 구성을 재사용한다(결과 아이템이 문서와 같은 필드 키를
        쓰는 경우 그대로 동작). 리랭킹용 텍스트를 달리 구성하려면 도메인에서 오버라이드한다.
        """
        return self.compose_embedding_text(item)

    # ========================================================================
    # LLM 리랭킹 (llm_service 가 참조)
    # ========================================================================
    @abstractmethod
    def format_candidate(self, rank: int, item: dict, query: str) -> str:
        """후보 1건을 리랭킹 프롬프트에 넣을 텍스트 블록으로 변환한다.

        어떤 필드를 어떻게 보여줄지(+사전 계산한 매칭 힌트)는 도메인이 직접 설계한다.
        llm_service 는 이 메서드 결과를 순번대로 이어붙여 {candidates} 에 주입한다.
        """
        ...

    @property
    @abstractmethod
    def reranking_prompt(self) -> str:
        """리랭킹 프롬프트 템플릿({query}/{candidates} 플레이스홀더 포함)."""
        ...

    @property
    @abstractmethod
    def llm_response_id_key(self) -> str:
        """LLM 응답 JSON 의 식별자 키 (결과 아이템 'id' 와 매핑)."""
        ...
