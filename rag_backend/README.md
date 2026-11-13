# RAG Base (도메인 추상화 RAG 검색 백엔드)

OpenSearch 기반 하이브리드(BM25 + 벡터) 검색에 Cross-encoder 리랭킹과 LLM 리랭킹을 단계적으로 결합한 RAG 검색 백엔드임.

```
하이브리드 검색 50건 (recall)  →  Cross-encoder 상위 N건 (precision)  →  LLM confidence/reason (설명)
                                    use_rerank 로 on/off                  use_llm 으로 on/off
```

핵심은 **도메인 추상화**임. 검색 엔진(적재/색인/검색/LLM)은 특정 도메인에 종속되지 않은 제너릭 로직으로 작성되어 있고, 도메인에 특화된 부분(필드 매핑, 정규화, 임베딩 구성, 인덱스 스키마, 검색 쿼리, 가중치, 리랭킹 프롬프트)은 `DomainSpec` 인터페이스 구현 클래스 하나로 모임.

- 도메인 교체는 구현 클래스 작성 + 레지스트리 등록 + `.env`의 `ACTIVE_DOMAIN` 변경만으로 끝남
- 엔진/LLM/API 코드는 도메인에 대해 알지 못함
- 현재 예시 도메인으로 `person`(인물), `animal`(동물)이 등록되어 있음

---

## 1. 프로젝트 구조

```
backend/
  requirements.txt
  README.md
  app/
    main.py                         # FastAPI 진입점 (모델/LLM 사전 로딩, 라우터 등록)
    models.py                       # API 요청/응답 모델 (Pydantic)
    api/
      endpoints/
        health.py                   # 헬스 체크
        search.py                   # 검색 / 인덱스 생성 / 적재 / 파이프라인
    core/
      config.py                     # .env 기반 설정 (인프라 연결값 + ACTIVE_DOMAIN)
    domains/                        # ★ 도메인 추상화 ★
      base.py                       # DomainSpec 인터페이스(ABC)
      registry.py                   # 도메인 이름 → 구현체 매핑 + resolve
      __init__.py                   # current_domain 선택(활성화)
      person.py                     # PersonDomain 구현체
      animal.py                     # AnimalDomain 구현체
    services/
      llm/
        base.py                     # LLMProvider 인터페이스
        providers.py                # GeminiProvider + build_provider 팩토리
        llm_service.py              # 검색 후보 LLM 리랭킹/증강 (도메인 독립)
      opensearch/
        client.py                   # OpenSearch 클라이언트
        embedding_service.py        # SentenceTransformer 인코더 (E5)
        index_service.py            # 인덱스 생성/삭제/벌크/통계
        data_loader_service.py      # CSV → 문서 변환 → 임베딩 → 적재
        query_processor.py          # 검색어 전처리(정규화)
        search_service.py           # 하이브리드 → 리랭킹 → LLM 단계 조율
      rerank/
        base.py                     # Reranker 인터페이스 + NoopReranker
        cross_encoder.py            # CrossEncoderReranker (bge-reranker-v2-m3)
        __init__.py                 # build_reranker 팩토리, reranker 싱글톤
      util/
        text.py                     # norm() 텍스트 정규화 헬퍼
        normalizer.py               # Normalizer ABC + TextNormalizer
        embedding_composer.py       # EmbeddingComposer ABC + TemplateEmbeddingComposer
    test/
      person_dummy.csv              # person 도메인 더미 데이터
      animal_dummy.csv              # animal 도메인 더미 데이터
```

### backend 역할

- FastAPI 기반 API 서버임
- CSV 원천 데이터를 도메인 규칙으로 문서화하여 OpenSearch에 적재함
- 형태소(Nori) + Edge-NGram + KNN 벡터 하이브리드 검색을 수행함
- Cross-encoder(`bge-reranker-v2-m3`)로 후보를 재채점해 상위 N건을 선별함 (`use_rerank`)
- Gemini provider를 통해 검색 결과를 LLM으로 리랭킹/증강함 (provider 미설정 시 Mock)
- 인덱스 생성/삭제/통계, 검색 파이프라인 생성을 관리함

