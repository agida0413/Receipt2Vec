export const won = (v) => (v == null ? '-' : `${Math.round(v).toLocaleString('ko-KR')}원`)

export const METHOD_KO = {
  CARD: '카드',
  CASH: '현금',
  PAY_MONEY: '페이머니',
  BANK_TRANSFER: '계좌',
  POINT: '포인트',
  UNKNOWN: '기타',
}

export const PROVIDER_KO = {
  KAKAOPAY: '카카오페이',
  TOSSPAY: '토스페이',
  NAVERPAY: '네이버페이',
  PAYCO: '페이코',
  SAMSUNGPAY: '삼성페이',
  APPLEPAY: '애플페이',
  NONE: '간편결제 아님',
}

export const STATUS_KO = { APPROVED: '승인', CANCELED: '취소', REFUNDED: '환불', FAILED: '실패' }

export const REVIEW_REASON_KO = {
  NO_TRANSACTIONS: '거래를 찾지 못함',
  UNSURE_DOC_TYPE: '문서 유형 불확실',
  MISSING_MERCHANT: '가맹점 없음',
  MISSING_DATE: '날짜 없음',
  MISSING_AMOUNT: '금액 없음',
  AMOUNT_INFERRED: '총액 추정값',
  ITEMS_SUM_MISMATCH: '품목 합계 불일치',
  LOW_OCR_SCORE: 'OCR 신뢰도 낮음',
  LLM_EXTRACTED: 'AI 추출값',
  LLM_UNVERIFIED_VALUE: '일부 값 원문 불일치',
}

export const via = (row) => PROVIDER_KO[row.provider] || METHOD_KO[row.method] || ''

export const shortDate = (d) => (d ? `${Number(d.slice(5, 7))}/${Number(d.slice(8, 10))}` : '날짜 미상')
