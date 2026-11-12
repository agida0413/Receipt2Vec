"""
환경 설정 관리
모든 설정은 .env 파일(없으면 기본값)에서 로드됨
"""
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

APP_ROOT = Path(__file__).resolve().parents[2]  # ocr_backend 폴더
load_dotenv(APP_ROOT / ".env")


def get_env(key: str, default: str = "") -> str:
    return os.getenv(key, default)


def get_env_int(key: str, default: int = 0) -> int:
    return int(os.getenv(key, str(default)))


def get_env_float(key: str, default: float = 0.0) -> float:
    return float(os.getenv(key, str(default)))


def get_env_bool(key: str, default: bool = False) -> bool:
    return os.getenv(key, str(default)).strip().lower() in ("true", "1", "yes", "on")


def get_env_optional(key: str, default: str = "") -> Optional[str]:
    """미설정 시 default, 빈 값으로 명시하면 None"""
    value = os.getenv(key, default).strip()
    return value or None


@dataclass
class OcrConfig:
    """PaddleOCR 설정"""
    lang: str
    ocr_version: str
    device: str
    det_model: Optional[str]
    rec_model: Optional[str]
    use_doc_orientation: bool
    use_doc_unwarping: bool
    use_textline_orientation: bool
    det_limit_side_len: int
    det_limit_type: str
    min_score: float
    enable_mkldnn: bool
    cpu_threads: int


@dataclass
class LlmConfig:
    """
    LLM(Gemini) 설정
    mode: off      = LLM 사용 안 함
          fallback = 규칙 파서 결과에 문제가 있을 때만 사용 (기본)
          always   = 항상 사용하고 그 결과를 채택
    """
    mode: str
    api_key: str
    model_name: str
    timeout_sec: int
    thinking_budget: int    # 0 = thinking 끔 (추출 작업은 불필요, 지연/비용 절감)
    max_input_chars: int    # 프롬프트에 넣을 OCR 원문 최대 길이

    @property
    def always(self) -> bool:
        return self.mode == "always"

    @property
    def is_configured(self) -> bool:
        return self.mode != "off" and bool(self.api_key)


LLM_MODES = ("off", "fallback", "always")


def _llm_mode() -> str:
    mode = get_env("LLM_MODE", "").strip().lower()
    if mode:
        if mode not in LLM_MODES:
            raise ValueError(f"LLM_MODE 는 {LLM_MODES} 중 하나여야 합니다: {mode!r}")
        return mode
    # 예전 설정(LLM_FALLBACK_ENABLED / LLM_ALWAYS) 호환: ALWAYS 가 켜져 있으면 always 로 해석
    if get_env_bool("LLM_ALWAYS", False):
        return "always"
    return "fallback" if get_env_bool("LLM_FALLBACK_ENABLED", True) else "off"


class Settings:
    def __init__(self):
        self.port = get_env_int("PORT", 3002)

        self.ocr = OcrConfig(
            lang=get_env("OCR_LANG", "korean"),
            ocr_version=get_env("OCR_VERSION", "PP-OCRv5"),
            device=get_env("OCR_DEVICE", "cpu"),
            det_model=get_env_optional("OCR_DET_MODEL", "PP-OCRv5_mobile_det"),
            rec_model=get_env_optional("OCR_REC_MODEL", "korean_PP-OCRv5_mobile_rec"),
            use_doc_orientation=get_env_bool("OCR_USE_DOC_ORIENTATION", True),
            use_doc_unwarping=get_env_bool("OCR_USE_DOC_UNWARPING", False),
            use_textline_orientation=get_env_bool("OCR_USE_TEXTLINE_ORIENTATION", True),
            det_limit_side_len=get_env_int("OCR_DET_LIMIT_SIDE_LEN", 1280),
            det_limit_type=get_env("OCR_DET_LIMIT_TYPE", "max"),
            min_score=get_env_float("OCR_MIN_SCORE", 0.5),
            enable_mkldnn=get_env_bool("OCR_ENABLE_MKLDNN", False),
            cpu_threads=get_env_int("OCR_CPU_THREADS", 4),
        )

        self.max_upload_bytes = get_env_int("MAX_UPLOAD_MB", 15) * 1024 * 1024
        self.image_max_side = get_env_int("IMAGE_MAX_SIDE", 2560)

        # 거래 일시에 붙일 타임존 (영수증에는 타임존이 없으므로)
        self.timezone = get_env("TIMEZONE", "Asia/Seoul")
        # OCR 평균 신뢰도가 이 값 미만이면 사용자 확인 요청
        self.review_min_ocr_score = get_env_float("REVIEW_MIN_OCR_SCORE", 0.85)

        self.llm = LlmConfig(
            mode=_llm_mode(),
            api_key=get_env("GEMINI_API_KEY", ""),
            model_name=get_env("LLM_MODEL", "gemini-2.5-flash"),
            timeout_sec=get_env_int("LLM_TIMEOUT_SEC", 30),
            thinking_budget=get_env_int("LLM_THINKING_BUDGET", 0),
            max_input_chars=get_env_int("LLM_MAX_INPUT_CHARS", 6000),
        )


settings = Settings()