---

## 2. 핵심 개념: 도메인 추상화

도메인의 모든 의미는 `DomainSpec`(ABC) 구현 클래스 하나가 보유함.
엔진이 공통으로 요구하는 구조적 키는 `id` 하나뿐이고, 나머지 필드와 설계는 도메인이 자유롭게 정함.

`DomainSpec` 인터페이스 (`app/domains/base.py`):

| 멤버 | 역할 |
| --- | --- |
| `to_document(row)` | 원천(CSV row) → 내부 문서 변환 (제외 대상이면 None) |
| `compose_embedding_text(doc)` | 문서 → 임베딩 입력 텍스트 구성 |
| `vector_field` | 벡터를 저장/조회할 knn_vector 필드명 |
| `build_index_body(dim)` | 인덱스 settings+mappings(분석기/필드/벡터) 전체 설계 |
| `build_search_query(query, vector)` | 하이브리드 검색 쿼리 body 전체 설계 (필드/가중치/size/k) |
| `map_search_hit(hit)` | OpenSearch hit → 결과 아이템 dict 매핑 |
| `compose_rerank_text(item)` | 결과 아이템 → cross-encoder 입력 텍스트 (기본: 임베딩 텍스트 재사용, 선택 오버라이드) |
| `format_candidate(rank, item, query)` | 후보 1건을 리랭킹 프롬프트 텍스트로 변환 |
| `reranking_prompt` | 리랭킹 프롬프트 템플릿 |
| `llm_response_id_key` | LLM 응답 JSON의 식별자 키 |

활성 도메인 선택(활성화) 흐름:

```
.env  ACTIVE_DOMAIN=person
  -> core/config.py        settings.current_domain
  -> domains/registry.py   DOMAIN_REGISTRY[name]() 로 인스턴스화
  -> domains/__init__.py   current_domain = load_domain(...)   ← 활성화 지점
  -> 엔진 전체             from app.domains import current_domain
```

---

## 3. 전체 처리 흐름

### 3-1. 데이터 적재

```
CSV 원천 데이터
  -> data_loader_service.read_csv
  -> current_domain.to_document(row)              # 도메인 규칙으로 문서화
  -> current_domain.compose_embedding_text(doc)   # 임베딩 입력 텍스트
  -> embedding_service.encode_passage(text)      # E5 passage 인코딩 → 벡터
  -> index_service.bulk_index                    # OpenSearch 적재
```

인덱스는 사전에 생성되어 있어야 함(아래 인덱스 관리 참고).

### 3-2. 검색 (use_rerank=true, use_llm=true — 기본)

```
사용자 질문
  -> POST /api/search/
  -> search_service.search_with_llm
     [1단계: 하이브리드 검색 — 후보 수집]
  -> query_processor.preprocess_query             # 검색어 정규화
  -> embedding_service.encode_query               # E5 query 인코딩
  -> current_domain.build_search_query            # 하이브리드 쿼리 설계 (후보 50건)
  -> OpenSearch 검색 (search_pipeline 결합)
  -> current_domain.map_search_hit                # hit → 결과 아이템
     [2단계: Cross-encoder 리랭킹 — 상위 N건 선별]
  -> current_domain.compose_rerank_text           # 후보 → 리랭킹 입력 텍스트
  -> reranker.rerank(원본 질문, ...)               # [질문, 문서] 쌍 채점 → rerankScore, 상위 RESULT_TOP_N건
     [3단계: LLM 증강]
  -> llm_service.rerank_candidates                # 도메인 프롬프트로 LLM 리랭킹 (rerank 점수 참고)
  -> confidence 기준 정렬 → 응답
```

