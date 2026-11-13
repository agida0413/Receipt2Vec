# frontend — 지출 챗봇 프로토타입 (React + SSE)

## 실행

```bash
# 프로젝트 루트에서 (Node 설치 불필요, Docker 로 빌드)
docker compose up -d --build frontend     # opensearch, ocr-backend 도 함께 기동
# → http://localhost:3000
```

로컬 개발 서버 (Node 22+ 필요):
```bash
cd frontend
npm install
npm run dev        # http://localhost:5173, /api/ocr → localhost:3002(ocr), 그 외 /api → localhost:3001(rag)
```

## 화면
- **사용자 ID**: 사용자별 데이터/대화 분리 (프로토타입이라 인증 없이 ID 입력)
- **영수증 업로드**: 사진 → `/api/ocr/recognize` → 인식 결과 확인·수정 → `/api/ocr/transactions` 저장 (ocr_backend 가 검증 후 rag_backend 로 전달)
  - 확인이 필요한 항목은 배지로 표시 (AI 추출값, 총액 추정값, 품목 합계 불일치 …)
- **챗봇**: rag_backend `/api/chat/stream` SSE 를 `fetch` 스트림으로 읽어 토큰 단위로 표시
  - 답변 아래에 근거 데이터(합계·건수·평균, 가맹점별 막대, 조회된 거래) 표시
  - LLM 대신 규칙/템플릿이 쓰이면 ⓘ 안내 표시

## 구조
```
src/
  api.js                    # API 호출 + SSE 파서 (POST 라서 EventSource 대신 fetch 사용)
  App.jsx                   # 레이아웃, 사용자 ID, 내 거래 목록
  components/ChatPanel.jsx  # 대화, SSE 이벤트 처리 (status/plan/result/token/notice/error/done)
  components/ResultView.jsx # 근거 데이터 (집계/그룹/거래 표)
  components/UploadDialog.jsx # 업로드 → 확인/수정 → 저장
nginx.conf                  # 배포 시 /api/ocr → ocr-backend, /api → backend(rag) 프록시 (SSE 버퍼링 끔)
```
