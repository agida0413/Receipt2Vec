"""
챗봇 정확도 평가 (실제 rag_backend + OpenSearch + Gemini 대상)

- 평가용 사용자(eval)의 거래 데이터를 '오늘' 기준 상대 날짜로 만들어 넣고
- 질문별 기대값(합계·이름·날짜)을 같은 데이터에서 코드로 계산해 답변을 채점한다.

실행 (rag_backend 이미지에서, 호스트 네트워크로 localhost:3001 / 9200 접근)
  MSYS_NO_PATHCONV=1 docker run --rm --network host -v "<rag_backend>:/app" -w /app \
      --entrypoint python rag-base-backend:latest tests/eval/chat_eval.py [--reset] [--only 5,6] [-v]
"""
import argparse
import json
import sys
import time
import urllib.request
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Callable, List, Optional, Tuple
from zoneinfo import ZoneInfo

RAG = __import__("os").environ.get("EVAL_RAG_URL", "http://localhost:3001")
OS = "http://localhost:9200"
USER = "eval"
TODAY = datetime.now(ZoneInfo("Asia/Seoul")).date()


# ============================================================================
# 데이터: (며칠 전, 시각, 가맹점, [(품목, 수량, 단가, 분류)], 결제수단, 간편결제, 카드사, 상태, 품목 없을 때 금액)
# ============================================================================
R = [
    (0, "09:00", "메가커피 역삼점", [("카페라떼", 1, 3200, "커피")], "CARD", None, "KB", "APPROVED", None),
    (1, "22:30", "세븐일레븐 역삼점", [("에쎄 체인지 1mg", 2, 4500, "담배"), ("새우깡", 1, 1500, "과자")],
     "CARD", None, "SHINHAN", "APPROVED", None),
    (1, "15:20", "쿠팡", [], "PAY_MONEY", "KAKAOPAY", None, "APPROVED", 25900),
    (2, "20:15", "하남돼지집 역삼점", [("삼겹살 2인분", 1, 32000, "고기"), ("된장찌개", 1, 3000, "식사"),
                                ("소주", 1, 5000, "주류")], "CARD", None, "SHINHAN", "APPROVED", None),
    (2, "13:00", "스타벅스 역삼점", [("아이스 아메리카노", 1, 4700, "커피")], "PAY_MONEY", "KAKAOPAY", None, "APPROVED", None),
    (3, "19:40", "CU 선릉역점", [("말보로 레드", 1, 4500, "담배"), ("카스 500ml", 1, 2900, "주류")],
     "CARD", None, "KB", "APPROVED", None),
    (3, "12:30", "김밥천국 역삼점", [("라볶이", 1, 6000, "라면"), ("참치김밥", 1, 4000, "분식")], "CASH", None, None, "APPROVED", None),
    (4, "08:10", "GS25 역삼점", [("에쎄 체인지 1mg", 1, 4500, "담배"), ("삼각김밥", 1, 1300, "분식")],
     "CARD", None, "KB", "APPROVED", None),
    (5, "15:00", "CGV 용산", [("영화 관람권", 1, 15000, "영화")], "CARD", None, "KB", "APPROVED", None),
    (5, "11:00", "이마트 성수점", [("삼겹살 600g", 1, 15800, "고기"), ("양파", 1, 2980, "채소"), ("우유 1L", 1, 2850, "음료")],
     "CARD", None, "KB", "APPROVED", None),
    (6, "19:30", "교촌치킨 역삼점", [("허니콤보", 1, 23000, "치킨")], "PAY_MONEY", "KAKAOPAY", None, "APPROVED", None),
    (7, "21:00", "GS25 역삼점", [("말보로 레드", 1, 4500, "담배"), ("레종 블루", 1, 4500, "담배")],
     "CARD", None, "KB", "APPROVED", None),
    (8, "12:20", "김밥천국 역삼점", [("제육덮밥", 1, 7500, "식사")], "CASH", None, None, "APPROVED", None),
    (9, "19:00", "본가 한우 강남점", [("한우 등심 1인분", 1, 45000, "고기"), ("냉면", 1, 9000, "식사")],
     "CARD", None, "HYUNDAI", "APPROVED", None),
    (10, "12:10", "김밥천국 역삼점", [("돈까스", 1, 8000, "식사")], "CARD", None, "KB", "APPROVED", None),
    (12, "12:00", "김밥천국 역삼점", [("라면", 1, 4500, "라면"), ("김밥", 1, 3000, "분식")], "CARD", None, "KB", "APPROVED", None),
    (14, "10:00", "이디야커피 강남점", [("카페라떼", 1, 3500, "커피")], "CARD", None, "KB", "APPROVED", None),
    (19, "20:00", "하남돼지집 역삼점", [("목살 2인분", 1, 30000, "고기"), ("공기밥", 1, 1000, "식사")],
     "CARD", None, "SHINHAN", "APPROVED", None),
    (22, "18:00", "온누리약국 역삼점", [("타이레놀 500mg", 1, 3500, "의약품")], "CARD", None, "KB", "APPROVED", None),
    (24, "23:40", "카카오T 택시", [], "PAY_MONEY", "KAKAOPAY", None, "APPROVED", 12300),
    (29, "21:30", "GS25 역삼점", [("에쎄 체인지 1mg", 1, 4500, "담배"), ("카스 500ml", 2, 2900, "주류")],
     "CARD", None, "KB", "APPROVED", None),
    (34, "14:00", "무신사", [], "CARD", None, "KB", "CANCELED", 59000),
    (40, "13:00", "스타벅스 강남R점", [("카페라떼", 1, 5000, "커피")], "CARD", None, "KB", "APPROVED", None),
]