- LLM provider가 없으면 Mock으로 폴백함. 이때 `rerankScore`가 있으면 `confidence = rerankScore × 100`
- 리랭커 미가용/오류 시 하이브리드 순서 상위 N건으로 폴백함 (`isReranked=false`)

### 3-3. 검색 옵션 조합

| `use_rerank` | `use_llm` | 동작 |
| --- | --- | --- |
| true | true | 하이브리드 → Cross-encoder → LLM (기본) |
| true | false | 하이브리드 → Cross-encoder, `rerankScore` 순 상위 N건 |
| false | true | 하이브리드 순서 상위 N건 → LLM |
| false | false | 하이브리드 순서 상위 N건 그대로 |

최종 건수 N은 리랭킹 여부와 무관하게 `RESULT_TOP_N`으로 동일하게 맞춰서 on/off 비교가 공정하도록 함.

### 왜 Cross-encoder 리랭킹을 넣었나

- **bi-encoder(E5)의 한계**: 질문·문서를 따로 벡터화해 거리만 비교하므로 빠르지만 정밀도가 낮음. BM25·벡터 점수 결합(min-max)도 경험적 가중치임
- **cross-encoder**: `[질문, 문서]`를 함께 입력받아 상호작용을 직접 보고 채점 → 정확하지만 후보마다 추론이 필요해 1차 상위 후보에만 적용
- **LLM 앞단 필터**: LLM에는 정제된 N건만 전달 → 토큰 비용·지연·응답 누락 위험 감소
- **LLM 없는 환경**: Mock 대신 실제 모델 점수로 순위/신뢰도 제공

### 3-4. 인덱스 관리

```
POST /api/search/index   (recreate 옵션)
  -> index_service.build_index
  -> current_domain.build_index_body(dim)         # 스키마는 도메인이 설계
```

---

## 4. 주요 API

모든 라우터는 `/api` prefix 하위에 등록됨.

### 검색 API

| Method | Endpoint | 설명 |
| --- | --- | --- |
| POST | `/api/search/` | 하이브리드 검색 + (use_rerank) Cross-encoder 리랭킹 + (use_llm) LLM 증강 결과 반환 |

요청 본문(`SearchRequest`): `query`, `use_rerank`(기본 true), `use_llm`(기본 true), `index_name?`, `search_pipeline_name?`

```json
{"query": "대용량 트래픽 다뤄본 백엔드 개발자", "use_rerank": true, "use_llm": false}
```

### 인덱스 / 적재 API

| Method | Endpoint | 설명 |
| --- | --- | --- |
| POST | `/api/search/index` | 인덱스 생성 (`recreate=true`면 삭제 후 재생성) |
| POST | `/api/search/load` | 데이터 적재 (인덱스가 없으면 400, 먼저 생성 필요) |
| GET | `/api/search/index/stats` | 인덱스 통계(문서 수/용량) 조회 |
| DELETE | `/api/search/index` | 인덱스 삭제 |

### 검색 파이프라인 API

| Method | Endpoint | 설명 |
| --- | --- | --- |
| POST | `/api/search/search_pipeline` | 하이브리드 점수 정규화/결합 파이프라인 생성 (`bm25_weight`, `vector_weight`) |

### 헬스 체크

| Method | Endpoint | 설명 |
| --- | --- | --- |
| POST | `/api/health/` | 상태/타임스탬프/버전 반환 |

### 거래(영수증·결제내역) 저장소 API

ocr_backend 가 추출·사용자 확인을 마친 거래(Transaction, schemaVersion 1)를 저장하고, 챗봇이 구조화된 조건으로 조회한다.
(`services/transactions/`, 도메인 검색 인덱스와 별개, 매핑 `dynamic: strict`)

