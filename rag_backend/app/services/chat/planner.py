"""
질문 전처리 (쿼리 증강): 질문 → QueryPlan (OpenSearch 거래 인덱스 구조에 맞춘 조회 조건)

- rule_plan  : 정규식 기반. 자주 쓰는 표현(어제/지난달/얼마/내역 …)은 LLM 없이 처리 → 할당량 절약, LLM 장애 시 대체
- LlmPlanner : Gemini 구조화 출력. 아래 정보를 함께 넘겨 '찾기 쉬운' 조건으로 증강한다.
    · 인덱스 구조 설명 (어떤 필드에 무엇이 있는지)
    · 이 사용자의 실제 가맹점·업종·간편결제 목록 (store.vocabulary) → 검색어를 실제 값에 맞춤
    · 동의어/하위 품목 확장 (커피 → 아메리카노, 라떼), 업종 매핑 (편의점 → 업종 필드)
    · 의미 검색용 문장 (단어 검색이 0건일 때 사용)
- sanitize_llm_plan : LLM 결과 교차 검증 (질문에 없는 기간 제거, 누락된 필터 보충, 업종 정규화)
둘 다 날짜를 직접 계산하지 않고 상대 표현만 만든다 (period.py 가 계산).
"""
import json
import logging
import re
from datetime import date
from typing import List, Optional, Tuple

from app.core.config import settings
from app.services.chat.gemini import QuotaExceededError, QuotaGuard, call_with_retry, get_client
from app.services.chat.schemas import (
    CardCompany, ChatTurn, GroupBy, Intent, Metric, PaymentMethod, Period, PeriodKind, PeriodUnit, Provider,
    QueryPlan, TransactionStatus,
)
from app.services.transactions.enrich import (
    BRAND_ALIASES, CATEGORY_NAMES, ITEM_CATEGORIES, is_item_word, strip_suffix, to_category, to_item_category,
)
from app.services.transactions.schemas import UserVocabulary

logger = logging.getLogger(__name__)


# ============================================================================
# 규칙 기반
# ============================================================================
_RELATIVE = [
    (r"오늘|금일", PeriodUnit.DAY, 0), (r"어제|어저께|전날", PeriodUnit.DAY, -1),
    (r"그제|그저께|엊그제", PeriodUnit.DAY, -2),
    (r"이번\s*주|금주|이번주|요번\s*주", PeriodUnit.WEEK, 0), (r"지난\s*주|저번\s*주", PeriodUnit.WEEK, -1),
    (r"이번\s*달|이달|금월|요번\s*달", PeriodUnit.MONTH, 0), (r"지난\s*달|저번\s*달|전월", PeriodUnit.MONTH, -1),
    (r"올해|금년|이번\s*년", PeriodUnit.YEAR, 0), (r"작년|지난\s*해|전년", PeriodUnit.YEAR, -1),
]
_N_DAYS_AGO = re.compile(r"(\d{1,3})\s*일\s*전")
_LAST_N_DAYS = re.compile(r"최근\s*(\d{1,3})\s*일|지난\s*(\d{1,3})\s*일")
_LAST_N_WEEKS = re.compile(r"최근\s*(\d{1,2})\s*주")
_YEAR_MONTH = re.compile(r"(20\d{2})\s*년\s*(\d{1,2})\s*월")
_MONTH = re.compile(r"(?<!\d)(\d{1,2})\s*월(?!\s*\d{1,2}\s*일)")
_ABS_DATE = re.compile(r"(20\d{2})[.\-/년]\s*(\d{1,2})[.\-/월]\s*(\d{1,2})")
_MONTH_DAY = re.compile(r"(?<!\d)(\d{1,2})\s*월\s*(\d{1,2})\s*일")

_COUNT = re.compile(r"몇\s*(번|건|회)|횟수|건수")
_AVG = re.compile(r"평균")
_MAX = re.compile(r"(제일|가장)\s*(비싼|큰)|최고\s*금액|최대")
_SUM = re.compile(r"얼마|총|합계|합산|지출|썼|쓴\s*돈|사용\s*금액|사용액|소비|씀|쓴\s*금액")
_SUM_MONEY = re.compile(r"얼마|돈|금액|썼|쓴|지출|비용|값")
# '가장 최근 거래', '마지막으로 간 게' → 최신순 1건 ('최근 30일' 같은 기간 표현은 제외)
_RECENT_ONE = re.compile(r"(가장|제일)?\s*(최근|마지막)(으로|에)?\s*(거래|결제|간|산|쓴|먹은|이용|방문|구매|한)")
_GROUP_MERCHANT = re.compile(
    r"(어디|가맹점|가게|매장).{0,6}(많이|자주|제일|가장)|(많이|자주).{0,6}(어디|간\s*곳|쓴\s*곳)|가맹점\s*별|순위|top",
    re.I,
)
_GROUP_CATEGORY = re.compile(r"업종\s*별|카테고리\s*별|분야\s*별|어떤\s*(종류|업종)")
# '제일 많이 간/산/먹은/핀' → 횟수·수량 기준, '뭐를 제일 많이' → 품목별
_FREQ = re.compile(r"(제일|가장|자주|많이).{0,8}(갔|간|가는|먹|샀|산|사\s*먹|폈|피운|핀|피웠|마신|마셨|시킨|시켜|이용)")
# 품목 순위는 '많이/자주' 가 있어야 함 ('가장 최근 거래 뭐야' 는 품목 순위가 아님)
_GROUP_ITEM = re.compile(r"(뭐|무엇|뭘|무슨|어떤\s*(거|것|걸|제품|품목)).{0,12}(많이|자주)|"
                         r"(많이|자주).{0,12}(뭐|무엇|뭘|무슨|어떤\s*(거|것|걸))|품목\s*별")
