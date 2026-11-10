"""
LLM 리랭킹/증강 서비스.

provider(LLMProvider) 위에서 검색 후보를 도메인 프롬프트로 리랭킹하고
confidence·reason 을 부여한다. 도메인 의존 부분은 current_domain 이 제공한다:
  - format_candidate(rank, item, query) : 후보 1건의 프롬프트 텍스트
  - reranking_prompt                    : 리랭킹 프롬프트
  - llm_response_id_key                 : LLM 응답 식별자 키

결과 아이템은 map_search_hit 출력 필드 + confidence/reason/searchScore(+rerankScore) 이며, 공통 키는 'id'.
provider 미가용 시 Mock 으로 폴백하며, rerankScore 가 있으면 그 점수를 confidence 로 쓴다.
"""
import json
import re
from difflib import get_close_matches

from app.domains import current_domain
from app.services.llm.providers import build_provider


# 단어를 임의로 'X 타입/라인/시리즈/계열'로 분류한 환각 패턴
_HALLUCINATED_CATEGORY_RE = re.compile(
    r"['\"]?([A-Za-z가-힣0-9]+)['\"]? (타입|라인|시리즈|계열)(의|로|이|이라|이며|이고|입니다)?"
)

# 인용 단어 사이 가운뎃점 나열
_QUOTED_MIDDLE_DOT_RE = re.compile(r"(['\"][^'\"]*['\"])\s*[·・ㆍ‧]\s*(?=['\"])")


def _strip_category_hallucination(reason: str) -> str:
    """'X 타입' 등 임의 분류 환각을 안전한 인용 형태로 치환 (도메인 독립 텍스트 위생)."""
    if not reason:
        return reason

    def _replace(m: re.Match) -> str:
        return f"'{m.group(1)}'이 포함된"

    return _HALLUCINATED_CATEGORY_RE.sub(_replace, reason)


def _normalize_quoted_list(reason: str) -> str:
    """인용 단어 사이 가운뎃점을 쉼표 나열로 치환."""
    if not reason:
        return reason
    return _QUOTED_MIDDLE_DOT_RE.sub(r"\1, ", _QUOTED_MIDDLE_DOT_RE.sub(r"\1, ", reason))


def _clean_reason(reason: str) -> str:
    return _strip_category_hallucination(_normalize_quoted_list(reason))


def _to_result_item(item: dict, *, confidence, reason) -> dict:
    """검색 아이템 + confidence/reason 을 합쳐 응답 아이템으로 만든다.

    기존 필드는 그대로 두고 score 를 searchScore 로 옮긴다.
    """
    out = dict(item)
    out["searchScore"] = out.pop("score", 0)
    out["confidence"] = confidence
    out["reason"] = reason
    return out