| Method | Endpoint | 설명 |
| --- | --- | --- |
| POST | `/api/transactions` | `{userId, transactions[]}` 저장 (보강·임베딩 후). dedupKey 로 중복 건너뜀 → `{saved, duplicates}` |
| GET | `/api/transactions?userId=&limit=` | 최근 거래 목록 |
| DELETE | `/api/transactions/{transactionId}?userId=` | 삭제 (본인 거래만) |
| POST | `/api/transactions/query` | 구조화 조회 (아래) → `{total, userTotal, matchType, aggregates, groups, transactions}` |
| GET | `/api/transactions/vocabulary?userId=` | 사용자의 실제 가맹점·업종·간편결제 목록 (질문 해석 LLM 에 제공) |
| POST | `/api/transactions/reenrich` | 업종 사전·보강 규칙 변경 후 기존 거래 전체 재보강 (관리용) |

**색인 (v2)** — 별칭 `receipt-transactions` → `receipt-transactions-v2`. 매핑을 바꾸면 `store.py` 의 `INDEX_VERSION` 을 올린다.
기동 시 새 인덱스를 만들고 이전 인덱스(별칭 대상 또는 `TRANSACTION_LEGACY_INDEX`) 문서를 다시 보강해 옮긴 뒤 별칭을 전환한다 (원본 보존).

| 저장 시 파생 필드 (`enrich.py`) | 해결하는 질문 |
| --- | --- |
| `merchant.category` / `merchant.brand` (업종·브랜드 사전) | "편의점에서…", "업종별로 얼마" |
| `merchant.normalizedName` (`(주)`·OCR 잡음 제거) | 가맹점별 집계가 같은 가게로 묶임 |
| `merchant.name.ngram`, `items.name.ngram`, `merchant.address.ngram` | "하나로" 처럼 형태소로 안 쪼개지는 부분 일치 |
| `dayOfWeek`, `transactedHour` | "주말에", "밤에", "요일별" |
| `searchText` (가맹점·업종·주소·품목·결제수단 한국어) | 키워드 검색 보조 |
| `embedding` (e5, knn_vector) | 단어가 안 맞을 때 의미 검색 후보 |

**조회 조건 (`TransactionQuery`)** — 검색어 그룹은 서로 AND, 그룹 안은 OR
`merchantTerms`(가맹점·브랜드) · `locationTerms`(지역 → 가맹점명·주소) · `itemTerms`+`categories`(무엇을: 품목 OR 업종) ·
`keywords`(성격 모를 단어, 단어별 그룹) · `semanticQuery`, 필터 `startDate/endDate(미포함)`, `provider`, `method`, `cardCompany`,
`status`, `minAmount/maxAmount`, `weekdays`, `hourFrom/hourTo`(자정 넘는 구간 가능), `sortBy`, `groupBy`
(`MERCHANT|CATEGORY|DAY|MONTH|WEEKDAY|METHOD|PROVIDER`), `limit`

**단계적 검색** (`matchType`)
1. `EXACT` 모든 그룹 일치 → 2. `RELAXED` 0건이면 그룹 하나 빠져도 허용 →
3. `SEMANTIC` 그래도 0건이면 벡터 후보 15건 → cross-encoder 재채점 → `TRANSACTION_SEMANTIC_MIN_SCORE`(0.02) 이상 & 1등×0.3 이상만.
   e5 점수만으로는 관련/무관 차이가 0.01 수준이라 리랭커로 거른다. 품목만 묻는 질문은 품목 있는 거래만 후보.

- `userId` 필터는 항상 강제, 저장 시 본문의 userId 는 무시하고 요청의 userId 로 덮어씀
- AGGREGATE 는 status 지정이 없으면 APPROVED 만 집계 (취소·환불 제외), 합계·건수는 항상 OpenSearch 집계

### 가계부 챗봇 API (`services/chat/`)

| Method | Endpoint | 설명 |
| --- | --- | --- |
| POST | `/api/chat/stream` | `{userId, message, history}` → SSE (`status` → `plan` → `result` → `token`… → `done`, 그 외 `notice`, `error`) |