_QTY = re.compile(r"몇\s*(갑|개|병|잔|캔|봉지|인분|번\s*샀)")
_GROUP_DAY = re.compile(r"일\s*별|날짜\s*별|하루\s*하루")
# '제일 많이 쓴 날' → 날짜별 금액 순위
_TOP_DAY = re.compile(r"(제일|가장)\s*많이\s*(쓴|쓴\s*돈|지출한|결제한)\s*날|어느\s*날.{0,6}(제일|가장)\s*많이")
_HOUR_BEFORE = re.compile(r"(\d{1,2})\s*시\s*(전|이전|before)")
_HOUR_AFTER = re.compile(r"(\d{1,2})\s*시\s*(이후|후|넘어|넘어서|부터)")
_GROUP_MONTH = re.compile(r"월\s*별|달\s*별|매달")
_GROUP_WEEKDAY = re.compile(r"요일\s*별|무슨\s*요일")
_GROUP_METHOD = re.compile(r"결제\s*수단\s*별|수단\s*별")
_LIST = re.compile(r"내역|목록|리스트|보여|알려|뭐\s*(샀|썼|먹)|무엇|어떤|언제|어디서|전부|다\s*줘")
_LIST_EXPLICIT = re.compile(r"내역|목록|리스트|보여|뭐\s*(샀|먹|마셨)|뭘|무엇|어디서|언제")
_CANCEL = re.compile(r"취소")
_REFUND = re.compile(r"환불")
_SMALLTALK = re.compile(r"^(안녕|하이|hi|hello|도움|사용법|뭐\s*할\s*수|누구)", re.I)

_AMOUNT = r"(\d[\d,]*)?\s*(만|천)?\s*원"  # 5000원, 3만원, 만원
_MIN_AMOUNT = re.compile(_AMOUNT + r"\s*(이상|넘는|넘게|초과|부터)")
_MAX_AMOUNT = re.compile(_AMOUNT + r"\s*(이하|미만|아래|안\s*되는|까지)")
_WEEKDAYS = [(r"주말", [6, 7]), (r"평일", [1, 2, 3, 4, 5])] + [
    (rf"{d}요일", [i + 1]) for i, d in enumerate("월화수목금토일")]
_HOURS = [(r"새벽", 0, 5), (r"아침|오전", 6, 11), (r"점심", 11, 14), (r"오후", 12, 17), (r"저녁", 17, 21),
          (r"밤|야간|심야", 21, 2)]

_METHOD_WORDS = [
    (r"현금", PaymentMethod.CASH), (r"페이\s*머니|토스\s*머니|머니", PaymentMethod.PAY_MONEY),
    (r"계좌|이체", PaymentMethod.BANK_TRANSFER), (r"포인트", PaymentMethod.POINT),
    (r"카드", PaymentMethod.CARD),
]
_PROVIDER_PATTERNS = [
    (Provider.KAKAOPAY, r"카카오\s*페이|kakao\s*pay|페이\s*머니"), (Provider.TOSSPAY, r"토스|toss"),
    (Provider.NAVERPAY, r"네이버\s*페이|naver\s*pay|N\s*pay"), (Provider.PAYCO, r"페이코|payco"),
    (Provider.SAMSUNGPAY, r"삼성\s*페이|samsung\s*pay"), (Provider.APPLEPAY, r"애플\s*페이|apple\s*pay"),
]
_CARD_PATTERNS = [
    (CardCompany.SHINHAN, r"신한"), (CardCompany.KB, r"KB|국민"), (CardCompany.SAMSUNG, r"삼성\s*카드"),
    (CardCompany.HYUNDAI, r"현대"), (CardCompany.LOTTE, r"롯데"), (CardCompany.WOORI, r"우리"),
    (CardCompany.HANA, r"하나|외환"), (CardCompany.BC, r"\bBC\b|비씨"), (CardCompany.NH, r"\bNH\b|농협"),
    (CardCompany.CITI, r"씨티"),
]

# 검색어 추출 시 지울 표현 (기간/의도/결제수단/금액/요일/시간대 단어)
_STRIP_PATTERNS = [p for p, _, _ in _RELATIVE] + [
    _N_DAYS_AGO.pattern, _LAST_N_DAYS.pattern, _LAST_N_WEEKS.pattern, _YEAR_MONTH.pattern,
    _ABS_DATE.pattern, _MONTH_DAY.pattern, _MONTH.pattern, _MIN_AMOUNT.pattern, _MAX_AMOUNT.pattern,
    r"카카오\s*페이|토스\s*페이|네이버\s*페이|페이코|삼성\s*페이|애플\s*페이|토스|카카오",
    r"신한|국민|KB|삼성|현대|롯데|우리|하나|BC|비씨|NH|씨티",
] + [p for p, _ in _WEEKDAYS] + [p for p, _, _ in _HOURS]
_STOPWORDS = set("""
나 내 내가 제가 저 우리 좀 줘 주세요 알려 알려줘 보여 보여줘 해줘 했어 했지 했나 했던 썼어 썼지 썼나 쓴 쓰고
돈 거 것 건 번 회 결제 사용 지출 소비 얼마 얼마나 언제 어디 어디서 어디에 뭐 무엇 몇 내역 목록 리스트 전부 다 모두
있어 있나 있었어 샀어 샀지 산 샀던 구매 구입 금액 총 합계 합산 평균 제일 가장 많이 자주 비싼 큰 최근 동안 기간
카드 현금 계좌 이체 포인트 머니 페이 취소 환불 된 한 하고 해서 그리고 그럼 그때 정도 대략 혹시 요 에 에서 별
곳 가게 매장 가맹점 순위 top 일별 월별 날짜별 수단별 결제수단 결제수단별 기록 거래 확인 사용한 쓴돈 지출한 소비한 결제한
몇번 몇회 몇건 몇개 감 갔음 씀 동네 우리 요번 요즘 너무 같은데 것같은데 거야 거지
있는 잇는 있던 위치한 근처 쪽 거기 여기 뭘 업종별 업종 카테고리 종류 요일별 요일 시간 이상 이하 미만 초과
먹었던 먹은 먹었어 먹었지 사먹은 사먹었어 폈지 폈어 피운 핀 마신 마셨어 마셨지 시킨 시켜먹은 갔지 갔어 간 갔던 이름
몇갑 갑 개 병 잔 날 때 적 언제 어디를 무엇을 탄 받은 받았어 나온 나왔어 등록 등록했어
""".split())
_JOSA = re.compile(r"(에서|에게|으로|로|에|을|를|이|가|은|는|의|도|만|랑|이랑|하고|과|와|까지|부터|이야|야|요)$")


