from datetime import datetime

from fastapi import APIRouter

from app.core.config import settings
from app.services.ocr import ocr_engine

router = APIRouter()


@router.get("/")
async def health_check():
    return {
        "status": "ok" if ocr_engine.is_ready else "degraded",
        "ocrReady": ocr_engine.is_ready,
        "ocrError": ocr_engine.load_error,
        "ocrLang": settings.ocr.lang,
        "timestamp": datetime.now().isoformat(),
        "version": "1.0.0",
    }