```
질문 → 사용자 어휘 조회(가맹점·업종) → [질문 전처리 LLM] QueryPlan
     → 코드: 날짜 계산·userId 강제·LLM 플랜 보정 → 거래 조회(단계적) → [답변 LLM] 스트리밍
```
- **질문 전처리(증강)**: Gemini 에 인덱스 구조 + 사용자의 실제 가맹점·업종 목록을 함께 주고, 검색어를 필드별
  (가맹점/지역/품목/업종)로 나누고 동의어·하위 품목으로 확장하게 한다 ("스벅" → 스타벅스, "커피" → 아메리카노·라떼 + 카페)
- **보정**: 질문에 없는 기간 제거, 빠뜨린 필터(결제수단·금액·요일·시간대)를 규칙으로 보충, 업종 값 정규화
- **답변**: 숫자는 집계 그대로, `matchType` 이 RELAXED/SEMANTIC 이면 '일부 일치/비슷한 거래' 라고 밝힘
- Gemini 를 못 쓰면(키 없음·429·402) 규칙 해석 + 템플릿 답변. 설정: `CHAT_PLANNER`, `CHAT_ANSWERER`, `CHAT_MODEL`
- **품목 단위**: 저장 시 품목마다 분류(`items[].category`: 담배·주류·고기 …)를 붙이고, 품목 질문은 품목 금액·수량으로 집계
  ('담배에 쓴 돈' = 담배 품목 합계, 편의점 결제 전체가 아님). `groupBy=ITEM` 은 품목별 순위, 결과의 `matchedItems` 는 맞은 품목

**정확도 평가** (`tests/eval/chat_eval.py`) — 평가용 사용자 `eval` 에 '오늘' 기준 상대 날짜로 거래 23건을 넣고,
질문 101개(기간 합계·품목·가맹점·필터·후속 질문·애매한 질문)의 기대값을 같은 데이터에서 계산해 답변을 채점한다.
```
MSYS_NO_PATHCONV=1 docker run --rm --network host -v "<rag_backend>:/app" -w /app \
    --entrypoint python rag-base-backend:latest tests/eval/chat_eval.py --reset   # 이후엔 --reset 없이, -v 상세
```
(`EVAL_RAG_URL` 로 다른 인스턴스 지정 가능 — 예: `CHAT_PLANNER=rule CHAT_ANSWERER=template` 로 띄운 비상 모드)

---

## 5. 핵심 서비스 설명

### `domains/base.py`
`DomainSpec`(ABC). 엔진이 호출하는 도메인 인터페이스 정의.

### `domains/registry.py`
도메인 이름 → 구현 클래스 매핑(`DOMAIN_REGISTRY`)과 `load_domain(name)`. 알 수 없는 이름은 `ValueError`.

### `domains/__init__.py`
`.env`의 `ACTIVE_DOMAIN`으로 구현체를 선택해 `current_domain` 싱글톤으로 노출.

### `domains/person.py`, `domains/animal.py`
구체 도메인 구현체. 필드 매핑·정규화·임베딩 템플릿·인덱스 스키마·검색 쿼리·가중치·리랭킹 프롬프트를 자체 보유.

### `services/opensearch/data_loader_service.py`
CSV 읽기 → 도메인 문서 변환 → 임베딩 인코딩 → bulk 적재. 식별자 기준 중복 제거 포함.

### `services/opensearch/index_service.py`
인덱스 생성/삭제/존재확인/벌크/통계. 스키마는 `current_domain.build_index_body`가 만든 body를 사용.

### `services/opensearch/search_service.py`
하이브리드 검색 → Cross-encoder 리랭킹 → LLM 증강 단계를 연결. 쿼리/결과 매핑/리랭킹 입력 텍스트는 도메인에 위임. 리랭커 오류 시 하이브리드 순서로 폴백.

