"""
환경 설정 관리
모든 설정은 .env 파일에서 로드됨
"""
import os
from pathlib import Path
from dataclasses import dataclass
from dotenv import load_dotenv

# .env 파일 로드
APP_ROOT = Path(__file__).resolve().parents[2]  # backend 폴더
load_dotenv(APP_ROOT / ".env")


def get_env(key: str, default: str = "") -> str:
    """환경변수 조회"""
    return os.getenv(key, default)


def get_env_int(key: str, default: int = 0) -> int:
    """환경변수 정수 조회"""
    return int(os.getenv(key, str(default)))


def get_env_bool(key: str, default: bool = False) -> bool:
    """환경변수 불리언 조회 (true/1/yes/on)"""
    return os.getenv(key, str(default)).strip().lower() in ("true", "1", "yes", "on")


def get_env_float(key: str, default: float = 0.0) -> float:
    """환경변수 실수 조회"""
    return float(os.getenv(key, str(default)))


@dataclass
class OpenSearchConfig:
    """OpenSearch 연결 설정"""
    host: str
    port: int
    timeout: int
    index_name: str
    search_pipeline_name: str


@dataclass
class EmbeddingConfig:
    """임베딩 모델 설정"""
    model_name: str
    dimension: int
    query_prefix: str
    passage_prefix: str


@dataclass
class RerankConfig:
    """Cross-encoder 리랭커 설정"""
    enabled: bool       # False 면 모델을 로딩하지 않음 (요청의 use_rerank 도 무시)
    model_name: str
    max_length: int     # [질문, 문서] 쌍의 최대 토큰 길이
    batch_size: int


@dataclass
class LLMConfig:
    """LLM 설정"""
    provider: str
    model_name: str
    api_key: str
    max_tokens: int
    temperature: float


class Settings:
    """애플리케이션 설정 (모든 값은 .env에서 로드)"""

    def __init__(self):
        self.app_root = APP_ROOT / "app"

        # 활성 도메인 (app/domains 레지스트리 키)
        self.current_domain = get_env("ACTIVE_DOMAIN", "person")

        # 데이터 파일 경로
        data_csv_path = get_env("DATA_CSV_PATH", "test/person_dummy.csv")
        self.data_csv = self.app_root / data_csv_path

        # OpenSearch 설정
        self.opensearch = OpenSearchConfig(
            host=get_env("OPENSEARCH_HOST", "localhost"),
            port=get_env_int("OPENSEARCH_PORT", 9200),
            timeout=get_env_int("OPENSEARCH_TIMEOUT", 3000),
            index_name=get_env("INDEX_NAME", "rag-embeddings-index"),
            search_pipeline_name=get_env("SEARCH_PIPELINE_NAME","hybrid-search-pipeline")
        )

        # 임베딩 설정
        self.embedding = EmbeddingConfig(
            model_name=get_env("EMBEDDING_MODEL", "intfloat/multilingual-e5-base"),
            dimension=get_env_int("EMBEDDING_DIMENSION", 768),
            query_prefix=get_env("EMBEDDING_QUERY_PREFIX", "query: "),
            passage_prefix=get_env("EMBEDDING_PASSAGE_PREFIX", "passage: "),
        )

        # 리랭커 설정
        self.rerank = RerankConfig(
            enabled=get_env_bool("RERANK_ENABLED", True),
            model_name=get_env("RERANK_MODEL", "BAAI/bge-reranker-v2-m3"),
            max_length=get_env_int("RERANK_MAX_LENGTH", 512),
            batch_size=get_env_int("RERANK_BATCH_SIZE", 16),
        )

        # 최종 결과 건수 (리랭킹 여부와 무관하게 상위 N건만 응답/LLM 전달)
        self.result_top_n = get_env_int("RESULT_TOP_N", 10)

        # LLM 설정
        self.llm = LLMConfig(
            provider=get_env("LLM_PROVIDER", "gemini"),
            model_name=get_env("LLM_MODEL", "gemini-2.5-flash-lite"),
            api_key=get_env("GEMINI_API_KEY", ""),
            max_tokens=get_env_int("LLM_MAX_TOKENS", 1024),
            temperature=get_env_float("LLM_TEMPERATURE", 0.3)
        )

    def print_config(self):
        """현재 설정값 출력 (디버깅용)"""
        print("=" * 60)
        print("현재 설정값 (.env)")
        print("=" * 60)
        print(f"[OpenSearch]")
        print(f"  host: {self.opensearch.host}")
        print(f"  port: {self.opensearch.port}")
        print(f"  index_name: {self.opensearch.index_name}")
        print(f"[Embedding]")
        print(f"  model: {self.embedding.model_name}")
        print(f"  dimension: {self.embedding.dimension}")
        print(f"[Rerank]")
        print(f"  enabled: {self.rerank.enabled}")
        print(f"  model: {self.rerank.model_name}")
        print(f"  result_top_n: {self.result_top_n}")
        print(f"[LLM]")
        print(f"  provider: {self.llm.provider}")
        print(f"  model: {self.llm.model_name}")
        print(f"[Data]")
        print(f"  csv_path: {self.data_csv}")
        print("=" * 60)


# 싱글톤 설정 인스턴스
settings = Settings()
