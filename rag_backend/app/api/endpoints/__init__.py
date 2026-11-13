from fastapi import APIRouter

from . import health
from . import search
from . import transactions
from . import chat

api_router = APIRouter()
api_router.include_router(health.router, prefix="/health", tags=["Health"])
api_router.include_router(search.router, tags=["Search"])
api_router.include_router(transactions.router, prefix="/transactions", tags=["Transactions"])
api_router.include_router(chat.router, prefix="/chat", tags=["Chat"])