@dataclass
class Tx:
    d: date
    hour: int
    merchant: str
    items: List[Tuple[str, int, int, str]]
    method: str
    provider: Optional[str]
    card: Optional[str]
    status: str
    total: int


def build() -> List[Tx]:
    out = []
    for ago, hm, merchant, items, method, provider, card, status, fixed in R:
        total = fixed if fixed is not None else sum(q * p for _, q, p, _ in items)
        out.append(Tx(TODAY - timedelta(days=ago), int(hm[:2]), merchant, items, method, provider, card, status, total))
    return out


DATA = build()


def to_doc(i: int, t: Tx, raw: Tuple) -> dict:
    hm = raw[1]
    tid = str(uuid.uuid5(uuid.NAMESPACE_URL, f"eval-{i}"))
    return {
        "schemaVersion": 1, "transactionId": tid, "userId": USER,
        "source": {"type": "RECEIPT" if t.items else "PAYMENT_APP", "provider": t.provider, "documentId": tid},
        "merchant": {"name": t.merchant},
        "transactedAt": f"{t.d.isoformat()}T{hm}:00+09:00", "transactedDate": t.d.isoformat(),
        "status": t.status, "amount": {"total": t.total, "currency": "KRW"},
        "payment": {"method": t.method, "provider": t.provider, "cardCompany": t.card},
        "items": [{"name": n, "quantity": q, "unitPrice": p, "amount": q * p} for n, q, p, _ in t.items],
        "quality": {"needsReview": False, "confirmed": True}, "extra": {},
        "raw": {"text": "", "parserName": "eval", "parserVersion": "1"},
    }


# ============================================================================
# 기대값 계산
# ============================================================================
def monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


THIS_WEEK = (monday(TODAY), monday(TODAY) + timedelta(days=7))
LAST_WEEK = (monday(TODAY) - timedelta(days=7), monday(TODAY))
THIS_MONTH = (TODAY.replace(day=1), (TODAY.replace(day=28) + timedelta(days=4)).replace(day=1))
LAST_MONTH = ((TODAY.replace(day=1) - timedelta(days=1)).replace(day=1), TODAY.replace(day=1))
YESTERDAY = (TODAY - timedelta(days=1), TODAY)
ALL = (date(2000, 1, 1), date(2100, 1, 1))


def txs(rng=ALL, pred: Callable[[Tx], bool] = lambda t: True, approved=True) -> List[Tx]:
    return [t for t in DATA if rng[0] <= t.d < rng[1] and pred(t) and (not approved or t.status == "APPROVED")]


def total(rng=ALL, pred=lambda t: True) -> int:
    return sum(t.total for t in txs(rng, pred))


def item_total(tag: str, rng=ALL) -> int:
    return sum(q * p for t in txs(rng) for _, q, p, c in t.items if c == tag)


def item_qty(tag: str, rng=ALL) -> int:
    return sum(q for t in txs(rng) for _, q, _, c in t.items if c == tag)


def won(n: int) -> str:
    return f"{n:,}"


def md(d: date) -> str:
    return f"{d.month}월 {d.day}일"


