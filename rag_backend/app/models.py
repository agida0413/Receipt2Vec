"""
API 요청/응답 모델 (Pydantic)

검색 결과 아이템은 도메인이 정의한 필드(map_search_hit 출력)를 그대로 노출한다.
엔진이 강제하는 구조적 키는 'id' 뿐이며, 도메인 필드는 extra="allow" 로 통과시킨다.
"""
from typing import List, Optional
from pydantic import BaseModel, Field, ConfigDict


# ============================================================================
# 검색
# ============================================================================
class SearchRequest(BaseModel):
    query: str = Field(..., description="검색어")
    use_llm: bool = Field(True, description="True면 LLM 증강(confidence/reason), False면 LLM 미사용")
    use_rerank: bool = Field(True, description="True면 cross-encoder 리랭킹 적용, False면 하이브리드 순서 그대로")
    index_name: Optional[str] = Field(None, description="인덱스명(미지정 시 기본값)")
    search_pipeline_name: Optional[str] = Field(None, description="검색 파이프라인명(미지정 시 기본값)")


class SearchResultItem(BaseModel):
    # 도메인이 정의한 필드(name/age/school/job/location 등)는 extra 로 통과시킨다.
    model_config = ConfigDict(extra="allow")

    id: str = Field("", description="고유 식별자(엔진이 요구하는 유일한 구조적 키)")
    confidence: Optional[int] = Field(None, description="LLM 신뢰도(0~100), 미사용 시 None")
    reason: Optional[str] = Field(None, description="추천 근거, 미사용 시 None")
    searchScore: float = Field(0, description="1차 하이브리드 검색 점수")
    rerankScore: Optional[float] = Field(None, description="cross-encoder 관련도(0~1), 미적용 시 None")


class SearchResponse(BaseModel):
    query: str
    results: List[SearchResultItem]
    llmModel: str
    isMock: bool
    isReranked: bool = Field(False, description="cross-encoder 리랭킹이 실제 적용되었는지")
    rerankModel: Optional[str] = Field(None, description="리랭킹 모델명(미적용 시 None)")
    totalCount: int


# ============================================================================
# 인덱스 생성 (적재와 분리된 별도 API)
# ============================================================================
class CreateIndexRequest(BaseModel):
    index_name: Optional[str] = Field(None, description="인덱스명(미지정 시 기본값)")
    recreate: bool = Field(False, description="True면 기존 인덱스 삭제 후 재생성")


class CreateIndexResponse(BaseModel):
    indexName: str
    created: bool = Field(..., description="인덱스가 새로 생성(또는 재생성)되었는지")
    recreated: bool = Field(False, description="기존 인덱스를 삭제 후 재생성했는지")
    message: str


# ============================================================================
# 데이터 적재 (인덱스는 사전에 생성되어 있어야 함)
# ============================================================================
class LoadDataRequest(BaseModel):
    index_name: Optional[str] = Field(None, description="인덱스명")
    max_rows: Optional[int] = Field(None, description="최대 처리 건수(None=전체)")


class LoadDataResponse(BaseModel):
    success: int
    failed: int
    indexName: str
    message: str


# ============================================================================
# 검색 파이프라인 생성
# ============================================================================
class CreateSearchPipelineRequest(BaseModel):
    search_pipeline_name: Optional[str] = Field(None, description="파이프라인명")
    vector_weight: float = Field(..., description="벡터 검색 가중치")
    bm25_weight: float = Field(..., description="BM25 텍스트 검색 가중치")
