"""
가계부 챗봇 (영수증·결제내역 기반 RAG)

  service.py  : 질문 → 전처리 → 조회 → 답변 오케스트레이션, SSE 이벤트 생성
  planner.py  : 질문 전처리/증강 → QueryPlan (규칙 / Gemini + 사용자 어휘)
  period.py   : 상대 기간 → 실제 날짜 범위 (코드가 계산)
  answerer.py : 조회 결과 → 답변 (Gemini 스트리밍 / 템플릿)
  gemini.py   : Gemini 공용 클라이언트 (재시도, 할당량·결제 쿨다운, 스트리밍)
  schemas.py  : QueryPlan, ChatRequest, QueryResult
거래 저장/검색은 services/transactions 가 담당한다.
"""