def to_provider(text: str) -> Optional[Provider]:
    for provider, pattern in _PROVIDER_PATTERNS:
        if re.search(pattern, text, re.I):
            return provider
    return None


def to_card_company(text: str) -> Optional[CardCompany]:
    for company, pattern in _CARD_PATTERNS:
        if re.search(pattern, text, re.I):
            return company
    return None


def _won(num: Optional[str], unit: Optional[str]) -> Optional[int]:
    if not num and not unit:
        return None  # '원' 만 있음
    n = int(num.replace(",", "")) if num else 1
    return n * {"만": 10000, "천": 1000}.get(unit or "", 1)


def _period_from_text(text: str) -> Period:
    p = Period.none()
    m = _ABS_DATE.search(text)
    if m:
        d = f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
        return p.model_copy(update={"kind": PeriodKind.ABSOLUTE, "startDate": d, "endDate": d})
    m = _MONTH_DAY.search(text)
    if m:
        from app.services.chat.period import today
        t = today()
        mo, dd = int(m.group(1)), int(m.group(2))
        y = t.year if (mo, dd) <= (t.month, t.day) else t.year - 1
        try:
            d = date(y, mo, dd).isoformat()
            return p.model_copy(update={"kind": PeriodKind.ABSOLUTE, "startDate": d, "endDate": d})
        except ValueError:
            pass
    m = _N_DAYS_AGO.search(text)
    if m:
        return p.model_copy(update={"kind": PeriodKind.RELATIVE, "unit": PeriodUnit.DAY,
                                    "offset": -int(m.group(1))})
    m = _LAST_N_DAYS.search(text)
    if m:
        return p.model_copy(update={"kind": PeriodKind.LAST_N_DAYS, "days": int(m.group(1) or m.group(2))})
    m = _LAST_N_WEEKS.search(text)
    if m:
        return p.model_copy(update={"kind": PeriodKind.LAST_N_DAYS, "days": int(m.group(1)) * 7})
    for pattern, unit, off in _RELATIVE:
        if re.search(pattern, text):
            return p.model_copy(update={"kind": PeriodKind.RELATIVE, "unit": unit, "offset": off})
    m = _YEAR_MONTH.search(text)
    if m:
        return p.model_copy(update={"kind": PeriodKind.MONTH_OF_YEAR, "year": int(m.group(1)),
                                    "month": int(m.group(2))})
    m = _MONTH.search(text)
    if m and 1 <= int(m.group(1)) <= 12:
        return p.model_copy(update={"kind": PeriodKind.MONTH_OF_YEAR, "month": int(m.group(1))})
    return p


_NOISE_PREFIX = re.compile(
    r"(뭐|뭘|무엇|무슨|이름|어떤|제일|가장|많이|자주|총|얼마|사먹|먹었|먹은|마셨|마신|폈|피운|피웠|샀|산게|산거|늦게|일찍|"
    r"마지막|최근|처음|보통|주로|대충|혹시|비교|어때|어땠|어떻|대비|차이|하루|매일|일평균|하루평균|"
    r"거기|그거|그곳|그건|그것|그가게|몇|먹으러|사러|마시러|언제|마심|먹음|샀음|말고)")
# 상태·동작 단어의 활용형 (환불한, 취소된, 결제했던, 구매한 …) → 필터/의도이지 검색어가 아님
_ACTION_PREFIX = re.compile(r"(환불|취소|결제|구매|구입|사용|지출|소비|이용|승인)")


def is_noise_term(tok: str, raw: Optional[str] = None) -> bool:
    """검색어로 쓰면 안 되는 단어 (질문어·부사·동사 활용·기간/수량 표현). 규칙 해석과 LLM 플랜 보정이 같이 쓴다"""
    tok = (tok or "").strip()
    if not tok or tok in _STOPWORDS or (raw and raw in _STOPWORDS) or tok.isdigit():
        return True
    if len(tok) < 2 and not (to_category(tok) or to_item_category(tok)):  # 술·약·밥·책 은 허용
        return True
    if re.fullmatch(r"\d+(원|일|월|년|주|번|건|시)?", tok):
        return True
    if _NOISE_PREFIX.match(tok) or _ACTION_PREFIX.match(tok):  # 뭐샀었지, 이름이뭐지, 총얼마, 얼마씩, 늦게 …
        return True
    # 동사/어미로 끝나는 짧은 토큰 (먹었어, 갔던 …)
    return bool(re.search(r"(었|았|했|겠|던|어요|어|지|나|니|까)$", tok) and len(tok) <= 4)


