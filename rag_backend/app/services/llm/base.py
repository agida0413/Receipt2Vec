"""
LLM Provider 인터페이스 (Strategy)

Java 인터페이스처럼 LLM 호출부를 추상화한다. Gemini/OpenAI 등 벤더별 구현체를
`LLMProvider` 를 구현해 추가하고, llm_service 가 config.llm.provider 로 선택한다.
"""
from abc import ABC, abstractmethod
from typing import Optional


class LLMProvider(ABC):
    """LLM 호출 인터페이스."""

    @abstractmethod
    def generate_json(self, prompt: str) -> str:
        """프롬프트로 생성 요청 → 응답 텍스트(JSON 문자열) 반환.

        결정성(temperature=0)·JSON 출력에 최적화된 호출을 권장한다.
        호출 불가(미초기화) 시 예외를 던지거나 호출 전에 is_available 로 확인한다.
        """
        ...

    @property
    @abstractmethod
    def is_available(self) -> bool:
        """모델 사용 가능 여부 (API 키/패키지 등)."""
        ...

    @property
    @abstractmethod
    def model_name(self) -> Optional[str]:
        """사용 중인 모델명 (미사용 시 None)."""
        ...
