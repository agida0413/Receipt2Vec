import sys
from pathlib import Path

_backend_root = Path(__file__).resolve().parent.parent
if str(_backend_root) not in sys.path:
    sys.path.insert(0, str(_backend_root))

import logging

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.endpoints import api_router
from app.core.config import settings
from app.services.ocr import ocr_engine

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler()],
)
logging.getLogger("httpx").setLevel(logging.WARNING)  # 모델 다운로드 요청 로그 억제
logger = logging.getLogger(__name__)

# 서버 시작 시 OCR 모델 미리 로딩 (최초 실행 시 모델 다운로드로 수 분 소요)
logger.info("PaddleOCR 모델 사전 로딩 시작...")
ocr_engine.warmup()
logger.info(f"PaddleOCR 로딩 완료 (사용가능: {ocr_engine.is_ready})")

app = FastAPI(
    title="Receipt2Vec OCR API",
    description="PaddleOCR 기반 영수증 / 결제앱 결제내역 인식 + 사용자별 거래 챗봇(SSE)",
    version="1.0.0",
)

app.include_router(api_router, prefix="/api")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
async def root():
    return {"message": "OCR server is running"}


if __name__ == "__main__":
    logger.info(f"서버를 포트 {settings.port}에서 시작합니다...")
    uvicorn.run(app, host="0.0.0.0", port=settings.port)