def _terms_from_text(text: str) -> List[str]:
    t = text
    for pattern in _STRIP_PATTERNS:
        t = re.sub(pattern, " ", t, flags=re.I)
    tokens: List[str] = []
    for raw in re.findall(r"[가-힣A-Za-z0-9]+", t):
        tok = _JOSA.sub("", raw)
        if not is_noise_term(tok, raw) and tok not in tokens:
            tokens.append(tok)
    return tokens


def _time_filters(text: str) -> dict:
    out: dict = {}
    m = _MIN_AMOUNT.search(text)
    if m and _won(m.group(1), m.group(2)) is not None:
        out["minAmount"] = _won(m.group(1), m.group(2))
    m = _MAX_AMOUNT.search(text)
    if m and _won(m.group(1), m.group(2)) is not None:
        out["maxAmount"] = _won(m.group(1), m.group(2))
    days: List[int] = []
    for pattern, ds in _WEEKDAYS:
        if re.search(pattern, text):
            days += [d for d in ds if d not in days]
    if days:
        out["weekdays"] = days
    for pattern, lo, hi in _HOURS:
        if re.search(pattern, text):
            out["hourFrom"], out["hourTo"] = lo, hi
            break
    m = _HOUR_BEFORE.search(text)
    if m and 1 <= int(m.group(1)) <= 24:
        out["hourFrom"], out["hourTo"] = 0, int(m.group(1)) - 1
    m = _HOUR_AFTER.search(text)
    if m and int(m.group(1)) <= 23:
        out["hourFrom"], out["hourTo"] = int(m.group(1)), 23
    return out


def rule_plan(message: str) -> QueryPlan:
    text = message.strip()
    period = _period_from_text(text)
    categories, items, merchants, keywords = [], [], [], []
    for tok in _terms_from_text(text):
        if tok in BRAND_ALIASES:            # 스벅 → 스타벅스
            merchants.append(BRAND_ALIASES[tok])
            continue
        tok = strip_suffix(tok)             # 담배값 → 담배, 김밥집 → 김밥, 영화비 → 영화
        cat, icat = to_category(tok), to_item_category(tok)
        if cat and cat not in categories:
            categories.append(cat)          # 업종 (편의점, 카페 …)
        if (icat or is_item_word(tok)) and tok not in items:
            items.append(tok)               # 품목 분류·상품명 (담배, 삼겹살 …) → 품목 단위 집계
        if not cat and not icat and not is_item_word(tok):
            keywords.append(tok)

    provider = to_provider(text)
    card_company = to_card_company(text) if re.search(r"카드", text) else None
    method = None
    for pattern, m in _METHOD_WORDS:
        if re.search(pattern, text):
            method = m
            break
    if provider and method == PaymentMethod.PAY_MONEY and not re.search(r"머니", text):
        method = None  # '카카오페이' 만 말했으면 결제수단은 제한하지 않음
    status = TransactionStatus.REFUNDED if _REFUND.search(text) else (
        TransactionStatus.CANCELED if _CANCEL.search(text) else None)
    extra = _time_filters(text)

    metric = Metric.SUM
    group_by = GroupBy.NONE
    if _COUNT.search(text):
        metric = Metric.COUNT
    elif _AVG.search(text):
        metric = Metric.AVG
        if re.search(r"하루|일\s*평균|매일|날마다", text):
            group_by = GroupBy.DAY   # 하루 평균 = 일별 합계의 평균
    elif _MAX.search(text):
        metric = Metric.MAX
    if _QTY.search(text) or (_FREQ.search(text) and not _SUM_MONEY.search(text)):
        metric = Metric.COUNT
    for pattern, g in ((_GROUP_ITEM, GroupBy.ITEM), (_GROUP_CATEGORY, GroupBy.CATEGORY),
                       (_GROUP_MERCHANT, GroupBy.MERCHANT),
                       (_GROUP_MONTH, GroupBy.MONTH), (_GROUP_WEEKDAY, GroupBy.WEEKDAY),
                       (_GROUP_DAY, GroupBy.DAY), (_GROUP_METHOD, GroupBy.METHOD)):
        if pattern.search(text):
            group_by = g
            break

    has_terms = bool(categories or items or merchants or keywords)
    is_aggregate = metric != Metric.SUM or group_by != GroupBy.NONE or bool(_SUM.search(text))
    has_condition = (period.kind != PeriodKind.NONE or provider or method or status or is_aggregate
                     or bool(extra))
    if _SMALLTALK.search(text) and not has_condition:
        intent = Intent.SMALLTALK
    elif metric == Metric.MAX:
        intent = Intent.LIST  # '제일 비싼 거' → 금액순 목록
    elif is_aggregate and not re.search(r"내역|목록|리스트|보여", text):
        intent = Intent.AGGREGATE
    elif _LIST.search(text) or has_condition:
        intent = Intent.SEARCH if (has_terms and not _LIST.search(text)) else Intent.LIST
    elif has_terms:
        intent = Intent.SEARCH
    else:
        intent = Intent.SMALLTALK

    if _RECENT_ONE.search(text) and period.kind != PeriodKind.LAST_N_DAYS:
        intent, metric, group_by = Intent.LIST, Metric.SUM, GroupBy.NONE
    if _TOP_DAY.search(text):
        intent, metric, group_by = Intent.AGGREGATE, Metric.MAX, GroupBy.DAY
    return QueryPlan.build(
        intent=intent, period=period, categories=categories, itemTerms=items, merchantTerms=merchants,
        keywords=keywords, semanticQuery=" ".join(categories + merchants + items + keywords) or None,
        provider=provider, method=method, cardCompany=card_company, status=status,
        metric=metric, groupBy=group_by,
        limit=1 if (metric == Metric.MAX and intent == Intent.LIST) or (
            _RECENT_ONE.search(text) and period.kind != PeriodKind.LAST_N_DAYS) else None,
        **extra,
    )


