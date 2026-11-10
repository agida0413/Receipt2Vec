from datetime import datetime

from fastapi import APIRouter

router = APIRouter()

@router.post("/")
async def health_check():
    return {
        "status": "ok",
        "timestamp": datetime.now().isoformat(),
        "version": "1.0.0"
    }


