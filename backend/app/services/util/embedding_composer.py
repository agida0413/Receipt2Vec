"""
임베딩 텍스트 구성 전략 (Strategy) — "벡터 컬럼방식"

어떤 필드를 어떻게 결합해 임베딩(벡터) 입력 텍스트를 만들지는 도메인마다 다르다.
`EmbeddingComposer` ABC 로 인터페이스를 두고 구현체를 갈아끼운다.
(실제 인코딩은 services/opensearch/embedding_service.py 가 담당)
"""
from abc import ABC, abstractmethod
from typing import List, Dict


class EmbeddingComposer(ABC):
    """내부 문서(canonical doc) → 임베딩 입력 텍스트 변환 인터페이스."""

    @abstractmethod
    def compose(self, doc: dict) -> str:
        ...


class TemplateEmbeddingComposer(EmbeddingComposer):
    """템플릿 기반 구성기.

    template 예:
        [{"label": "이름", "field": "name"},
         {"label": "직업", "field": "job"},
         {"label": "학교", "field": "school"}]

    → "이름: <name> | 직업: <job> | 학교: <school>"
      (값이 비어있는(None/"") 필드는 자동 제외)
    """

    def __init__(self, template: List[Dict[str, str]]):
        self._template = template

    def compose(self, doc: dict) -> str:
        parts = []
        for item in self._template:
            value = doc.get(item["field"])
            if value:
                parts.append(f'{item["label"]}: {str(value).strip()}')
        return " | ".join(parts)