# ============================================================================
# LLM 기반 (질문 증강)
# ============================================================================
PLANNER_INSTRUCTION = """\
당신은 가계부 챗봇의 '질문 해석기' 입니다. 사용자의 질문을 거래 검색 조건(JSON)으로 바꿉니다.
조건은 아래 OpenSearch 거래 인덱스에서 바로 쓰이므로, 각 값을 알맞은 칸에 나눠 담아야 잘 찾아집니다.
오늘은 {today} ({weekday}) 입니다. 단, 날짜를 직접 계산하지 마십시오. 아래 규칙대로 상대 표현만 쓰십시오.

[거래 인덱스 구조]
- 거래 1건 = 영수증 1장 또는 결제앱 내역 1줄. 필드: 가맹점명(지점명 포함, 예 'GS25 역삼점'), 업종, 주소(없는 경우 많음),
  품목 목록(영수증만 있음, 결제앱 내역은 품목 없음), 결제일시, 금액, 결제수단, 간편결제사, 카드사, 상태(승인/취소/환불)
- 업종 값: {categories}
- 품목 분류 값 (저장 시 품목마다 붙음, 예 '에쎄 체인지 1mg' → 담배): {item_categories}

[이 사용자의 데이터 — 검색어를 여기 있는 실제 값에 맞추십시오]
{vocabulary}

intent:
- AGGREGATE: 금액 합계/건수/평균/어디에 많이 썼는지 등 숫자를 묻는 질문 ("얼마 썼어", "몇 번", "어디에 제일 많이")
- LIST: 거래 목록을 보여달라는 질문 ("어제 결제 내역", "카카오페이로 쓴 거 보여줘", "제일 비싼 거")
- SEARCH: 특정 가맹점/품목/업종을 찾는 질문 ("치즈케이크 산 데 어디였지", "스타벅스 간 적 있어?")
- SMALLTALK: 인사, 사용법 등 거래 조회가 필요 없는 말

period (날짜 계산 금지):
- 질문에 기간 표현이 없으면 반드시 kind=NONE (전체 기간). '오늘' 이나 '이번 달' 로 가정하지 마십시오.
- 오늘/어제/그제 → RELATIVE, unit=DAY, offset=0/-1/-2.  'N일 전' → offset=-N
- 이번 주/지난주 → RELATIVE, unit=WEEK, offset=0/-1
- 이번 달/지난달 → RELATIVE, unit=MONTH, offset=0/-1
- 올해/작년 → RELATIVE, unit=YEAR, offset=0/-1
- 최근 N일/최근 N주 → LAST_N_DAYS, days=N (주는 N*7)
- 'N월' → MONTH_OF_YEAR, month=N, year 는 말했을 때만
- 날짜를 직접 말함 ('3월 5일', '2025-03-05') → ABSOLUTE, startDate/endDate (YYYY-MM-DD, 연도 없으면 {year})

검색어 (질문에 나온 것만, 서로 다른 칸은 AND 로 검색됩니다):
- categories : 업종을 말하면 위 업종 값으로 ("편의점에서" → ["편의점"], "커피숍" → ["카페"], "밥 먹은 곳" → ["음식점"])
- merchantTerms: 가게/브랜드 이름. 사용자 가맹점 목록에 해당하는 이름이 있으면 그 이름을 그대로 넣으십시오
  ("스벅" → ["스타벅스"], "하나로" → 목록의 하나로마트 이름). 업종 단어는 여기 넣지 않습니다.
- locationTerms: 지역·동네·역 이름 ("역삼에 있는" → ["역삼"], "강남역 근처" → ["강남"]). 빠뜨리지 마십시오.
- itemTerms  : 산 물건. 첫 번째는 위 '품목 분류 값' 중 맞는 것(있으면), 이어서 동의어·대표 상품명 2~5개
  ("담배" → ["담배","에쎄","말보로","레종"], "고기" → ["고기","삼겹살","목살","등심"], "커피" → ["커피","아메리카노","라떼"],
  "술" → ["주류","소주","맥주"]). 품목은 영수증 거래에만 있습니다. "식당/고깃집/카페" 같은 가게 종류는 categories 로.
- keywords   : 위 어디에 넣을지 정말 애매한 단어만. 보통 빈 목록.
- semanticQuery: 찾는 대상을 설명하는 짧은 한국어 문장 ("역삼역 근처 편의점에서 산 물건"). 검색어가 없으면 null.
- '뭐 샀어', '얼마', '어디' 같은 질문 단어나 기간·결제수단 단어는 검색어에 넣지 않습니다.

필터:
- provider: KAKAOPAY/TOSSPAY/NAVERPAY/PAYCO/SAMSUNGPAY/APPLEPAY (간편결제를 말한 경우만)
- method: CARD/CASH/PAY_MONEY/BANK_TRANSFER/POINT (결제수단을 말한 경우만)
- cardCompany: SHINHAN/KB/SAMSUNG/HYUNDAI/LOTTE/WOORI/HANA/BC/NH/CITI (카드사를 말한 경우만)
- status: 취소/환불을 묻는 경우만 CANCELED/REFUNDED, 아니면 null
- minAmount/maxAmount: "만원 이상" → minAmount=10000, "5천원 이하" → maxAmount=5000
- weekdays: 주말 → [6,7], 평일 → [1,2,3,4,5], 금요일 → [5]. 말하지 않으면 []
- hourFrom/hourTo: 새벽 0~5, 아침 6~11, 점심 11~14, 저녁 17~21, 밤 21~2. 말하지 않으면 null
- metric: SUM(기본, 금액)/COUNT(몇 번·몇 개·몇 갑, '제일 많이 간/산/먹은/핀' 처럼 횟수·수량)/AVG(평균)/MAX(제일 비싼 거래)
- groupBy: MERCHANT(어디에/어디를 많이)/CATEGORY(업종별)/ITEM(뭘 제일 많이 샀는지·먹었는지·폈는지, 품목별)/
  DAY(일별)/MONTH(월별)/WEEKDAY(요일별)/METHOD(결제수단별)/PROVIDER/NONE
  예) "식당 어디를 제일 많이 갔지" → categories=["음식점"], groupBy=MERCHANT, metric=COUNT
      "이번 주 담배 뭐 많이 폈지" → itemTerms=["담배",…], groupBy=ITEM, metric=COUNT
      "이번 달 가장 많이 사먹은 게 뭐야" → groupBy=ITEM, metric=COUNT (검색어 없음)
      "담배에 쓴 돈 얼마" → itemTerms=["담배",…], intent=AGGREGATE, metric=SUM (품목 금액 합계로 계산됨)
      "어제 뭐 먹었지" / "지난주에 뭐 샀지" → intent=LIST, 검색어 없음 (업종을 추측해 넣지 마십시오. 편의점 간식도 포함)
      "스벅 마지막으로 간 게 언제" → merchantTerms=["스타벅스"], intent=LIST, metric=SUM, limit=1 (최신순.
      MAX 는 '제일 비싼' 일 때만)
      "제일 비싼 거" → intent=LIST, metric=MAX, limit=1, groupBy=NONE
- status 는 취소/환불을 물을 때만. 그 외에는 반드시 null (APPROVED 를 넣지 마십시오)
- limit: '제일 비싼 거 하나' 처럼 개수를 말한 경우만, 아니면 null

후속 질문("그럼 지난달은?", "거기서 카카오페이만", "그 중에 뭐 샀어?")은 이전 대화의 조건을 이어받아 바뀐 부분만 반영합니다.
"""

