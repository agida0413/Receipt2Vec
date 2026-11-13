# ocr_backend

PaddleOCR(3.x, PP-OCRv5 한국어 모델) 로 **종이 영수증**과 **카카오페이 등 결제앱 결제내역 캡처**를 인식해
구조화된 JSON 으로 돌려주는 FastAPI 서버. (거래 저장·검색은 rag_backend 담당)

## 실행

```bash
# 프로젝트 루트에서
docker compose up -d --build ocr-backend
docker compose logs -f ocr-backend   # 최초 실행 시 모델 다운로드로 수 분 소요
```

- Swagger: http://localhost:3002/docs
- 설정을 바꾸려면 `.env.example` 을 `.env` 로 복사해서 수정 (없어도 기본값으로 동작)

## 처리 흐름

```
사진 → PaddleOCR → 규칙 파서 → Normalizer → 품질 평가
                      └(실패 시) Gemini 추출 → 원문 대조 검증 → Normalizer → 품질 평가
     → 더 나은 결과 채택 → Transaction[] (needsReview 표시)
     → [클라이언트] 사용자 확인/수정 → POST /confirm → 확정된 Transaction[] → RAG 백엔드 저장
```

- **LLM 은 규칙 파서가 실패했을 때만** 호출 (`extraction.llmTriggerReasons` 에 이유 표시)
  - 문서 유형 불확실, 가맹점/날짜/금액 누락, 총액 추정, 품목 합계 불일치
- LLM 이 준 **금액·승인번호·사업자번호·날짜는 OCR 원문에 있는 값만 채택** (없으면 버리고 `llmUnverifiedFields` 에 기록)
- 카드번호는 끝 4자리만 남기고 마스킹한 뒤 파싱/LLM 전송/응답
- 429/5xx 는 지수 백오프로 최대 2회 재시도, 그래도 실패하면 규칙 결과로 응답 (`llmError`)

## API

| Method | Path | 설명 |
|---|---|---|
| GET  | `/api/health/` | 모델 로딩 상태 |
| POST | `/api/ocr/recognize` | 이미지 업로드 → `transactions[]` (multipart) |
| POST | `/api/ocr/parse` | 텍스트만 넣어서 같은 파이프라인 실행 (파서/LLM 튜닝용) |
| POST | `/api/ocr/confirm` | 사용자가 수정한 `transactions[]` 재검증 → 확정 |

```bash
curl -F "file=@receipt.jpg" -F "user_id=user01" -F "use_llm=true"      http://localhost:3002/api/ocr/recognize
```

응답 주요 필드
- `transactions[]`: 공통 거래 형식 ([app/domain/transaction.py](app/domain/transaction.py)) — **저장 대상**
  - `quality.needsReview` / `reviewReasons`: 사용자 확인이 필요한지와 이유
- `needsReview`: 확인이 필요한 거래가 하나라도 있는지
- `extraction`: 규칙/LLM 중 무엇을 썼는지, LLM 호출 이유·오류
- `rawText`, `lines`: OCR 원문 / 박스
- `receipt`, `paymentApp`: 규칙 파서 원본 결과 (디버깅용)

`/confirm` 은 `{"transactions": [...]}` 를 받아 금액 양수화·카드번호 끝 4자리·날짜/일시 정합성을 다시 맞추고,
가맹점명·거래일·금액이 모두 있으면 `quality.confirmed=true` 로 돌려준다. 하나라도 비면 `valid=false` + `errors`.

## 설정 (.env)

`.env.example` 참고. LLM 관련:
- `GEMINI_API_KEY`, `LLM_MODEL` (기본 `gemini-2.5-flash`. 계정에서 쓸 수 없으면 `gemini-2.5-flash-lite` 등으로 변경)
- `LLM_MODE`: `off`(미사용) / `fallback`(규칙 파서 실패 시만, 기본) / `always`(항상 LLM 결과 사용)
  - 요청별: `use_llm=false` 로 끄기, `force_llm=true` 로 강제. LLM 실패·거래 0건이면 규칙 결과로 응답

## 거래 저장 (/api/ocr/transactions)

사용자가 확인·수정한 거래를 `confirm` 과 같은 규칙으로 재검증한 뒤 rag_backend(`RAG_BACKEND_URL`)의
`POST /api/transactions` 로 넘긴다. OpenSearch 인덱스·검색·챗봇은 모두 rag_backend 담당이며,
이 서버는 OpenSearch 에 직접 접속하지 않는다.

| Method | Path | 설명 |
|---|---|---|
| POST | `/api/ocr/transactions` | `{"userId","transactions":[...]}` → `{"saved","duplicates"}` (필수 항목 누락 시 422, rag_backend 미기동 시 503) |

## 구조

```
app/
  main.py                       # FastAPI, 시작 시 모델 warmup
  core/config.py                # .env 설정
  api/endpoints/ocr.py          # /api/ocr/*
  services/ocr/
    engine.py                   # PaddleOCR 래퍼 (싱글톤, 스레드 Lock)
    image_utils.py              # 디코딩, EXIF 회전, 리사이즈
    layout.py                   # 텍스트 박스 → 줄(row) 묶기
  domain/transaction.py         # 공통 Transaction 스키마 (RAG 백엔드와의 계약)
  services/pipeline.py          # 규칙 → LLM 폴백 → 검증 → 정규화 → 품질 평가
  services/normalizer.py        # 파서 출력 → Transaction (enum 매핑)
  services/quality.py           # needsReview / LLM 폴백 판단
  services/llm/                 # Gemini 공용 클라이언트(재시도·쿨다운), 추출기, 원문 대조 검증
  services/transaction_store.py # 확인된 거래 → rag_backend 저장 API 호출
  services/parser/
    __init__.py                 # 문서 유형 판별
    common.py                   # 금액/날짜/번호 추출
    receipt_parser.py           # 영수증
    payment_app_parser.py       # 결제앱 (상세/목록 화면)
tests/                          # 파서·파이프라인 테스트 (OCR/LLM 없이 실행, LLM 은 가짜 주입)
```

## 테스트

```bash
# 이미지 안에서 실행 (의존성 포함)
cd ocr_backend
docker run --rm -v "$(pwd):/app" -w /app --entrypoint sh receipt2vec-ocr-backend:latest   -c "pip install -q pytest && python -m pytest tests -q"
```
