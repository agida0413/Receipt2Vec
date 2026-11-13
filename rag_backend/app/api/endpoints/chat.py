"""
가계부 챗봇 API (/api/chat)

POST /stream   질문 → SSE 스트리밍 답변 (질문 전처리 → 거래 조회 → 답변)
"""
from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from app.services.chat.schemas import ChatRequest
from app.services.chat.service import chat_service

router = APIRouter()


@router.post("/stream")
async def chat_stream(request: ChatRequest):
    return StreamingResponse(
        chat_service.stream(request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # nginx 프록시 버퍼링 끔 (토큰이 모였다가 한꺼번에 오는 것 방지)
        },
    )