_WEEKDAY_NAMES = "월화수목금토일"


def describe_vocabulary(vocab: Optional[UserVocabulary]) -> str:
    if vocab is None:
        return "- (조회 실패: 일반적인 이름으로 검색어를 만드십시오)"
    if vocab.total == 0:
        return "- 아직 저장된 거래가 없습니다."
    lines = [f"- 저장된 거래 {vocab.total}건 ({vocab.firstDate} ~ {vocab.lastDate})"]
    if vocab.merchants:
        lines.append("- 가맹점: " + ", ".join(vocab.merchants))
    if vocab.categories:
        lines.append("- 업종: " + ", ".join(vocab.categories))
    if vocab.providers:
        lines.append("- 간편결제: " + ", ".join(vocab.providers))
    return "\n".join(lines)


class LlmPlanner:
    def __init__(self, guard: QuotaGuard):
        self._guard = guard
        self._cfg = settings.chat

    @property
    def model_name(self) -> str:
        return self._cfg.model_name

    def unavailable_reason(self) -> Optional[str]:
        if not settings.llm.api_key:
            return "GEMINI_API_KEY 미설정"
        return self._guard.message(self.model_name)

    async def plan(self, message: str, history: List[ChatTurn], vocab: Optional[UserVocabulary] = None) -> QueryPlan:
        from google.genai import types
        from app.services.chat.period import today

        t = today()
        turns = history[-self._cfg.history_turns:]
        contents = [
            types.Content(role="user" if h.role == "user" else "model", parts=[types.Part(text=h.content)])
            for h in turns
        ] + [types.Content(role="user", parts=[types.Part(text=message)])]
        config = types.GenerateContentConfig(
            system_instruction=PLANNER_INSTRUCTION.format(
                today=t.isoformat(), weekday=_WEEKDAY_NAMES[t.weekday()] + "요일", year=t.year,
                categories=", ".join(CATEGORY_NAMES), item_categories=", ".join(ITEM_CATEGORIES),
                vocabulary=describe_vocabulary(vocab)),
            temperature=0.0,
            response_mime_type="application/json",
            response_schema=QueryPlan,
            thinking_config=types.ThinkingConfig(thinking_budget=0),
        )
        client = get_client(settings.llm.api_key)
        response = await call_with_retry(
            lambda: client.aio.models.generate_content(model=self.model_name, contents=contents, config=config),
            guard=self._guard, model_name=self.model_name, timeout=self._cfg.timeout_sec,
        )
        plan = QueryPlan.model_validate_json(response.text)
        logger.info(f"LLM 플랜: {json.dumps(plan.model_dump(mode='json'), ensure_ascii=False)}")
        return plan


def _clean(words: List[str], limit: int = 8) -> List[str]:
    out: List[str] = []
    for w in words:
        w = (w or "").strip()
        if w and w not in out:
            out.append(w)
    return out[:limit]


