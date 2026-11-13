"""
거래(영수증·결제내역) 저장/조회 API 스키마

거래 문서 자체의 형식(Transaction, schemaVersion 1)은 ocr_backend 가 정의·검증한다.
여기서는 dict 로 받고, 저장 시 파생 필드(업종·브랜드·요일·임베딩 …)를 붙인 뒤
인덱스 매핑(dynamic=strict)이 정의되지 않은 필드를 거부한다.
"""
from datetime import date
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class QueryMode(str, Enum):
    AGGREGATE = "AGGREGATE"  # 합계/건수/평균 + 그룹 집계
    LIST = "LIST"            # 필터 + 정렬
    SEARCH = "SEARCH"        # 키워드 관련도순


class GroupBy(str, Enum):
    NONE = "NONE"
    MERCHANT = "MERCHANT"
    CATEGORY = "CATEGORY"
    DAY = "DAY"
    MONTH = "MONTH"
    WEEKDAY = "WEEKDAY"
    METHOD = "METHOD"
    PROVIDER = "PROVIDER"
    ITEM = "ITEM"            # 품목별 (가장 많이 산 것)


class GroupOrder(str, Enum):
    SUM = "SUM"        # 금액 순 (어디에 제일 많이 썼지)
    COUNT = "COUNT"    # 횟수/수량 순 (어디를 제일 많이 갔지, 뭘 제일 많이 샀지)


class SortBy(str, Enum):
    DATE = "DATE"      # 최신순
    AMOUNT = "AMOUNT"  # 금액 큰 순


class MatchType(str, Enum):
    """검색어가 있을 때 어떤 단계에서 찾았는지 (답변에서 '비슷한 거래' 라고 구분해 말하기 위함)"""
    NONE = "NONE"          # 검색어 없음 (필터만)
    EXACT = "EXACT"        # 모든 검색 조건 일치
    RELAXED = "RELAXED"    # 일부 검색어만 일치
    SEMANTIC = "SEMANTIC"  # 단어는 안 맞고 의미(벡터)로 찾음


class SaveTransactionsRequest(BaseModel):
    userId: str = Field(..., min_length=1)
    transactions: List[Dict[str, Any]] = Field(..., description="ocr_backend Transaction(JSON) 목록")


class SaveTransactionsResponse(BaseModel):
    saved: List[str] = Field(..., description="저장된 transactionId")
    duplicates: List[str] = Field([], description="이미 저장된 거래라 건너뛴 transactionId")


class TransactionRow(BaseModel):
    """화면/LLM 용 축약 거래"""
    transactionId: str
    date: Optional[str]
    time: Optional[str]
    merchant: Optional[str]
    category: Optional[str] = None
    amount: Optional[int]
    status: str
    method: Optional[str]
    provider: Optional[str]
    cardCompany: Optional[str]
    items: List[str] = []
    matchedItems: List[str] = Field([], description="검색 품목 조건에 맞은 품목 (예: '에쎄 체인지 1mg x2')")


class TransactionQuery(BaseModel):
    """
    구조화된 거래 조회 조건 (질문 해석·날짜 계산은 호출 측 책임)

    검색어 그룹은 서로 AND, 그룹 안의 단어는 OR(동의어·확장어)로 매칭한다.
      merchantTerms : 가맹점명/브랜드/지점 ("스타벅스", "GS25 역삼점")
      itemTerms     : 품목 ("아메리카노", "라떼", "커피")
      locationTerms : 지역 ("역삼", "강남") → 가맹점명·주소
      keywords      : 어디에 속하는지 모르는 단어 → 각 단어를 모든 필드에서 (규칙 해석기용)
      categories    : 업종 ("편의점", "카페") → merchant.category + 브랜드 사전
    결과가 0건이면 조건을 완화(RELAXED)하고, 그래도 없으면 semanticQuery 로 의미 검색(SEMANTIC)한다.
    """
    userId: str = Field(..., min_length=1)
    mode: QueryMode
    startDate: Optional[date] = Field(None, description="포함")
    endDate: Optional[date] = Field(None, description="미포함")
    keywords: List[str] = []
    merchantTerms: List[str] = []
    itemTerms: List[str] = []
    locationTerms: List[str] = []
    categories: List[str] = []
    semanticQuery: Optional[str] = Field(None, description="의미 검색용 문장 (단어 검색 0건일 때)")
    provider: Optional[str] = None
    method: Optional[str] = None
    cardCompany: Optional[str] = None
    status: Optional[str] = Field(None, description="없으면 AGGREGATE 는 APPROVED 만 집계")
    statuses: List[str] = Field([], description="여러 상태 중 하나 (예: 취소·환불 함께). status 보다 우선")
    minAmount: Optional[int] = None
    maxAmount: Optional[int] = None
    weekdays: List[int] = Field([], description="1=월 … 7=일")
    hourFrom: Optional[int] = Field(None, ge=0, le=23)
    hourTo: Optional[int] = Field(None, ge=0, le=23, description="포함. hourFrom > hourTo 면 자정을 넘는 구간")
    sortBy: SortBy = SortBy.DATE
    groupBy: GroupBy = GroupBy.NONE
    groupOrder: GroupOrder = GroupOrder.SUM
    limit: int = Field(20, ge=1, le=100)

    def has_text(self) -> bool:
        return bool(self.keywords or self.merchantTerms or self.itemTerms or self.locationTerms or self.categories)


class TransactionQueryResult(BaseModel):
    total: int = Field(..., description="조건에 맞는 거래 수")
    userTotal: Optional[int] = Field(None, description="조건과 무관한 이 사용자의 전체 거래 수 (0건일 때만 채움)")
    matchType: MatchType = MatchType.NONE
    aggregates: Dict[str, Optional[float]] = {}
    itemAggregates: Dict[str, Optional[float]] = Field(
        {}, description="품목 조건이 있을 때 품목 단위 합계 {sum, quantity, lines} (거래 전체 금액이 아님)")
    groups: List[Dict[str, Any]] = []
    transactions: List[TransactionRow] = []


class UserVocabulary(BaseModel):
    """질문 해석 LLM 에 넘기는 '이 사용자의 데이터에 실제로 있는 값' (검색어를 실제 값에 맞추기 위함)"""
    total: int
    merchants: List[str] = []
    categories: List[str] = []
    providers: List[str] = []
    firstDate: Optional[str] = None
    lastDate: Optional[str] = None
