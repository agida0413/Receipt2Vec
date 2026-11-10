"""
공유 텍스트 유틸 (도메인 독립)
"""
import re

_NORM_RE = re.compile(r"[^a-zA-Z0-9가-힣]")


def norm(s) -> str:
    """공백·괄호·언더스코어·특수문자 모두 제거 + 소문자화.

    norm("Backend_개발자(시니어)") = "backend개발자시니어"
    norm("서울 강남구") = "서울강남구"
    """
    if not s:
        return ""
    return _NORM_RE.sub("", str(s)).lower()
