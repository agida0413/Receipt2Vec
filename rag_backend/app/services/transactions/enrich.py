"""
저장 시 파생 필드 생성 (검색을 유연하게 만들기 위한 색인 보강)

OCR 이 넘겨주는 거래에는 '가맹점명 원문' 만 있어서 아래 질문들이 검색되지 않았다.
  - "편의점에서 …"     → 가맹점명에 '편의점' 이 없음        → merchant.category (업종)
  - "하나로마트 몇 번" → 'Q서서울농협하나로마트사직점'       → merchant.brand + 부분일치(ngram)
  - "가게별 합계"      → 'Q서서울…' / '서서울…' 이 따로 집계 → merchant.normalizedName
  - "주말에/밤에 …"    → 날짜만 있음                         → dayOfWeek / transactedHour
  - "커피 마신 곳"     → 단어가 정확히 일치해야만 찾음       → embedding (의미 검색)

업종/브랜드 사전은 시작점이다. 사전을 고친 뒤 POST /api/transactions/reenrich 로 기존 거래에 다시 적용한다.
"""
import re
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Tuple

# 업종 → (브랜드 목록, 가맹점명에 들어가면 해당 업종으로 보는 단어)
CATEGORIES: Dict[str, Tuple[List[str], List[str]]] = {
    "편의점": (["GS25", "CU", "씨유", "세븐일레븐", "7-ELEVEN", "7ELEVEN", "이마트24", "EMART24", "미니스톱",
             "스토리웨이"], ["편의점"]),
    "카페": (["스타벅스", "STARBUCKS", "투썸플레이스", "투썸", "이디야", "메가커피", "메가MGC", "컴포즈커피", "빽다방",
            "할리스", "폴바셋", "커피빈", "블루보틀", "공차", "더벤티", "매머드커피"], ["카페", "커피", "COFFEE", "CAFE"]),
    "마트": (["이마트", "홈플러스", "롯데마트", "하나로마트", "코스트코", "트레이더스", "노브랜드", "GS더프레시",
            "롯데슈퍼", "농협"], ["마트", "슈퍼", "식자재"]),
    "서점": (["교보문고", "영풍문고", "알라딘", "예스24", "YES24"], ["서점", "문고", "책방"]),
    "패스트푸드": (["맥도날드", "버거킹", "롯데리아", "KFC", "맘스터치", "서브웨이", "SUBWAY"], ["버거"]),
    "빵집": (["파리바게뜨", "파리바게트", "뚜레쥬르", "성심당"], ["베이커리", "제과", "빵"]),
    "음식점": (["김밥천국", "본죽", "한솥", "교촌", "BBQ", "BHC", "굽네", "네네치킨", "처갓집", "도미노", "피자헛",
             "맥도날드딜리버리"],
            ["식당", "음식", "김밥", "국밥", "치킨", "피자", "분식", "고기", "횟집", "반점", "냉면", "갈비", "곱창",
             "족발", "초밥", "돈까스", "돼지", "한우", "삼겹", "소고기", "포차", "주점", "호프", "이자카야", "쌀국수",
             "칼국수", "설렁탕", "해장국", "순대국", "백반", "한식", "중식", "일식", "양식"]),
    "배달": (["배달의민족", "배민", "요기요", "쿠팡이츠"], ["배달"]),
    "온라인쇼핑": (["쿠팡", "11번가", "G마켓", "지마켓", "옥션", "SSG", "무신사", "네이버쇼핑", "마켓컬리", "컬리",
               "알리익스프레스", "테무"], ["쇼핑"]),
    "주유소": (["SK에너지", "GS칼텍스", "S-OIL", "에쓰오일", "현대오일뱅크"], ["주유소", "주유"]),
    "약국": ([], ["약국"]),
    "병원": ([], ["병원", "의원", "치과", "한의원", "클리닉"]),
    "교통": (["카카오T", "티머니", "코레일", "KTX", "SRT"], ["택시", "버스", "지하철", "주차"]),
    "문화": (["CGV", "메가박스", "롯데시네마"], ["영화", "극장", "공연"]),
}
CATEGORY_NAMES = list(CATEGORIES)

# 질문에 쓰이는 다른 표현 → 업종
CATEGORY_ALIASES = {
    "커피숍": "카페", "커피집": "카페", "커피": "카페", "베이커리": "빵집", "제과점": "빵집", "대형마트": "마트",
    "슈퍼": "마트", "장보기": "마트", "책방": "서점", "책": "서점", "온라인": "온라인쇼핑", "쇼핑몰": "온라인쇼핑",
    "인터넷쇼핑": "온라인쇼핑", "햄버거": "패스트푸드", "버거": "패스트푸드", "식당": "음식점", "밥집": "음식점",
    "외식": "음식점", "배달음식": "배달", "기름": "주유소", "주유": "주유소", "병원비": "병원", "택시": "교통",
    "영화관": "문화", "영화": "문화", "식비": "음식점", "밥값": "음식점", "고깃집": "음식점", "김밥집": "음식점",
    "술집": "음식점", "맛집": "음식점", "택시비": "교통", "교통비": "교통", "약값": "약국",
}

