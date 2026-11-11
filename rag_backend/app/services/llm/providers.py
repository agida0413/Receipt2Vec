"""
LLM Provider 구현체

GeminiProvider: Google Generative AI(Gemini) 구현.
새 벤더 추가 시 LLMProvider 를 구현하고 build_provider() 팩토리에 등록.
"""
from typing import Optional

from app.core.config import settings
from app.services.llm.base import LLMProvider

# Gemini API (설치: pip install google-generativeai)
try:
    import google.generativeai as genai
    GEMINI_AVAILABLE = True
except ImportError:
    GEMINI_AVAILABLE = False
    print("[경고] google-generativeai 패키지 미설치. pip install google-generativeai")


class GeminiProvider(LLMProvider):
    """Gemini LLM 구현체."""

    def __init__(self, config=None):
        self._config = config or settings.llm
        self._model = None
        self._initialized = False

    def _initialize(self):
        if self._initialized:
            return
        if GEMINI_AVAILABLE and self._config.api_key:
            try:
                genai.configure(api_key=self._config.api_key)
                self._model = genai.GenerativeModel(self._config.model_name)
                print(f"[LLM] Gemini 초기화 완료: {self._config.model_name}")
            except Exception as e:
                print(f"[LLM] Gemini 초기화 실패: {e}")
                self._model = None
        self._initialized = True

    @property
    def is_available(self) -> bool:
        self._initialize()
        return self._model is not None

    @property
    def model_name(self) -> Optional[str]:
        return self._config.model_name if self._model else None

    def generate_json(self, prompt: str) -> str:
        self._initialize()
        if not self._model:
            raise RuntimeError("Gemini 모델이 초기화되지 않았습니다.")

        response = self._model.generate_content(
            prompt,
            generation_config={
                "temperature": 0.0,
                "top_p": 1.0,
                "response_mime_type": "application/json",
                # 응답 누락 방지 (Gemini 2.5 flash-lite 한도 내)
                "max_output_tokens": 32768,
            },
        )
        return response.text.strip()


# ============================================================================
# Provider 팩토리
# ============================================================================
def build_provider(config=None) -> LLMProvider:
    """config.llm.provider 값으로 구현체 선택."""
    cfg = config or settings.llm
    provider = (cfg.provider or "gemini").lower()

    if provider == "gemini":
        return GeminiProvider(cfg)

    # 미지원 provider → Gemini 로 폴백
    print(f"[LLM] 미지원 provider '{provider}', Gemini 로 폴백")
    return GeminiProvider(cfg)
