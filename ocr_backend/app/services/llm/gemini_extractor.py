"""
Gemini 기반 거래 정보 추출 (규칙 파서 실패 시에만 호출)

- 입력: OCR 텍스트 (카드번호는 끝 4자리만 남기고 마스킹된 상태)
- 출력: LlmExtraction (JSON 스키마 강제)
- 결과는 verifier 로 원문 대조 후 사용
"""
import logging
from datetime import date
from typing import Optional, Protocol

from app.core.config import LlmConfig, settings
from app.domain.transaction import SourceType
from app.services.llm.gemini_client import (  # noqa: F401  (parse_retry_delay 는 테스트/외부 호환용 재노출)
    QuotaExceededError, QuotaGuard, call_with_retry, get_client, parse_retry_delay, shared_quota_guard,
)
from app.services.llm.schema import LlmExtraction

logger = logging.getLogger(__name__)

SYSTEM_INSTRUCTION = """\
당신은 한국의 종이 영수증 또는 간편결제 앱(카카오페이, 토스페이, 네이버페이 등) 결제내역 화면을
OCR 한 텍스트에서 거래 정보를 추출하는 시스템입니다.

규칙:
1. 원문에 있는 정보만 사용합니다. 원문에 없으면 null 로 둡니다. 추측하거나 계산해서 만들지 마십시오.
2. 금액은 원 단위 정수입니다. 콤마를 제거하고 항상 양수로 씁니다. 취소/환불은 status 로 표현합니다.
3. OCR 오류로 천 단위 구분자가 '.' 으로 읽힐 수 있습니다 (예: 12.000 → 12000). 원화에는 소수점이 없습니다.
4. 날짜는 YYYY-MM-DD, 시간은 24시간제 HH:MM 또는 HH:MM:SS 입니다. '오후 2:23' → 14:23.
   연도가 원문에 없으면 {year}년으로 봅니다.
5. 종이 영수증은 거래 1건입니다. 결제내역 '목록' 화면이면 목록의 결제 건마다 transactions 에 하나씩 넣습니다.
   월 합계·잔액 같은 요약 줄은 거래가 아닙니다.
   목록 화면에서 각 거래의 날짜는 그 거래보다 '위쪽에 있는 가장 가까운 날짜 헤더'입니다
   (예: '11월 3일' 헤더 아래의 거래들은 다음 날짜 헤더가 나오기 전까지 모두 11월 3일).
6. totalAmount 는 최종 결제 금액입니다 (받을금액/결제금액/합계/승인금액). 공급가액·부가세·받은돈·거스름돈과 혼동하지 마십시오.
7. 가맹점명은 원문 그대로 씁니다. 잘 알려진 브랜드명의 한 글자 OCR 오타만 바로잡을 수 있습니다
   (예: 스타벽스 → 스타벅스). 글자를 삭제하거나 지명·지점명을 바꾸지 마십시오 (예: '서서울농협' 은 그대로).
   줄 맨 앞의 로고 오인식 기호(Q, ©, ® 등)는 제거합니다. 숫자는 절대 바꾸지 마십시오.
8. items 는 영수증의 품목입니다 (상품명, 수량, 단가, 금액). 할인 줄은 amount 를 음수로 씁니다. 결제앱 화면은 보통 빈 배열입니다.
9. paymentMethod: CARD(신용/체크카드), CASH(현금), PAY_MONEY(카카오페이머니·토스머니 등 충전 잔액),
   BANK_TRANSFER(계좌), POINT(포인트), 모르면 UNKNOWN.
   paymentProvider: 카카오페이 등 간편결제를 거쳤으면 그 서비스, 아니면 null.
10. cardLast4 는 '카드번호' 끝 4자리 숫자입니다. 마스킹(*)되어 있으면 null.
    상품 바코드(8801… 같은 13자리)는 카드번호가 아닙니다.
11. installmentMonths: 원문에 '일시불' 이 있으면 0, 'n개월' 이면 n, 표시가 없으면 null.
12. approvalNumber 는 '승인번호' 라벨이 붙은 값만입니다. POS 번호·거래번호·영수증번호는 승인번호가 아닙니다.
13. vatAmount 는 '부가세'/'세액' 금액입니다. '부가세 면세물품가액'·'부가세 과세물품가액' 은 부가세가 아닙니다.
    supplyAmount 는 공급가액/과세물품가액입니다 (면세물품가액 제외).
14. cardCompany 는 카드 결제 정보(카드사명, 매입사, 발급사)에 적힌 경우만입니다.
    가맹점명에 들어간 단어(예: 농협하나로마트의 '농협')는 카드사가 아닙니다.
15. 원문에 없는 정보는 반드시 null 입니다. 영수증 일부가 잘려 카드 정보가 없으면 카드 관련 필드는 모두 null.
"""

PROMPT_TEMPLATE = """\
규칙 기반 파서의 문서 유형 판단: {hint} (틀릴 수 있음)

OCR 텍스트:
```
{text}
```"""


class Extractor(Protocol):
    """테스트에서 가짜 구현을 주입할 수 있도록 인터페이스 분리"""
    model_name: str

    @property
    def is_available(self) -> bool: ...

    async def extract(self, text: str, hint: SourceType) -> LlmExtraction: ...


class GeminiExtractor:
    def __init__(self, config: Optional[LlmConfig] = None, guard: Optional[QuotaGuard] = None):
        self._config = config or settings.llm
        self._client = None
        self._guard = guard or QuotaGuard()

    @property
    def model_name(self) -> str:
        return self._config.model_name

    @property
    def is_available(self) -> bool:
        return self._config.is_configured

    @property
    def cooldown_message(self) -> Optional[str]:
        """할당량 초과로 호출을 멈춘 상태면 안내 문구, 아니면 None"""
        return self._guard.message(self.model_name)

    def _get_client(self):
        if self._client is None:
            self._client = get_client(self._config.api_key)
        return self._client

    def _build_config(self):
        from google.genai import types
        return types.GenerateContentConfig(
            system_instruction=SYSTEM_INSTRUCTION.format(year=date.today().year),
            temperature=0.0,
            response_mime_type="application/json",
            response_schema=LlmExtraction,
            thinking_config=types.ThinkingConfig(thinking_budget=self._config.thinking_budget),
        )

    async def extract(self, text: str, hint: SourceType) -> LlmExtraction:
        if not self.is_available:
            raise RuntimeError("LLM_MODE=off 이거나 GEMINI_API_KEY 가 없습니다.")
        prompt = PROMPT_TEMPLATE.format(hint=hint.value, text=text[: self._config.max_input_chars])
        client = self._get_client()
        config = self._build_config()
        response = await call_with_retry(
            lambda: client.aio.models.generate_content(
                model=self._config.model_name, contents=prompt, config=config,
            ),
            guard=self._guard, model_name=self.model_name, timeout=self._config.timeout_sec,
        )
        if not response or not response.text:
            raise RuntimeError("Gemini 응답이 비어 있습니다.")
        return LlmExtraction.model_validate_json(response.text)


gemini_extractor = GeminiExtractor(guard=shared_quota_guard)