# 가맹점 줄임말 → 브랜드 (사용자가 흔히 쓰는 표현)
BRAND_ALIASES = {
    "스벅": "스타벅스", "맥날": "맥도날드", "버킹": "버거킹", "롯리": "롯데리아", "파바": "파리바게뜨",
    "뚜쥬": "뚜레쥬르", "투썸": "투썸플레이스", "빽다": "빽다방", "세븐": "세븐일레븐", "이디야커피": "이디야",
    "다이소": "다이소", "올영": "올리브영", "배민": "배달의민족", "쿠팡이츠": "쿠팡이츠",
}

# 품목 분류: 품목명에는 '담배' 같은 분류명이 없음 ('에쎄 체인지 1mg') → 저장 시 items[].category 로 붙인다
# 한 품목이 여러 분류일 수 있음 (라볶이 → 라면, 분식)
ITEM_CATEGORIES: Dict[str, List[str]] = {
    "담배": ["담배", "에쎄", "ESSE", "말보로", "MARLBORO", "던힐", "DUNHILL", "레종", "RAISON", "메비우스", "MEVIUS",
           "보헴", "BOHEM", "팔리아멘트", "PARLIAMENT", "카멜", "CAMEL", "아이코스", "IQOS", "테리아", "TEREA",
           "히츠", "HEETS", "라크", "LARK", "켄트", "KENT", "버지니아", "한라산", "시가", "궐련", "릴 하이브리드"],
    "주류": ["소주", "맥주", "카스", "CASS", "테라", "TERRA", "참이슬", "처음처럼", "진로", "하이트", "켈리", "막걸리",
           "와인", "위스키", "하이볼", "사케", "청하", "버드와이저", "하이네켄", "호가든", "칭따오", "아사히", "생맥",
           "새로", "좋은데이", "복분자"],
    "커피": ["커피", "아메리카노", "라떼", "에스프레소", "카푸치노", "콜드브루", "마키아토", "모카", "카페모카", "드립"],
    "음료": ["콜라", "사이다", "생수", "주스", "우유", "에이드", "녹차", "포카리", "게토레이", "탄산수", "음료", "티",
           "스무디", "요구르트", "두유", "밀크티"],
    "고기": ["고기", "삼겹살", "목살", "항정살", "갈비", "등심", "안심", "채끝", "한우", "소고기", "돼지고기", "닭고기",
           "차돌", "우삼겹", "불고기", "제육", "족발", "보쌈", "곱창", "대창", "막창", "스테이크", "베이컨", "소시지",
           "프랑크", "육회", "갈매기살", "가브리살", "부채살", "치킨", "닭갈비"],
    "치킨": ["치킨", "후라이드", "양념치킨", "허니콤보", "콤보", "순살", "닭강정", "닭다리"],
    "라면": ["라면", "컵라면", "신라면", "진라면", "진리면", "왕뚜껑", "짜파게티", "불닭", "너구리", "안성탕면",
           "육개장 사발", "라볶이", "튀김우동"],
    "과자": ["과자", "새우깡", "포카칩", "꼬북칩", "허니버터칩", "초코파이", "빼빼로", "오레오", "쿠키", "젤리",
           "초콜릿", "초콜렛", "사탕", "프링글스", "칩"],
    "분식": ["김밥", "떡볶이", "라볶이", "순대", "튀김", "어묵", "주먹밥", "삼각김밥", "쫄면"],
    "식사": ["덮밥", "찌개", "국밥", "백반", "돈까스", "냉면", "비빔밥", "정식", "공기밥", "도시락", "볶음밥", "짜장",
           "짬뽕", "우동", "국수", "탕", "덮밥", "카레", "파스타", "피자", "햄버거", "버거", "샌드위치"],
    "의약품": ["타이레놀", "판콜", "게보린", "베아제", "훼스탈", "후시딘", "마데카솔", "밴드", "파스", "감기약",
            "소화제", "진통제", "영양제", "비타민", "알약", "시럽"],
    "생활용품": ["종량제", "봉투", "휴지", "물티슈", "세제", "샴푸", "치약", "칫솔", "건전지"],
    "영화": ["관람권", "영화", "팝콘", "예매"],
}
ITEM_CATEGORY_ALIASES = {
    "술": "주류", "맥주": "주류", "소주": "주류", "음료수": "음료", "마실것": "음료", "육류": "고기", "간식": "과자",
    "약": "의약품", "밥": "식사", "끼니": "식사", "식사": "식사", "치킨": "치킨", "커피": "커피", "담배": "담배",
    "고기": "고기", "라면": "라면", "과자": "과자", "분식": "분식", "영화": "영화", "생필품": "생활용품",
    "고깃집": "고기", "술값": "주류", "술집": "주류", "담배값": "담배", "커피값": "커피", "영화비": "영화",
    "약값": "의약품",
}

