"""
리랭커 패키지.

RERANK_ENABLED 설정으로 구현체를 선택해 reranker 싱글톤으로 노출한다.
  - true  : CrossEncoderReranker (모델 로딩)
  - false : NoopReranker (모델 미로딩, 요청의 use_rerank 무시)
"""
from app.core.config import settings
from .base import Reranker, NoopReranker
from .cross_encoder import CrossEncoderReranker


def build_reranker(config=None) -> Reranker:
    cfg = config or settings.rerank
    if not cfg.enabled:
        print("[Rerank] RERANK_ENABLED=false → 리랭킹 비활성")
        return NoopReranker()
    return CrossEncoderReranker(cfg)


# 싱글톤 인스턴스
reranker: Reranker = build_reranker()

__all__ = [
    "Reranker", "NoopReranker", "CrossEncoderReranker",
    "build_reranker", "reranker",
]