### `services/opensearch/query_processor.py`
검색어 전처리(트림/중복 공백 정리/외톨이 자모 제거/특수문자 제거). 도메인 독립.

### `services/opensearch/embedding_service.py`
`SentenceTransformer`(기본 `intfloat/multilingual-e5-base`, 768차원)로 인코딩. 쿼리/문서에 E5 prefix 적용. 모델은 지연 로딩.

### `services/rerank/`
`Reranker` 인터페이스(점수 산출 `score` + 공통 `rerank`: 점수 부여·정렬·상위 N 절단)와 `CrossEncoderReranker`(기본 `BAAI/bge-reranker-v2-m3`, 다국어). `RERANK_ENABLED=false`면 `NoopReranker`로 모델을 로딩하지 않음. 점수는 0~1(sigmoid)로 정규화.

### `services/llm/providers.py`, `services/llm/base.py`
`LLMProvider` 인터페이스와 `GeminiProvider`. `build_provider()`가 `LLM_PROVIDER` 값으로 구현체 선택.

### `services/llm/llm_service.py`
검색 후보를 도메인 프롬프트로 리랭킹하고 `confidence`/`reason` 부여. 도메인 의존 부분(`format_candidate`/`reranking_prompt`/`llm_response_id_key`)은 `current_domain`에서 가져옴. provider 미가용 시 Mock.

### `services/util/`
`norm()` 텍스트 정규화, `TextNormalizer`(규칙 기반 정규화), `TemplateEmbeddingComposer`(템플릿 기반 임베딩 텍스트 구성). 순수 모듈로 도메인이 조합해서 사용.

---

## 6. 새 도메인 추가 방법

1. `app/domains/<name>.py`에 `DomainSpec`을 구현한 클래스를 작성
2. `app/domains/registry.py`의 `DOMAIN_REGISTRY`에 `"<name>": <Class>` 등록
3. `.env`의 `ACTIVE_DOMAIN=<name>` 설정
4. 해당 도메인 인덱스를 생성하고 데이터를 적재

엔진(`services/*`)·LLM·API 코드는 수정하지 않음.

---

## 7. 실행 방법

사전 요구사항:

- Python 3.10+
- OpenSearch (기본 `localhost:9200`), **k-NN** 및 **analysis-nori** 플러그인 활성화
- (선택) Gemini API Key — 없으면 LLM은 Mock으로 동작

### 7-1. 의존성 설치

```powershell
cd <프로젝트 폴더>\backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### 7-2. 서버 실행

```powershell
cd <프로젝트 폴더>\backend
python -m app.main          # PORT 환경변수 기본 3001
# 또는
uvicorn app.main:app --reload --host 0.0.0.0 --port 3001
```

Swagger 문서:

```
http://127.0.0.1:3001/docs
```

---

## 8. 환경 변수 (.env)

`backend/.env`에 설정함(`app/core/config.py`가 로드).

```dotenv
# 활성 도메인 (app/domains 레지스트리 키: person | animal | ...)
ACTIVE_DOMAIN=person

# OpenSearch
OPENSEARCH_HOST=localhost
OPENSEARCH_PORT=9200
OPENSEARCH_TIMEOUT=3000
INDEX_NAME=person-embeddings-index
SEARCH_PIPELINE_NAME=hybrid-search-pipeline

# 임베딩 모델
EMBEDDING_MODEL=intfloat/multilingual-e5-base
EMBEDDING_DIMENSION=768
EMBEDDING_QUERY_PREFIX=query:
EMBEDDING_PASSAGE_PREFIX=passage:

# LLM (provider 미설정/키 없으면 Mock 동작)
LLM_PROVIDER=gemini
LLM_MODEL=gemini-2.5-flash-lite
GEMINI_API_KEY=
LLM_MAX_TOKENS=1024
LLM_TEMPERATURE=0.3

