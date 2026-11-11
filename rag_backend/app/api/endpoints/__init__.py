from fastapi import APIRouter

from . import health
from . import search

api_router = APIRouter()
api_router.include_router(health.router, prefix="/health", tags=["Health"])
api_router.include_router(search.router, tags=["Search"])