# '담배값', '커피값', '영화비', '김밥집' → 앞부분이 업종/품목이면 그 단어로
_SUFFIXES = ("값", "비", "집", "가게", "류")


def strip_suffix(word: str) -> str:
    w = (word or "").strip()
    for suf in _SUFFIXES:
        stem = w[: -len(suf)]
        if w.endswith(suf) and len(stem) >= 1 and (stem in CATEGORIES or stem in CATEGORY_ALIASES
                                                    or stem in ITEM_CATEGORIES or stem in ITEM_CATEGORY_ALIASES
                                                    or is_item_word(stem)):
            return stem
    return w


def is_item_word(word: str) -> bool:
    """품목 사전에 있는 상품·음식 이름인지 ('삼겹살', '새우깡', '에쎄')"""
    w = (word or "").strip().upper()
    return bool(w) and any(w == x.upper() for words in ITEM_CATEGORIES.values() for x in words)


def to_item_category(word: str) -> Optional[str]:
    """질문 속 단어 → 품목 분류 (없으면 None)"""
    w = (word or "").strip()
    if w in ITEM_CATEGORIES:
        return w
    return ITEM_CATEGORY_ALIASES.get(w)


# 다른 단어의 일부로 흔히 나와서 '단어 경계' 가 필요한 짧은 단어 (파스타≠파스, 테라스≠테라, 새로운≠새로)
_BOUNDARY_WORDS = {"파스", "새로", "시가", "카스", "테라", "드립", "티", "칩", "탕", "릴", "봉투", "정식"}


def _word_in(w: str, upper: str, squashed: str) -> bool:
    wu = w.upper()
    if len(w) == 1 or w in _BOUNDARY_WORDS or re.fullmatch(r"[A-Z0-9]{1,4}", wu):
        return re.search(rf"(?<![가-힣A-Z]){re.escape(wu)}(?![가-힣A-Z])", upper) is not None
    return _squash(w) in squashed


def classify_item(name: Optional[str]) -> List[str]:
    """품목명 → 분류 목록 ('에쎄 체인지 1mg' → ['담배'], '라볶이' → ['라면', '분식'])"""
    if not name:
        return []
    upper, squashed = name.upper(), _squash(name)
    return [category for category, words in ITEM_CATEGORIES.items()
            if any(_word_in(w, upper, squashed) for w in words)]


_PROVIDER_KO = {"KAKAOPAY": "카카오페이", "TOSSPAY": "토스페이", "NAVERPAY": "네이버페이", "PAYCO": "페이코",
                "SAMSUNGPAY": "삼성페이", "APPLEPAY": "애플페이"}
_METHOD_KO = {"CARD": "카드", "CASH": "현금", "PAY_MONEY": "페이머니", "BANK_TRANSFER": "계좌이체", "POINT": "포인트"}
_CARD_KO = {"SHINHAN": "신한카드", "KB": "국민카드", "SAMSUNG": "삼성카드", "HYUNDAI": "현대카드", "LOTTE": "롯데카드",
            "WOORI": "우리카드", "HANA": "하나카드", "BC": "비씨카드", "NH": "농협카드", "CITI": "씨티카드"}
_WEEKDAY_KO = "월화수목금토일"


def _squash(s: str) -> str:
    return re.sub(r"[\s()\[\]·.,_-]+", "", s or "").upper()


def to_category(word: str) -> Optional[str]:
    """질문 속 단어 → 업종 (없으면 None)"""
    w = (word or "").strip()
    if w in CATEGORIES:
        return w
    return CATEGORY_ALIASES.get(w)


def category_brands(category: str) -> List[str]:
    return CATEGORIES.get(category, ([], []))[0]


def _brand_in(brand: str, upper: str, squashed: str) -> bool:
    b = brand.upper()
    if re.fullmatch(r"[A-Z0-9]{1,3}", b):  # CU, KFC, SSG 같은 짧은 영문은 단어 경계로 (CUCKOO 오인 방지)
        return re.search(rf"(?<![A-Z]){re.escape(b)}(?![A-Z])", upper) is not None
    return _squash(brand) in squashed


