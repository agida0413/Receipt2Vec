"""
도메인 가변 로직 전략(Strategy) 인터페이스 + 공유 유틸.

여기 모듈들은 app 의 다른 패키지에 의존하지 않는 순수 모듈이다(순환 import 방지).
이 전략(ABC/구현체)들의 조합·주입은 도메인 구현 클래스(app/domains/*)에서 수행한다.
"""
from .text import norm
from .normalizer import Normalizer, TextNormalizer
from .embedding_composer import EmbeddingComposer, TemplateEmbeddingComposer

__all__ = [
    "norm",
    "Normalizer", "TextNormalizer",
    "EmbeddingComposer", "TemplateEmbeddingComposer",
]
