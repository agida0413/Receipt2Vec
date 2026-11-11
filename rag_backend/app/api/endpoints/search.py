"""
검색 API 엔드포인트
"""
from fastapi import APIRouter, HTTPException,status
from app.core.config import settings
from app.models import (
    SearchRequest, SearchResponse,
    CreateIndexRequest, CreateIndexResponse,
    LoadDataRequest, LoadDataResponse,
    CreateSearchPipelineRequest,
)
from app.services.opensearch import search_service, index_service, data_loader

router = APIRouter(prefix="/search", tags=["search"])

#================================================================
# 검색
#================================================================
@router.post("/", response_model=SearchResponse)
async def search_items(request: SearchRequest):
    """
     검색 API (하이브리드 → Cross-encoder → LLM)

    - use_rerank=True: cross-encoder 로 후보 재채점 후 상위 N 건 (False 면 하이브리드 순서 상위 N 건)
    - use_llm=True   : 상위 N 건에 LLM confidence/reason 부여
    """
    try:
        if request.use_llm:
            result = search_service.search_with_llm(
                query_text=request.query,
                index_name=request.index_name,
                search_pipeline_name=request.search_pipeline_name,
                use_rerank=request.use_rerank,
            )
            results = result["results"]
            llm_model, is_mock = result["llmModel"], result["isMock"]
        else:
            result = search_service.search(
                query_text=request.query,
                index_name=request.index_name,
                search_pipeline_name=request.search_pipeline_name,
                use_rerank=request.use_rerank,
            )
            # LLM 없이 검색 결과 변환 (도메인 필드 그대로 통과: score→searchScore)
            results = []
            for r in result["results"]:
                item = dict(r)
                item["searchScore"] = item.pop("score", 0)
                item["confidence"] = None
                item["reason"] = None
                results.append(item)
            llm_model, is_mock = "none", False

        return SearchResponse(
            query=result["query"],
            results=results,
            llmModel=llm_model,
            isMock=is_mock,
            isReranked=result["isReranked"],
            rerankModel=result["rerankModel"],
            totalCount=len(results)
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


#================================================================
# 인덱스 생성 
#================================================================
@router.post("/index", response_model=CreateIndexResponse)
async def create_index(request: CreateIndexRequest):
    """
    인덱스 생성 API (스키마는 활성 도메인이 설계)

    - recreate=False: 없으면 생성, 이미 있으면 변경 없음
    - recreate=True : 기존 인덱스 삭제 후 재생성
    """
    idx = request.index_name or settings.opensearch.index_name
    try:
        recreated = False
        if request.recreate and index_service.index_exists(idx):
            index_service.delete_index(idx)
            recreated = True

        created = index_service.build_index(idx)

        if recreated:
            message = f"인덱스 '{idx}' 재생성 완료"
        elif created:
            message = f"인덱스 '{idx}' 생성 완료"
        else:
            message = f"인덱스 '{idx}' 이미 존재 (변경 없음)"

        return CreateIndexResponse(
            indexName=idx,
            created=created or recreated,
            recreated=recreated,
            message=message
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


#================================================================
# 데이터적재 (인덱스는 사전에 POST /index 로 생성되어 있어야 함)
#================================================================
@router.post("/load", response_model=LoadDataResponse)
async def load_data(request: LoadDataRequest):
    """
    데이터 적재 API (인덱스 생성은 POST /index 로 분리됨)
    """
    idx = request.index_name or settings.opensearch.index_name

    # 인덱스가 없으면 적재 거부 (동적 매핑 인덱스 자동생성 방지)
    if not index_service.index_exists(idx):
        raise HTTPException(
            status_code=400,
            detail=f"인덱스 '{idx}' 가 없습니다. 먼저 POST /api/search/index 로 생성하세요."
        )

    try:
        # 데이터 적재 (CSV 파일은 설정값 사용)
        result = data_loader.load_to_opensearch(
            index_name=idx,
            max_rows=request.max_rows
        )

        # failed는 리스트일 수 있음 (실패한 문서 목록)
        failed_count = len(result["failed"]) if isinstance(result["failed"], list) else result["failed"]

        return LoadDataResponse(
            success=result["success"],
            failed=failed_count,
            indexName=idx,
            message=f"적재 완료: {result['success']}건 성공, {failed_count}건 실패"
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

#================================================================
# 하이브리드 쿼리구성을 위해 검색파이프라인생성
#================================================================
@router.post("/search_pipeline")
async def create_search_pipeline(request:CreateSearchPipelineRequest):
    search_service.create_search_pipeline(request.search_pipeline_name,request.vector_weight,request.bm25_weight)
    return {
            "search_pipeline_name":request.search_pipeline_name,
            "bm25_weight":request.bm25_weight,
            "vector_weight":request.vector_weight,
            "create": True
     }


#================================================================
# 부가API(인덱스통계,삭제)
#================================================================
@router.get("/index/stats")
async def get_index_stats(index_name: str = None):
    """인덱스 통계 조회"""
    return index_service.get_index_stats(index_name)


@router.delete("/index")
async def delete_index(index_name: str = None):
    """인덱스 삭제"""
    idx = index_name or settings.opensearch.index_name
    deleted = index_service.delete_index(idx)
    return {"deleted": deleted, "index_name": idx}
