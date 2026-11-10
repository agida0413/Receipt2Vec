"""
검색어 전처리 (정규화)

OpenSearch 하이브리드 검색 + LLM 리랭킹 전에 검색어를 일관된 형태로 정리.
인덱스 토큰과 일치도를 높여 recall 향상.
(도메인 독립적인 일반 전처리)
"""
import re


# 한글 외톨이 자모 (자음·모음 단독) — 의미 없는 입력 노이즈
_HANGUL_JAMO_RE = re.compile(r"[ㄱ-ㅣ]")
# 한글·영문·숫자·공백만 유지
_SAFE_CHARS_RE = re.compile(r"[^\w가-힣\s]")
_MULTI_SPACE_RE = re.compile(r"\s+")


def preprocess_query(query: str) -> str:
    """검색어 정규화.

    단계:
      1. 트림 + 중복 공백 정리
      2. 외톨이 한글 자모 제거 (입력 오타 노이즈)
      3. 특수문자 제거 (한글·영문·숫자·공백만 유지)

    정리 결과가 빈 문자열이면 빈 문자열 반환(호출자가 0건 처리).
    """
    if not query:
        return ""

    q = _MULTI_SPACE_RE.sub(" ", query.strip())
    q = _HANGUL_JAMO_RE.sub("", q)
    # 특수문자 제거(공백 치환 아님): "판}교" → "판교"
    q = _SAFE_CHARS_RE.sub("", q)
    q = _MULTI_SPACE_RE.sub(" ", q).strip()
    return q


def preprocess_query_with_log(query: str) -> str:
    """preprocess_query 호출 + 변경된 경우 콘솔 로그 출력."""
    normalized = preprocess_query(query)
    if normalized != query:
        print(f"[Query] 전처리: {query!r} → {normalized!r}")
    return normalized
