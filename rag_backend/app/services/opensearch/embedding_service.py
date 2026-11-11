"""
임베딩 인코더 서비스 (도메인 독립)

SentenceTransformer 모델로 텍스트를 벡터로 인코딩한다.
"무엇을 임베딩할지"(텍스트 구성)는 도메인 전략(EmbeddingComposer)이 담당하고,
이 서비스는 "어떻게 인코딩할지"(모델/prefix)만 담당한다.
(cross-encoder 리랭킹은 services/rerank 가 담당)
"""
from sentence_transformers import SentenceTransformer
from app.core.config import settings


class EmbeddingService:
    """텍스트 임베딩 서비스"""

    def __init__(self):
        self._embedding_model = None
        self._config = settings.embedding

    # ==========================================================
    # 임베딩 모델 로딩 (Lazy)
    # ==========================================================
    @property
    def embedding_model(self) -> SentenceTransformer:
        if self._embedding_model is None:
            print(f"[Embedding] 모델 로딩: {self._config.model_name}")
            self._embedding_model = SentenceTransformer(self._config.model_name)
        return self._embedding_model

    # ==========================================================
    # 검색 쿼리 임베딩 (E5 query prefix)
    # ==========================================================
    def encode_query(self, text: str) -> list[float]:
        query_text = f"{self._config.query_prefix}{text}"
        return self.embedding_model.encode(query_text).tolist()

    # ==========================================================
    # 적재 문서 임베딩 (E5 passage prefix)
    # ==========================================================
    def encode_passage(self, text: str) -> list[float]:
        passage_text = f"{self._config.passage_prefix}{text}"
        return self.embedding_model.encode(passage_text).tolist()

    @property
    def dimension(self) -> int:
        return self._config.dimension


# 싱글톤 인스턴스
embedding_service = EmbeddingService()
