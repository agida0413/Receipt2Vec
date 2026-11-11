"""
Cross-encoder 리랭커 구현체 (기본: BAAI/bge-reranker-v2-m3, 다국어)

bi-encoder(E5)는 질문과 문서를 따로 벡터화해 거리만 비교하지만, cross-encoder 는
[질문, 문서] 를 한 번에 입력받아 두 텍스트의 상호작용을 직접 보고 관련도를 채점한다.
정확도는 높지만 후보마다 모델을 한 번씩 돌려야 하므로 1차 검색 상위 후보에만 적용한다.
"""
import math
from typing import List, Optional

from app.core.config import settings
from app.services.rerank.base import Reranker


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


class CrossEncoderReranker(Reranker):
    """sentence-transformers CrossEncoder 기반 리랭커."""

    def __init__(self, config=None):
        self._config = config or settings.rerank
        self._model = None
        self._load_failed = False

    def _load(self):
        if self._model is not None or self._load_failed:
            return
        try:
            from sentence_transformers import CrossEncoder
            print(f"[Rerank] 모델 로딩: {self._config.model_name}")
            self._model = CrossEncoder(self._config.model_name, max_length=self._config.max_length)
        except Exception as e:
            print(f"[Rerank] 모델 로딩 실패: {e}")
            self._load_failed = True

    def warmup(self) -> None:
        self._load()

    @property
    def is_available(self) -> bool:
        self._load()
        return self._model is not None

    @property
    def model_name(self) -> Optional[str]:
        return self._config.model_name if self._model is not None else None

    def score(self, query: str, texts: List[str]) -> List[float]:
        self._load()
        if self._model is None:
            raise RuntimeError("Cross-encoder 모델이 로딩되지 않았습니다.")
        if not texts:
            return []

        pairs = [[query, t] for t in texts]
        raw = [float(s) for s in self._model.predict(pairs, batch_size=self._config.batch_size)]

        # 단일 라벨 모델은 기본적으로 sigmoid 가 적용되어 0~1 이 나온다.
        # 버전/설정에 따라 logit 이 그대로 나오는 경우를 대비해 범위를 벗어나면 직접 정규화.
        if any(s < 0.0 or s > 1.0 for s in raw):
            raw = [_sigmoid(s) for s in raw]
        return raw