# Cross-encoder 리랭킹 (false면 모델 미로딩, 요청의 use_rerank 무시)
RERANK_ENABLED=true
RERANK_MODEL=BAAI/bge-reranker-v2-m3
RERANK_MAX_LENGTH=512
RERANK_BATCH_SIZE=16

# 최종 결과 건수 (하이브리드 후보 50건 → 상위 N건만 응답/LLM 전달)
RESULT_TOP_N=10

# 적재할 CSV 경로 (app/ 기준 상대경로)
DATA_CSV_PATH=test/person_dummy.csv

# 서버 포트
PORT=3001
```

검색 가중치(boost 등)는 도메인 클래스 상수로 관리되며 `.env`에서 설정하지 않음.

---

## 9. OpenSearch 사용 흐름

예시는 `person` 도메인 기준임(`ACTIVE_DOMAIN=person`, `INDEX_NAME=person-embeddings-index`).

### 9-1. 인덱스 생성

```powershell
Invoke-RestMethod -Method Post `
  -Uri "http://127.0.0.1:3001/api/search/index" `
  -ContentType "application/json" `
  -Body '{"index_name":"person-embeddings-index","recreate":true}'
```

### 9-2. 검색 파이프라인 생성 (하이브리드 점수 결합 — 최초 1회)

```powershell
Invoke-RestMethod -Method Post `
  -Uri "http://127.0.0.1:3001/api/search/search_pipeline" `
  -ContentType "application/json" `
  -Body '{"search_pipeline_name":"hybrid-search-pipeline","bm25_weight":0.6,"vector_weight":0.4}'
```

### 9-3. 데이터 적재

```powershell
Invoke-RestMethod -Method Post `
  -Uri "http://127.0.0.1:3001/api/search/load" `
  -ContentType "application/json" `
  -Body '{"index_name":"person-embeddings-index"}'
```

### 9-4. 검색 (RAG)

```powershell
Invoke-RestMethod -Method Post `
  -Uri "http://127.0.0.1:3001/api/search/" `
  -ContentType "application/json" `
  -Body '{"query":"판교 백엔드 개발자","use_llm":true}'
```

### 9-5. 인덱스 확인 / 통계 / 삭제

```powershell
# OpenSearch 직접 조회
Invoke-RestMethod -Method Get -Uri "http://localhost:9200/_cat/indices?v"

# 통계
Invoke-RestMethod -Method Get -Uri "http://127.0.0.1:3001/api/search/index/stats?index_name=person-embeddings-index"

# 삭제
Invoke-RestMethod -Method Delete -Uri "http://127.0.0.1:3001/api/search/index?index_name=person-embeddings-index"
```

---

## 10. 응답 구조

검색 응답(`SearchResponse`):

| 필드 | 설명 |
| --- | --- |
| `query` | 원본 검색어 |
| `results` | 결과 아이템 배열 |
| `llmModel` | 사용한 LLM 모델명(또는 `mock`/`none`) |
| `isMock` | LLM Mock 여부 |
| `isReranked` | Cross-encoder 리랭킹 실제 적용 여부 |
| `rerankModel` | 리랭킹 모델명(미적용 시 null) |
| `totalCount` | 결과 수 |

결과 아이템(`SearchResultItem`)은 공통 필드 + 도메인 필드로 구성됨.

- 공통: `id`, `confidence`, `reason`, `searchScore`, `rerankScore`(0~1, 리랭킹 미적용 시 null)
- 도메인 필드는 `map_search_hit` 출력이 그대로 통과됨 (Pydantic `extra="allow"`)
  - person: `name`, `age`, `school`, `job`, `location`
  - animal: `name`, `species`, `age`, `habitat`, `diet`

---

## 11. 참고

- 검색 가중치/사이즈는 각 도메인 클래스 상수에서 직접 관리함.
- 하이브리드 `hybrid` 쿼리는 OpenSearch에 검색 파이프라인(normalization-processor)이 등록되어 있어야 정상 동작함(9-2 참고).
