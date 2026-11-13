"""
가계부 챗봇 스키마

QueryPlan: 사용자 질문을 '조회 조건' 으로 구조화한 결과 (= 질문 전처리/증강 결과)
  - Gemini response_schema 로도 쓰인다. Gemini 는 기본값(default)이 있는 스키마를 거부하므로
    모든 필드를 '필수 + null/빈 목록 허용' 으로 두고, 코드에서는 QueryPlan.build() 로 만든다.
  - 날짜는 LLM 이 계산하지 않고 상대 표현(kind/unit/offset)으로만 표시 → period.py 가 계산.
  - 검색어는 OpenSearch 필드 구조에 맞춰 나눠 담는다 (가맹점/품목/지역/업종 + 의미검색 문장).
"""
from enum import Enum
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

from app.services.transactions.schemas import TransactionQueryResult


class Provider(str, Enum):
    KAKAOPAY = "KAKAOPAY"
    TOSSPAY = "TOSSPAY"
    NAVERPAY = "NAVERPAY"
    PAYCO = "PAYCO"
    SAMSUNGPAY = "SAMSUNGPAY"
    APPLEPAY = "APPLEPAY"


class PaymentMethod(str, Enum):
    CARD = "CARD"
    CASH = "CASH"
    PAY_MONEY = "PAY_MONEY"
    BANK_TRANSFER = "BANK_TRANSFER"
    POINT = "POINT"


class CardCompany(str, Enum):
    SHINHAN = "SHINHAN"
    KB = "KB"
    SAMSUNG = "SAMSUNG"
    HYUNDAI = "HYUNDAI"
    LOTTE = "LOTTE"
    WOORI = "WOORI"
    HANA = "HANA"
    BC = "BC"
    NH = "NH"
    CITI = "CITI"


class TransactionStatus(str, Enum):
    APPROVED = "APPROVED"
    CANCELED = "CANCELED"
    REFUNDED = "REFUNDED"


class Intent(str, Enum):
    AGGREGATE = "AGGREGATE"    # 얼마/몇 번/평균/어디에 많이 → OpenSearch 집계 (정확한 숫자)
    LIST = "LIST"              # 내역 보여줘 → 필터 + 최신순
    SEARCH = "SEARCH"          # 치즈케이크 산 데 어디? → 검색어 관련도순
    SMALLTALK = "SMALLTALK"    # 인사/사용법 → 조회 없음


class PeriodKind(str, Enum):
    NONE = "NONE"                    # 기간 언급 없음 (전체)
    RELATIVE = "RELATIVE"            # 오늘/어제/이번 주/지난달/작년 (unit + offset)
    LAST_N_DAYS = "LAST_N_DAYS"      # 최근 N일 (days)
    MONTH_OF_YEAR = "MONTH_OF_YEAR"  # 3월, 2025년 3월 (month, year 는 없으면 null)
    ABSOLUTE = "ABSOLUTE"            # 날짜를 직접 말한 경우 (startDate~endDate, YYYY-MM-DD)


class PeriodUnit(str, Enum):
    DAY = "DAY"
    WEEK = "WEEK"
    MONTH = "MONTH"
    YEAR = "YEAR"


class Metric(str, Enum):
    SUM = "SUM"
    COUNT = "COUNT"
    AVG = "AVG"
    MAX = "MAX"


class GroupBy(str, Enum):
    NONE = "NONE"
    MERCHANT = "MERCHANT"
    CATEGORY = "CATEGORY"
    DAY = "DAY"
    MONTH = "MONTH"
    WEEKDAY = "WEEKDAY"
    METHOD = "METHOD"
    PROVIDER = "PROVIDER"
    ITEM = "ITEM"


class Period(BaseModel):
    kind: PeriodKind
    unit: Optional[PeriodUnit] = Field(..., description="RELATIVE 일 때 단위")
    offset: Optional[int] = Field(..., description="RELATIVE: 0=이번, -1=지난, -2=지지난")
    days: Optional[int] = Field(..., description="LAST_N_DAYS: N")
    year: Optional[int] = Field(..., description="MONTH_OF_YEAR: 연도 (말하지 않았으면 null)")
    month: Optional[int] = Field(..., description="MONTH_OF_YEAR: 월")
    startDate: Optional[str] = Field(..., description="ABSOLUTE: YYYY-MM-DD")
    endDate: Optional[str] = Field(..., description="ABSOLUTE: YYYY-MM-DD (포함)")

    @classmethod
    def none(cls) -> "Period":
        return cls(kind=PeriodKind.NONE, unit=None, offset=None, days=None, year=None, month=None,
                   startDate=None, endDate=None)


class QueryPlan(BaseModel):
    intent: Intent
    period: Period
    merchantTerms: List[str] = Field(..., description="가맹점명/브랜드/지점명. 사용자 가맹점 목록에 있으면 그 이름")
    itemTerms: List[str] = Field(..., description="품목명과 그 동의어·하위 품목 (커피 → 커피, 아메리카노, 라떼)")
    locationTerms: List[str] = Field(..., description="지역/동네/역 이름 (역삼, 강남역)")
    categories: List[str] = Field(..., description="업종 (편의점, 카페, 마트 …)")
    keywords: List[str] = Field(..., description="위 어디에도 넣기 애매한 검색어 (보통 비움)")
    semanticQuery: Optional[str] = Field(..., description="의미 검색용 한 문장 (검색어가 없으면 null)")
    provider: Optional[Provider]
    method: Optional[PaymentMethod]
    cardCompany: Optional[CardCompany]
    status: Optional[TransactionStatus]
    minAmount: Optional[int] = Field(..., description="원 단위 최소 금액 (이상)")
    maxAmount: Optional[int] = Field(..., description="원 단위 최대 금액 (이하)")
    weekdays: List[int] = Field(..., description="요일 1=월 … 7=일 (주말 → [6,7])")
    hourFrom: Optional[int] = Field(..., description="시작 시(0~23)")
    hourTo: Optional[int] = Field(..., description="끝 시(0~23, 포함)")
    metric: Metric
    groupBy: GroupBy
    limit: Optional[int]

    @classmethod
    def build(cls, **kw: Any) -> "QueryPlan":
        base: Dict[str, Any] = dict(
            intent=Intent.LIST, period=Period.none(), merchantTerms=[], itemTerms=[], locationTerms=[],
            categories=[], keywords=[], semanticQuery=None, provider=None, method=None, cardCompany=None,
            status=None, minAmount=None, maxAmount=None, weekdays=[], hourFrom=None, hourTo=None,
            metric=Metric.SUM, groupBy=GroupBy.NONE, limit=None,
        )
        base.update(kw)
        return cls(**base)

    def search_terms(self) -> List[str]:
        """화면 표시용 검색어 모음"""
        seen, out = set(), []
        for t in self.categories + self.merchantTerms + self.locationTerms + self.itemTerms + self.keywords:
            if t and t not in seen:
                seen.add(t)
                out.append(t)
        return out


# ============================================================================
# API
# ============================================================================
class ChatTurn(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class ChatRequest(BaseModel):
    userId: str = Field(..., min_length=1)
    message: str = Field(..., min_length=1, max_length=500)
    history: List[ChatTurn] = Field([], description="이전 대화 (후속 질문 해석용)")


class QueryResult(TransactionQueryResult):
    """챗봇 응답용 조회 결과 (= 저장소 결과 + 의도·기간 라벨)"""
    intent: Intent
    periodLabel: str
