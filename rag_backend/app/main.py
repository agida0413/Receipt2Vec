import sys
from pathlib import Path

_backend_root = Path(__file__).resolve().parent.parent
if str(_backend_root) not in sys.path:
    sys.path.insert(0, str(_backend_root))

import logging
import os
from dotenv import load_dotenv

load_dotenv()

import uvicorn
from fastapi import FastAPI
from app.api.endpoints import api_router
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.services.opensearch import index_service, embedding_service
from app.services.llm import llm_service
from app.services.rerank import reranker

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# 서버 시작 시 모델 미리 로딩
logger.info("임베딩 모델 사전 로딩 시작...")
_ = embedding_service.embedding_model
logger.info("임베딩 모델 로딩 완료")

logger.info("리랭킹 모델 사전 로딩 시작...")
reranker.warmup()
logger.info(f"리랭킹 모델 로딩 완료 (사용가능: {reranker.is_available})")

logger.info("LLM(Gemini) 사전 초기화 시작...")
_ = llm_service.is_available
logger.info(f"LLM 초기화 완료 (사용가능: {llm_service.is_available})")

app = FastAPI(
    title="RAG Base API",
    description="도메인 플러그인 기반 하이브리드 RAG 검색 서버",
    version="1.0.0"
)

app.include_router(api_router, prefix="/api")

logging.getLogger("httpx").setLevel(logging.WARNING)


@app.on_event("startup")
async def prepare_transaction_index():
    """거래 인덱스 준비 (없으면 생성, 이전 버전이 있으면 파생 필드·임베딩을 다시 만들어 이관 후 별칭 전환)"""
    from fastapi.concurrency import run_in_threadpool
    from app.services.transactions.store import transaction_store
    try:
        await run_in_threadpool(transaction_store.ensure_index)
        logger.info(f"거래 인덱스 준비 완료: {transaction_store.alias} → {transaction_store.physical_index}")
        # 첫 추론은 torch 초기화로 수 초 걸림 → 기동 시 한 번 돌려 첫 질문 지연 제거
        await run_in_threadpool(lambda: (transaction_store._embed("준비", query=True),
                                         transaction_store.reranker and transaction_store.reranker.score("준비", ["준비"])))
    except Exception as e:  # noqa: BLE001  (OpenSearch 가 늦게 떠도 서버는 기동, 첫 요청 때 재시도)
        logger.warning(f"거래 인덱스 준비 실패 (첫 요청 시 재시도): {e}")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
async def root():
    return {"message": "RAG Base server is running"}

if __name__ == "__main__":
    port = int(os.getenv("PORT", 3001))
    logger.info(f"서버를 포트 {port}에서 시작합니다...")
    uvicorn.run(app, host="0.0.0.0", port=port)