def first(merchant_part: str) -> Tx:
    return sorted([t for t in DATA if merchant_part in t.merchant], key=lambda t: t.d, reverse=True)[0]


CONV = ("GS25", "CU", "세븐일레븐")
WEEKEND = lambda t: t.d.isoweekday() >= 6  # noqa: E731


# ============================================================================
# 질문 · 채점 기준
#   all_of: 모두 포함, any_of: 하나 이상 포함, none_of: 포함되면 실패, history: 이전 대화
# ============================================================================
@dataclass
class Case:
    q: str
    all_of: List[str] = field(default_factory=list)
    any_of: List[str] = field(default_factory=list)
    none_of: List[str] = field(default_factory=list)
    history: List[Tuple[str, str]] = field(default_factory=list)
    note: str = ""


avg_month = round(total(THIS_MONTH) / len(txs(THIS_MONTH)))
CASES: List[Case] = [
    # 기간 합계
    Case("이번 주 얼마 썼어?", [won(total(THIS_WEEK))]),
    Case("지난주 지출 총액 알려줘", [won(total(LAST_WEEK))]),
    Case("이번 달 얼마 썼어?", [won(total(THIS_MONTH))]),
    Case("지난달 얼마 썼어?", [won(total(LAST_MONTH))], note="취소 거래 제외"),
    Case("이번 달 평균 얼마씩 썼어?", [won(avg_month)]),
    # 품목
    Case("저번주에 먹었던 고기 이름이 뭐지?", ["등심"], note="지난주 고기 품목: 한우 등심, 삼겹살(마트)"),
    Case("이번주에 담배 쓴 돈이 총 얼마야?", [won(item_total("담배", THIS_WEEK))], none_of=[won(total(THIS_WEEK, lambda t: t.merchant.startswith(CONV)))],
         note="편의점 결제 전체가 아니라 담배 품목만"),
    Case("이번주 담배 뭐 많이 폈지?", ["에쎄"]),
    Case("이번달 가장 많이 사먹은게 뭐야?", ["에쎄"], note="수량 기준 1위"),
    Case("이번 달 담배값 얼마 나왔어?", [won(item_total("담배", THIS_MONTH))]),
    Case("담배 몇 갑 샀어?", [f"{item_qty('담배')}"]),
    Case("술 마신 적 있어?", any_of=["소주", "카스"]),
    Case("이번 주 커피에 쓴 돈 얼마야?", [won(item_total("커피", THIS_WEEK))]),
    Case("커피 몇 번 마셨어?", [f"{len(txs(ALL, lambda t: any(c == '커피' for *_, c in t.items)))}"]),
    Case("삼겹살 언제 먹었어?", [md(first("하남돼지집").d)]),
    Case("치킨 시켜먹은 적 있어?", ["교촌"]),
    Case("라면 산 적 있어?", ["김밥천국"], any_of=["라면", "라볶이"]),
    Case("마트에서 뭐 샀어?", any_of=["양파", "우유", "삼겹살"]),
    Case("약국에서 뭐 샀어?", ["타이레놀"]),
    Case("우유 산 적 있어?", ["이마트"]),
    Case("맥주 얼마나 마셨어?", ["카스"]),
    Case("어제 뭐 먹었지?", ["새우깡"]),
    Case("오늘 커피 마셨나?", ["메가커피"]),
    # 가맹점·업종
    Case("식당 어디를 제일 많이 갔지?", ["김밥천국"], any_of=[f"{len(txs(ALL, lambda t: '김밥천국' in t.merchant))}번",
                                                    f"{len(txs(ALL, lambda t: '김밥천국' in t.merchant))}회"]),
    Case("김밥천국 몇 번 갔어?", [f"{len(txs(ALL, lambda t: '김밥천국' in t.merchant))}"]),
    Case("편의점에서 이번 달 얼마 썼어?", [won(total(THIS_MONTH, lambda t: t.merchant.startswith(CONV)))]),
    Case("이번 주 편의점 몇 번 갔어?", [f"{len(txs(THIS_WEEK, lambda t: t.merchant.startswith(CONV)))}"]),
    Case("스타벅스에서 얼마 썼어?", [won(total(ALL, lambda t: '스타벅스' in t.merchant))]),
    Case("스벅 마지막으로 간 게 언제야?", [md(first("스타벅스").d)]),
    Case("담배 제일 많이 산 곳 어디야?", ["GS25"]),
    Case("영화 본 적 있어?", ["CGV"]),
    Case("택시비 얼마 나왔어?", ["12,300"]),
    Case("역삼에서 밥 먹은 곳 어디야?", any_of=["김밥천국", "하남돼지집"]),
    Case("이번 달 업종별 지출 알려줘", ["음식점", "편의점"]),
    # 필터
    Case("카카오페이로 결제한 거 보여줘", ["쿠팡", "교촌"]),
    Case("신한카드로 얼마 썼어?", [won(total(ALL, lambda t: t.card == "SHINHAN"))]),
    Case("3만원 이상 쓴 거 있어?", ["하남돼지집", "본가"]),
    Case("주말에 쓴 돈 얼마야?", [won(total(ALL, WEEKEND))]),
    Case("밤 늦게 결제한 거 있어?", ["세븐일레븐"]),
    Case("제일 비싼 거 뭐였어?", ["본가"], none_of=["무신사"], note="취소 거래 제외"),
    Case("요일별로 얼마 썼어?", ["요일"]),
    Case("취소된 거래 있어?", ["무신사"]),
    Case("지난주 저녁에 먹은 거 뭐야?", any_of=["교촌", "본가", "허니콤보", "등심"]),
    Case("11월에 고기 먹은 데 어디야?" if TODAY.month == 11 else "이번 달 고기 먹은 데 어디야?", any_of=["하남돼지집", "본가"]),
    # 후속 질문
    Case("그 중에 편의점은?", [won(total(THIS_MONTH, lambda t: t.merchant.startswith(CONV)))],
         history=[("이번 달 얼마 썼어?", f"이번 달 총 {won(total(THIS_MONTH))}원을 썼어요.")]),
    Case("거기서 뭐 먹었어?", any_of=["라볶이", "제육덮밥", "돈까스", "라면"],
         history=[("김밥천국 몇 번 갔어?", "김밥천국에 4번 가셨어요.")]),
    Case("그럼 이번 주는?", [won(total(THIS_WEEK))],
         history=[("지난주 얼마 썼어?", f"지난주에는 {won(total(LAST_WEEK))}원을 썼어요.")]),
    # 없는 것 / 애매한 것
    Case("헬스장 등록했어?", any_of=["없"], none_of=["헬스장에서"]),
    Case("작년에 쓴 돈 얼마야?", any_of=["없", "0원"]),
    Case("고깃집에서 쓴 돈 얼마야?", [won(total(ALL, lambda t: '하남돼지집' in t.merchant or '한우' in t.merchant))],
         note="애매: 고깃집 = 하남돼지집·본가 한우"),
] + [
    # ---- 2차: 1차에 맞춰 고치지 않은 새 표현 (일반화 확인용) ----
    Case("요즘 담배 너무 많이 피는 것 같은데 이번 달 담배 몇 갑이야?", [f"{item_qty('담배', THIS_MONTH)}"]),
    Case("고기 먹으러 간 날 언제언제야?", [md(first("하남돼지집").d), md(first("본가").d)]),
    Case("지난주에 제일 많이 쓴 곳 어디야?", ["본가"]),
    Case("이번 주 점심값 얼마 썼어?", any_of=[won(total(THIS_WEEK, lambda t: 11 <= t.hour <= 14)),
                                         won(total(THIS_WEEK, lambda t: 11 <= t.hour <= 14 and '김밥' in t.merchant))],
         note="애매: 카페를 점심값에 넣을지 (둘 다 인정)"),
    Case("카페라떼 몇 잔 마셨어?", [f"{sum(q for t in txs() for n, q, _, _ in t.items if n == '카페라떼')}"]),
    Case("이디야 간 적 있어?", ["이디야"]),
    Case("편의점에서 담배 말고 뭐 샀어?", any_of=["새우깡", "삼각김밥", "카스"]),
    Case("지난달 커피값 얼마야?", [won(item_total("커피", LAST_MONTH))]),
    Case("현금으로 낸 거 있어?", ["김밥천국"]),
    Case("현대카드 사용 내역 보여줘", ["본가"]),
    Case("국민카드로 얼마 썼어?", [won(total(ALL, lambda t: t.card == "KB"))]),
    Case("쿠팡에서 뭐 샀어?", any_of=["품목 정보", "품목은", "알 수 없", "없"]),
    Case("가장 최근 거래 뭐야?", ["메가커피"]),
    Case(f"{LAST_MONTH[0].month}월에 쓴 돈 얼마야?", [won(total(LAST_MONTH))]),
    Case(f"{md(TODAY - timedelta(days=10))}에 뭐 샀어?", ["돈까스"]),
    Case("평일 점심 주로 어디서 먹어?", ["김밥천국"]),
    Case("소주 마신 날이 언제야?", [md(first("하남돼지집").d)]),
    Case("새우깡 얼마였지?", ["1,500"]),
    Case("이번 주에 술값 얼마 나왔어?", [won(item_total("주류", THIS_WEEK))]),
    Case("배달 시켜먹은 거 있어?", any_of=["교촌", "없"], note="애매: 배달 여부는 데이터에 없음 (치킨집 또는 없음 인정)"),
    Case("병원 간 적 있어?", any_of=["없"], none_of=["병원에서"]),
    Case("이번 달 제일 많이 쓴 날은 언제야?",
         [md(max({t.d for t in txs(THIS_MONTH)}, key=lambda d: total((d, d + timedelta(days=1)))))]),
    Case("에쎄 몇 갑 샀어?", [f"{sum(q for t in txs() for n, q, _, _ in t.items if '에쎄' in n)}"]),
    Case("말보로는?", [f"{sum(q for t in txs() for n, q, _, _ in t.items if '말보로' in n)}"],
         history=[("에쎄 몇 갑 샀어?", "에쎄는 총 4갑 샀어요.")]),
    Case("그거 어디서 샀어?", any_of=["CU", "GS25"],
         history=[("말보로 몇 갑 샀어?", "말보로는 총 2갑 샀어요.")]),
    Case("지난주랑 비교해서 이번 주 지출 어때?", any_of=[won(total(THIS_WEEK)), won(total(LAST_WEEK))]),
    Case("2만원 넘게 쓴 식당 어디야?", any_of=["하남돼지집", "본가"]),
    Case("커피 마시러 제일 자주 간 카페는?", ["스타벅스"]),
    Case("편의점 몇 번 갔어?", [f"{len(txs(ALL, lambda t: t.merchant.startswith(CONV)))}"]),
    Case("토요일에 뭐 했어?", ["교촌"]),
] + [
    # ---- 3차: 위 수정 이후 처음 보는 질문 (구어체·오타·복합 조건) ----
    Case("이번주 편의점에서 담배값만 얼마야", [won(item_total("담배", THIS_WEEK))]),
    Case("한우 먹은 데가 어디였더라", ["본가"]),
    Case("요번달 스타벅스 몇번 감?", [f"{len(txs(THIS_MONTH, lambda t: '스타벅스' in t.merchant))}"]),
    Case("지난주 주말에 뭐 했지", any_of=["CGV", "이마트", "교촌"]),
    Case("카스 몇캔 마심?", [f"{sum(q for t in txs() for n, q, _, _ in t.items if '카스' in n)}"]),
    Case("레종 산 적 있나", ["GS25"]),
    Case("11시 전에 결제한 거", any_of=["메가커피", "GS25"]),
    Case("이번달 카페에 얼마 씀", [won(total(THIS_MONTH, lambda t: any(k in t.merchant for k in ("스타벅스", "메가커피", "이디야"))))]),
    Case("돼지고기 먹은 날", [md(first("하남돼지집").d)]),
    Case("지난달에 약 산 거 얼마였어", ["3,500"]),
    Case("카카오페이 이번 주 사용액", [won(total(THIS_WEEK, lambda t: t.provider == "KAKAOPAY"))]),
    Case("만원 이하로 쓴 거 몇 건이야", [f"{len(txs(ALL, lambda t: t.total <= 10000))}"]),
    Case("영화비 얼마 썼어", ["15,000"]),
    Case("치킨 얼마짜리였어?", ["23,000"]),
    Case("이번 주 하루에 평균 얼마 썼어?", any_of=[won(round(total(THIS_WEEK) / len({t.d for t in txs(THIS_WEEK)}))),
                                            won(round(total(THIS_WEEK) / len(txs(THIS_WEEK))))],
         note="애매: 하루 평균 vs 건당 평균"),
    Case("우리 동네 김밥집 이름 뭐였지", ["김밥천국"]),
    Case("최근에 고기 언제 먹었어?", [md(first("하남돼지집").d)]),
    Case("과자 산 거 보여줘", ["새우깡"]),
    Case("교보문고 간 적 있어?", any_of=["없"], none_of=["교보문고에서 결제"]),
    Case("그 날 또 뭐 샀어?", any_of=["양파", "우유", "영화"],
         history=[("이마트에서 뭐 샀어?", f"{md(first('이마트').d)} 이마트 성수점에서 삼겹살 600g, 양파, 우유 1L 를 샀어요.")]),
    Case("이번 달 식비 얼마야?", [won(total(THIS_MONTH, lambda t: any(k in t.merchant for k in ("김밥천국", "하남돼지집", "본가", "교촌"))))],
         note="애매: 식비 = 음식점 업종"),
]