def classify_merchant(name: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    """가맹점명 → (업종, 브랜드). 브랜드가 맞으면 우선, 아니면 업종 단어로 판단"""
    s = _squash(name or "")
    if not s:
        return None, None
    upper = (name or "").upper()
    for category, (brands, _) in CATEGORIES.items():
        for brand in sorted(brands, key=len, reverse=True):  # '이마트24' 를 '이마트' 보다 먼저
            if _brand_in(brand, upper, s):
                return category, brand
    for category, (_, words) in CATEGORIES.items():
        if any(_squash(w) in s for w in words):
            return category, None
    return None, None


def normalize_merchant(name: Optional[str]) -> Optional[str]:
    """
    집계용 가맹점명: 법인 표기·공백·OCR 잡음 제거
      '(주) 고메드갤' → '고메드갤',  'Q서서울농협하나로마트사직점' → '서서울농협하나로마트사직점'
    """
    if not name:
        return None
    s = re.sub(r"\(\s*(주|유|사)\s*\)|㈜|주식회사|유한회사", "", name)
    s = re.sub(r"^[A-Za-z](?=[가-힣])", "", s.strip())  # 영수증 앞 1글자 영문 잡음 (Q서서울…)
    s = re.sub(r"\s+", " ", s).strip()
    return s or name.strip()


def _parse_dt(tx: Dict[str, Any]) -> Tuple[Optional[date], Optional[int]]:
    at = tx.get("transactedAt")
    if at:
        try:
            dt = datetime.fromisoformat(str(at).replace("Z", "+00:00"))
            return dt.date(), dt.hour
        except ValueError:
            pass
    d = tx.get("transactedDate")
    if d:
        try:
            return date.fromisoformat(str(d)[:10]), None
        except ValueError:
            pass
    return None, None


def enrich(tx: Dict[str, Any]) -> Dict[str, Any]:
    """거래 문서에 파생 필드를 채운 사본을 반환 (원본 필드는 건드리지 않음)"""
    doc = dict(tx)
    merchant = dict(doc.get("merchant") or {})
    category, brand = classify_merchant(merchant.get("name"))
    merchant["category"] = category
    merchant["brand"] = brand
    merchant["normalizedName"] = normalize_merchant(merchant.get("name"))
    doc["merchant"] = merchant
    doc["items"] = [{**i, "category": classify_item(i.get("name"))} for i in (doc.get("items") or [])]

    d, hour = _parse_dt(doc)
    doc["dayOfWeek"] = d.isoweekday() if d else None  # 1=월 … 7=일
    doc["transactedHour"] = hour
    doc["searchText"] = search_text(doc)
    return doc


def search_text(doc: Dict[str, Any]) -> str:
    """키워드 검색용 문장: 가맹점·업종·브랜드·주소·품목·결제수단을 한국어로"""
    m = doc.get("merchant") or {}
    p = doc.get("payment") or {}
    parts = [m.get("name"), m.get("normalizedName"), m.get("brand"), m.get("category"), m.get("address")]
    parts += [i.get("name") for i in (doc.get("items") or [])]
    parts += [c for i in (doc.get("items") or []) for c in (i.get("category") or [])]
    parts += [_PROVIDER_KO.get(p.get("provider") or ""), _METHOD_KO.get(p.get("method") or ""),
              _CARD_KO.get(p.get("cardCompany") or ""), (doc.get("extra") or {}).get("paymentMethodText")]
    seen, out = set(), []
    for x in parts:
        if x and str(x) not in seen:
            seen.add(str(x))
            out.append(str(x))
    return " ".join(out)


def embedding_text(doc: Dict[str, Any]) -> str:
    """의미 검색용 문장 (E5 passage). 숫자보다 '무엇을 어디서' 를 중심으로"""
    m = doc.get("merchant") or {}
    p = doc.get("payment") or {}
    items = [i.get("name") for i in (doc.get("items") or []) if i.get("name")][:15]
    lines = [f"가맹점: {m.get('name') or '미상'}"]
    if m.get("category"):
        lines.append(f"업종: {m['category']}")
    if m.get("address"):
        lines.append(f"주소: {m['address']}")
    if items:
        lines.append("품목: " + ", ".join(items))
    via = _PROVIDER_KO.get(p.get("provider") or "") or _METHOD_KO.get(p.get("method") or "")
    if via:
        lines.append(f"결제: {via}")
    if doc.get("dayOfWeek"):
        lines.append(f"요일: {_WEEKDAY_KO[doc['dayOfWeek'] - 1]}요일")
    return " / ".join(lines)