class LLMService:
    """LLM 리랭킹/증강 서비스 (provider + 활성 도메인 위)."""

    def __init__(self, provider=None):
        self._provider = provider or build_provider()
        self._id_key = current_domain.llm_response_id_key
        self._prompt = current_domain.reranking_prompt

    @property
    def is_available(self) -> bool:
        return self._provider.is_available

    @property
    def model_name(self) -> str:
        return self._provider.model_name or "mock"

    # ========================================================================
    # Mock 응답 (provider 미가용 시)
    # ========================================================================
    def build_mock_results(self, query: str, candidates: list) -> list:
        results = []
        for i, item in enumerate(candidates):
            # cross-encoder 점수가 있으면 실제 모델 점수를 신뢰도로 사용
            rerank = item.get("rerankScore")
            if rerank is not None:
                confidence = round(rerank * 100)
                reason = f"cross-encoder 관련도 {rerank:.2f} (LLM 미사용)"
                results.append(_to_result_item(item, confidence=confidence, reason=reason))
                continue

            name = item.get("name", "")
            confidence = max(30, 100 - (i * 8))

            if query and name and query in name:
                confidence = min(100, confidence + 20)
                reason = f"검색어 '{query}' 직접 포함"
            elif name and any(char in name for char in query):
                confidence = min(100, confidence + 10)
                reason = "검색어 일부 문자 포함"
            else:
                reason = "의미적 유사성으로 매칭"

            results.append(_to_result_item(item, confidence=confidence, reason=reason))
        return results

    # ========================================================================
    # 검색 결과 LLM 증강 (리랭킹)
    # ========================================================================
    def rerank_candidates(self, query: str, candidates: list) -> list:
        if not self._provider.is_available:
            print("[LLM] Mock 응답 사용")
            return self.build_mock_results(query, candidates)

        try:
            # 후보 텍스트 구성은 도메인이 담당
            candidates_lines = [
                current_domain.format_candidate(i + 1, item, query)
                for i, item in enumerate(candidates)
            ]

            total = len(candidates)
            candidates_text = (
                f"※ 총 {total}개 후보입니다. 반드시 정확히 {total}개 항목을 "
                f"입력 순서대로 모두 반환하세요. 한 개라도 빠뜨리면 잘못된 응답입니다.\n\n"
                + "\n".join(candidates_lines)
            )
            print(f"candidates_text={candidates_text}")
            print(f"query={query}")
            prompt = self._prompt.format(query=query, candidates=candidates_text)

            response_text = self._provider.generate_json(prompt)

            # JSON 파싱 (코드펜스 방어)
            if response_text.startswith("```"):
                response_text = response_text.split("```")[1]
                if response_text.startswith("json"):
                    response_text = response_text[4:]

            llm_results = json.loads(response_text)
            # 응답 식별자 키 → 내부 id 로 매핑 (str 통일)
            result_map = {str(r.get(self._id_key, "")): r for r in llm_results}

            # 진단 로그
            raw_count = len(llm_results)
            unique_count = len(result_map)
            input_ids = {str(item["id"]) for item in candidates}
            response_ids = set(result_map.keys())
            dup_in_response = raw_count - unique_count
            unknown_ids = response_ids - input_ids
            print(f"[LLM] 입력 후보 수: {len(candidates)}, "
                  f"응답 raw={raw_count}, unique={unique_count}, dup={dup_in_response}")
            print(f"[LLM] 응답 id 목록: {list(result_map.keys())[:10]}"
                  f"{'...' if len(result_map) > 10 else ''}")
            if unknown_ids:
                print(f"[LLM] 입력에 없는 응답 id: {unknown_ids}")

            # 식별자 변형 fuzzy 매핑 보정
            if unknown_ids:
                _missing_set = input_ids - response_ids
                for unknown in list(unknown_ids):
                    closest = get_close_matches(unknown, list(_missing_set), n=1, cutoff=0.85)
                    if closest:
                        target = closest[0]
                        result_map[target] = result_map.pop(unknown)
                        response_ids.discard(unknown)
                        response_ids.add(target)
                        _missing_set.discard(target)
                        unknown_ids.discard(unknown)
                        print(f"[LLM] id 매핑 보정: {unknown} → {target}")

            missing_ids = []
            enhanced = []
            for item in candidates:
                item_id = item["id"]
                llm_item = result_map.get(str(item_id), {})
                if not llm_item:
                    missing_ids.append(item_id)

                raw_reason = llm_item.get("reason", "LLM 분석 결과 없음")
                enhanced.append(_to_result_item(
                    item,
                    confidence=llm_item.get("confidence", 50),
                    reason=_clean_reason(raw_reason),
                ))

            if missing_ids:
                print(f"[LLM] 응답 누락 id ({len(missing_ids)}건): {missing_ids[:5]}")

            return enhanced

        except Exception as e:
            print(f"[LLM Error] {e}, Mock 응답으로 대체")
            return self.build_mock_results(query, candidates)


# 싱글톤 인스턴스
llm_service = LLMService()
