"""
Reranker 인터페이스 (Strategy)

1차 하이브리드 검색 후보를 [질문, 문서] 쌍 단위로 다시 채점해 재정렬한다.
점수 산출(score)만 구현체가 담당하고, 점수 부여·정렬·상위 N 절단(rerank)은 공통 로직이다.
"""
from abc import ABC, abstractmethod
from typing import List, Optional


class Reranker(ABC):
    """리랭커 인터페이스."""

    @abstractmethod
    def score(self, query: str, texts: List[str]) -> List[float]:
        """질문-문서 쌍의 관련도 점수(0~1, 높을수록 관련)를 입력 순서대로 반환."""
        ...

    @property
    @abstractmethod
    def is_available(self) -> bool:
        """리랭커 사용 가능 여부 (설정 비활성/모델 로딩 실패 시 False)."""
        ...

    @property
    @abstractmethod
    def model_name(self) -> Optional[str]:
        """사용 중인 모델명 (미사용 시 None)."""
        ...

    def warmup(self) -> None:
        """서버 기동 시 모델 사전 로딩 (기본 no-op)."""
        return None

    def rerank(self, query: str, items: List[dict], texts: List[str], top_n: int) -> List[dict]:
        """items 에 rerankScore 를 부여하고 점수 내림차순 정렬 후 상위 top_n 건 반환.

        items 와 texts 는 같은 순서/길이여야 한다(texts[i] 는 items[i] 의 리랭킹 입력 텍스트).
        """
        if not items:
            return []
        scores = self.score(query, texts)
        reranked = []
        for item, s in zip(items, scores):
            out = dict(item)
            out["rerankScore"] = round(float(s), 4)
            reranked.append(out)
        reranked.sort(key=lambda x: x["rerankScore"], reverse=True)
        return reranked[:top_n]


class NoopReranker(Reranker):
    """리랭킹 비활성(RERANK_ENABLED=false) 시 사용하는 빈 구현체."""

    def score(self, query: str, texts: List[str]) -> List[float]:
        raise RuntimeError("리랭커가 비활성화되어 있습니다 (RERANK_ENABLED=false).")

    @property
    def is_available(self) -> bool:
        return False

    @property
    def model_name(self) -> Optional[str]:
        return None
