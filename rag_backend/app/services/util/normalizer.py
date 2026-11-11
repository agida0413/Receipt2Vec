"""
텍스트 정규화 전략 (Strategy)

Java 인터페이스처럼 `Normalizer` ABC 를 두고, 도메인별 구현체를 갈아끼운다.
규칙(제외 키워드/괄호 제거 여부/최소 길이)은 생성자로 주입받아 도메인에 종속되지 않는다.
구현체 조합·주입은 도메인 구현 클래스(app/domains/*)에서 수행한다.
"""
import re
from abc import ABC, abstractmethod
from typing import Iterable, Optional


class Normalizer(ABC):
    """이름/텍스트 정규화 인터페이스. 색인 대상에서 제외할 경우 None 반환."""

    @abstractmethod
    def normalize(self, name: str) -> Optional[str]:
        ...


class TextNormalizer(Normalizer):
    """규칙 기반 텍스트 정규화 구현체 (도메인 독립, 설정 주입형).

    - 제외 키워드 포함 시 제외(None)
    - 모든 괄호 및 내용물 삭제(옵션)
    - 한글/영문/숫자/공백 외 특수문자 제거
    - 연속 공백 정리
    - 최소 길이 미만 제외(None)
    """

    _BRACKET_RES = [
        re.compile(r"\([^)]*\)"),
        re.compile(r"\[[^\]]*\]"),
        re.compile(r"\{[^}]*\}"),
    ]
    _SPECIAL_RE = re.compile(r"[^a-zA-Z0-9가-힣\s]")
    _SPACE_RE = re.compile(r"\s+")

    def __init__(
        self,
        exclude_keywords: Iterable[str] = (),
        remove_bracket_content: bool = True,
        min_length: int = 2,
    ):
        self._exclude = tuple(exclude_keywords)
        self._remove_bracket = remove_bracket_content
        self._min_length = min_length

    def normalize(self, name: str) -> Optional[str]:
        if not name:
            return None

        name = name.strip()

        # 제외 키워드(원본 기준)
        if any(keyword in name for keyword in self._exclude):
            return None

        # 괄호와 내용물 제거
        if self._remove_bracket:
            for bracket_re in self._BRACKET_RES:
                name = bracket_re.sub(" ", name)

        # 특수문자 제거 → 공백
        name = self._SPECIAL_RE.sub(" ", name)
        # 연속 공백 정리
        name = self._SPACE_RE.sub(" ", name).strip()

        if len(name) < self._min_length:
            return None

        return name