# ============================================================================
# 실행
# ============================================================================
def http(method: str, url: str, body=None):
    data = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
    r = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(r, timeout=120) as f:
        return f.read().decode()


def reset():
    http("POST", f"{OS}/receipt-transactions/_delete_by_query?refresh=true",
         {"query": {"term": {"userId": USER}}})
    docs = [to_doc(i, t, R[i]) for i, t in enumerate(DATA)]
    res = json.loads(http("POST", f"{RAG}/api/transactions", {"userId": USER, "transactions": docs}))
    print(f"평가 데이터 적재: {len(res['saved'])}건 (중복 {len(res['duplicates'])})")


def ask(q: str, history: List[Tuple[str, str]]):
    hist = [x for u, a in history for x in ({"role": "user", "content": u}, {"role": "assistant", "content": a})]
    raw = http("POST", f"{RAG}/api/chat/stream", {"userId": USER, "message": q, "history": hist})
    ev = []
    for block in raw.split("\n\n"):
        lines = block.strip().split("\n")
        if len(lines) >= 2 and lines[0].startswith("event: "):
            ev.append((lines[0][7:], json.loads(lines[1][6:])))
    answer = "".join(d["text"] for e, d in ev if e == "token")
    plan = next((d for e, d in ev if e == "plan"), {})
    result = next((d for e, d in ev if e == "result"), {})
    done = next((d for e, d in ev if e == "done"), {})
    notices = [d["message"] for e, d in ev if e in ("notice", "error")]
    return answer, plan, result, done, notices