def sanitize_llm_plan(plan: QueryPlan, message: str, history: List[ChatTurn]) -> QueryPlan:
    """
    LLM 플랜 교차 검증
    - 첫 질문인데 질문에 기간 표현이 전혀 없으면 LLM 이 붙인 기간은 버린다
      (실제로 '스타벅스 몇 번 갔어?' 를 '오늘' 로 해석한 사례). 후속 질문은 이전 조건 상속이 정상이므로 제외.
    - 질문에 명시된 필터(결제수단/간편결제/카드사/취소)를 LLM 이 빠뜨리면 규칙으로 보충
      (실제 사례: '그 중에 카카오페이머니로 낸 건?' 에서 method 누락)
    - 업종은 정해진 값으로 정규화, 가맹점 칸에 들어간 업종 단어는 업종으로 이동
    - 검색어를 통째로 빠뜨렸으면 규칙 해석의 검색어로 보충
    """
    updates: dict = {}
    if not history and plan.period.kind != PeriodKind.NONE and _period_from_text(message).kind == PeriodKind.NONE:
        logger.info(f"LLM 이 질문에 없는 기간({plan.period.kind.value})을 추가 → 전체 기간으로 보정")
        updates["period"] = Period.none()

    rule = rule_plan(message)
    for field in ("provider", "method", "cardCompany", "status", "minAmount", "maxAmount", "hourFrom", "hourTo"):
        if getattr(plan, field) is None and getattr(rule, field) is not None:
            logger.info(f"LLM 플랜에 '{field}' 누락 → 규칙값 보충")
            updates[field] = getattr(rule, field)
    if not plan.weekdays and rule.weekdays:
        updates["weekdays"] = rule.weekdays
    # 질문에 분명한 표현이 있는데 LLM 이 놓친 집계 방식 보충 ('몇 갑', '제일 많이 간', '뭐 제일 많이')
    if plan.groupBy == GroupBy.NONE and rule.groupBy != GroupBy.NONE:
        updates["groupBy"] = rule.groupBy
    # 품목별 순위는 '뭘 제일 많이' 같은 표현이 있을 때만 (실측: '제일 비싼 거' 를 품목 순위로 해석)
    if plan.groupBy == GroupBy.ITEM and rule.groupBy != GroupBy.ITEM:
        logger.info("질문에 품목 순위 표현 없음 → groupBy ITEM 제거")
        updates["groupBy"] = rule.groupBy
    if plan.metric == Metric.SUM and rule.metric == Metric.COUNT:
        updates["metric"] = Metric.COUNT
    # 금액을 묻는데 횟수로 해석 (실측: '지난주에 제일 많이 쓴 곳' → 방문 횟수 순위)
    if plan.metric == Metric.COUNT and _SUM_MONEY.search(message) and not (_COUNT.search(message) or _QTY.search(message)):
        logger.info("금액 표현(얼마/돈/쓴) → metric COUNT 를 SUM 으로")
        updates["metric"] = Metric.SUM
    # '제일 비싼' 이 아닌데 MAX(금액순) → 날짜순 (실측: '스벅 마지막으로 간 게 언제' 를 금액순 1건으로)
    if plan.metric == Metric.MAX and not _MAX.search(message) and not re.search(r"비싼|큰\s*돈|많이\s*쓴", message):
        logger.info("질문에 '제일 비싼' 표현 없음 → metric MAX 제거 (최신순)")
        updates["metric"] = Metric.SUM
    group = updates.get("groupBy", plan.groupBy)
    if group != GroupBy.NONE and plan.intent in (Intent.LIST, Intent.SEARCH) and rule.intent == Intent.AGGREGATE:
        updates["intent"] = Intent.AGGREGATE
    # 후속 질문: 직전 질문이 합계였고 이번에 '보여줘' 같은 목록 표현이 없으면 합계로 이어감
    # (실측: '이번 달 얼마 썼어?' → '그 중에 편의점은?' 을 목록으로 해석)
    prev_user = next((h.content for h in reversed(history) if h.role == "user"), None)
    if prev_user and plan.intent in (Intent.LIST, Intent.SEARCH) and not _LIST_EXPLICIT.search(message) \
            and rule_plan(prev_user).intent == Intent.AGGREGATE:
        logger.info("후속 질문 → 직전 질문의 합계 의도 유지")
        updates["intent"] = Intent.AGGREGATE
    # 금액·횟수를 묻는데 목록으로 해석 (실측: '새우깡 얼마였지' → 영수증 전체 금액으로 답함)
    if plan.intent in (Intent.LIST, Intent.SEARCH) and rule.intent == Intent.AGGREGATE \
            and not _LIST_EXPLICIT.search(message):
        updates["intent"] = Intent.AGGREGATE
    # '가장 최근 거래', '마지막으로 간 게 언제' → 최신순 1건 (위 보정보다 우선)
    if _RECENT_ONE.search(message) and plan.period.kind != PeriodKind.LAST_N_DAYS:
        updates.update(intent=Intent.LIST, metric=Metric.SUM, groupBy=GroupBy.NONE, limit=1)
    # '그 날', '그때' → 이전 대화에 마지막으로 나온 날짜 (실측: 이전 답변에 '11월 8일' 이 있는데 '어제' 로 해석)
    if re.search(r"그\s*날|그때|그\s*당시|그\s*날짜", message) and _period_from_text(message).kind == PeriodKind.NONE:
        for turn in reversed(history):
            ref = _period_from_text(turn.content)
            if ref.kind == PeriodKind.ABSOLUTE:
                logger.info(f"'그 날' → 이전 대화의 날짜 {ref.startDate}")
                updates["period"] = ref
                break
    # 질문하지 않은 '승인 건만' 필터는 제거 (취소 건도 목록에는 보여야 함, 합계는 저장소가 승인 건만 집계)
    if plan.status == TransactionStatus.APPROVED and "승인" not in message:
        updates["status"] = None

    categories: List[str] = []
    keywords = list(plan.keywords)
    for c in plan.categories:
        cat = to_category(c)
        if cat:
            categories.append(cat)
        else:
            keywords.append(c)  # 정해진 업종이 아니면 일반 검색어로
    merchants = []
    for m in plan.merchantTerms:
        cat = to_category(m)
        if cat:
            categories.append(cat)
        else:
            merchants.append(m)
    no_noise = lambda ws: [w for w in ws if not is_noise_term(w)]  # noqa: E731  ('얼마씩', '늦게' 등)
    # 특정 상품을 물었으면 분류 전체로 넓히지 않음 (실측: '에쎄 몇 갑' → 담배·말보로·레종까지 세서 7갑)
    text = message.replace(" ", "")
    asked = [t for t in plan.itemTerms if t.replace(" ", "") in text]
    if asked and all(not to_item_category(t) for t in asked):
        if len(asked) < len(plan.itemTerms):
            logger.info(f"특정 상품 질문 → 품목 확장 제거 {plan.itemTerms} → {asked}")
        plan = plan.model_copy(update={"itemTerms": asked})
    updates.update(categories=_clean(categories), merchantTerms=_clean(no_noise(merchants)),
                   itemTerms=_clean(plan.itemTerms), locationTerms=_clean(no_noise(plan.locationTerms)),
                   keywords=_clean(no_noise(keywords)), weekdays=[d for d in updates.get("weekdays", plan.weekdays) if 1 <= d <= 7])
    for field in ("hourFrom", "hourTo"):
        v = updates.get(field, getattr(plan, field))
        if v is not None and not 0 <= v <= 23:
            updates[field] = None

    merged = plan.model_copy(update=updates)
    if not merged.search_terms() and rule.search_terms() and merged.intent != Intent.SMALLTALK and not history:
        logger.info(f"LLM 플랜에 검색어 없음 → 규칙 검색어 보충 {rule.search_terms()}")
        merged = merged.model_copy(update={"categories": rule.categories, "keywords": rule.keywords,
                                           "semanticQuery": merged.semanticQuery or rule.semanticQuery})
    return merged