def grade(c: Case, answer: str) -> List[str]:
    text = answer.replace(" ", "")
    norm = lambda s: s.replace(" ", "")  # noqa: E731
    errs = [f"없음:{s}" for s in c.all_of if norm(s) not in text]
    if c.any_of and not any(norm(s) in text for s in c.any_of):
        errs.append(f"하나도없음:{c.any_of}")
    errs += [f"있으면안됨:{s}" for s in c.none_of if norm(s) in text]
    return errs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--reset", action="store_true")
    ap.add_argument("--only", default="")
    ap.add_argument("-v", action="store_true")
    args = ap.parse_args()
    if args.reset:
        reset()
    only = {int(x) for x in args.only.split(",") if x}
    passed, failed = 0, []
    for i, c in enumerate(CASES, 1):
        if only and i not in only:
            continue
        t0 = time.time()
        try:
            answer, plan, result, done, notices = ask(c.q, c.history)
        except Exception as e:  # noqa: BLE001
            answer, plan, result, done, notices = f"(요청 실패 {e})", {}, {}, {}, []
        errs = grade(c, answer)
        ok = not errs
        passed += ok
        p = plan.get("plan") or {}
        brief = {k: v for k, v in p.items() if v not in (None, [], "NONE", "SUM") and k not in ("period", "semanticQuery")}
        print(f"{'✅' if ok else '❌'} {i:2d}. {c.q}  [{time.time() - t0:.1f}s, {done.get('planner', '?')}]")
        if not ok or args.v:
            print(f"     plan : {json.dumps(brief, ensure_ascii=False)} range={(plan.get('range') or {}).get('label')}")
            print(f"     result: total={result.get('total')} match={result.get('matchType')} "
                  f"agg={result.get('aggregates')} item={result.get('itemAggregates')} groups={[g.get('key') for g in result.get('groups', [])][:3]}")
            print(f"     answer: {answer.strip()[:300]!r}")
            if errs:
                print(f"     채점 : {errs} {('(' + c.note + ')') if c.note else ''}")
            if notices:
                print(f"     notice: {notices}")
            if not ok:
                failed.append(i)
    n = passed + len(failed)
    print(f"\n정확도 {passed}/{n} = {passed / max(n, 1):.0%}  실패: {failed}")


if __name__ == "__main__":
    sys.exit(main())