_FOLLOWUP = re.compile(r"^\s*(그\s*중|거기|그럼|그거|그건|그\s*날|그때|그리고|또|그\s*가게|거기서)|^\s*\S{1,8}(은|는)\s*\??\s*$")


def rule_plan_followup(message: str, history: List[ChatTurn]) -> QueryPlan:
    """
    규칙 해석 + 이전 질문 조건 이어받기 (LLM 을 못 쓸 때 후속 질문 대응)
    '그 중에 편의점은?', '거기서 뭐 먹었어?', '그럼 이번 주는?', '말보로는?'
    """
    plan = rule_plan(message)
    prev = next((h.content for h in reversed(history) if h.role == "user"), None)
    if not prev or not _FOLLOWUP.search(message):
        return plan
    base = rule_plan(prev)
    updates: dict = {}
    if plan.period.kind == PeriodKind.NONE:
        updates["period"] = base.period
        if re.search(r"그\s*날|그때", message):
            for turn in reversed(history):
                ref = _period_from_text(turn.content)
                if ref.kind == PeriodKind.ABSOLUTE:
                    updates["period"] = ref
                    break
    if not plan.search_terms():
        updates.update({k: getattr(base, k) for k in ("categories", "itemTerms", "merchantTerms", "locationTerms",
                                                      "keywords", "semanticQuery")})
    for f in ("provider", "method", "cardCompany", "status"):
        if getattr(plan, f) is None and getattr(base, f) is not None:
            updates[f] = getattr(base, f)
    explicit_list = _LIST_EXPLICIT.search(message)
    if plan.intent == Intent.SMALLTALK or (not explicit_list and base.intent == Intent.AGGREGATE):
        updates.update(intent=base.intent, metric=base.metric if plan.metric == Metric.SUM else plan.metric)
    return plan.model_copy(update=updates)


async def make_plan(
    message: str, history: List[ChatTurn], llm: LlmPlanner, vocab: Optional[UserVocabulary] = None,
) -> Tuple[QueryPlan, str, Optional[str]]:
    """(plan, 사용한 플래너 이름, 안내 문구)"""
    mode = settings.chat.planner
    fallback = lambda: rule_plan_followup(message, history)  # noqa: E731
    if mode == "rule":
        return fallback(), "rule", None
    reason = llm.unavailable_reason()
    if reason:
        if mode == "llm":
            logger.warning(f"LLM 플래너 사용 불가 → 규칙 사용: {reason}")
        return fallback(), "rule", reason
    try:
        plan = await llm.plan(message, history, vocab)
    except QuotaExceededError as e:
        return fallback(), "rule", str(e)  # 할당량/결제 문제는 원인을 그대로 안내
    except Exception as e:  # noqa: BLE001
        logger.warning(f"LLM 플래너 실패 → 규칙 사용: {e}")
        return fallback(), "rule", f"질문 해석 LLM 실패로 규칙 기반 사용 ({type(e).__name__})"
    return sanitize_llm_plan(plan, message, history), f"llm:{llm.model_name}", None
