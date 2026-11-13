// 백엔드 API 호출 모음: /api/ocr/* → ocr_backend, 그 외 /api/* (챗봇·거래 목록) → rag_backend
// 개발: vite proxy, 배포: nginx 가 /api 를 백엔드로 프록시하므로 상대 경로만 사용

async function jsonOrThrow(res) {
  if (res.ok) return res.json()
  let detail = `${res.status} ${res.statusText}`
  try {
    const body = await res.json()
    detail = typeof body.detail === 'string' ? body.detail : body.detail?.message || JSON.stringify(body.detail)
  } catch {
    /* 본문 없음 */
  }
  throw new Error(detail)
}

/**
 * 챗봇 SSE 스트림.
 * EventSource 는 GET 만 지원하므로 fetch + ReadableStream 으로 직접 파싱한다.
 * onEvent(eventName, data) 가 이벤트마다 호출된다.
 */
export async function streamChat({ userId, message, history, signal, onEvent }) {
  const res = await fetch('/api/chat/stream', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
    body: JSON.stringify({ userId, message, history }),
    signal,
  })
  if (!res.ok || !res.body) await jsonOrThrow(res)

  const reader = res.body.pipeThrough(new TextDecoderStream()).getReader()
  let buffer = ''
  for (;;) {
    const { value, done } = await reader.read()
    if (done) break
    buffer += value.replace(/\r\n/g, '\n')
    let sep
    while ((sep = buffer.indexOf('\n\n')) >= 0) {
      const block = buffer.slice(0, sep)
      buffer = buffer.slice(sep + 2)
      const parsed = parseSseBlock(block)
      if (parsed) onEvent(parsed.event, parsed.data)
    }
  }
}

function parseSseBlock(block) {
  let event = 'message'
  const dataLines = []
  for (const line of block.split('\n')) {
    if (line.startsWith('event:')) event = line.slice(6).trim()
    else if (line.startsWith('data:')) dataLines.push(line.slice(5).trimStart())
  }
  if (!dataLines.length) return null
  try {
    return { event, data: JSON.parse(dataLines.join('\n')) }
  } catch {
    return { event, data: dataLines.join('\n') }
  }
}

export async function recognizeReceipt({ file, userId, forceLlm }) {
  const form = new FormData()
  form.append('file', file)
  form.append('user_id', userId)
  if (forceLlm) form.append('force_llm', 'true')
  return jsonOrThrow(await fetch('/api/ocr/recognize', { method: 'POST', body: form }))
}

export async function saveTransactions({ userId, transactions }) {
  return jsonOrThrow(
    await fetch('/api/ocr/transactions', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ userId, transactions }),
    }),
  )
}

export async function listTransactions(userId, limit = 30) {
  const q = new URLSearchParams({ userId, limit: String(limit) })
  return jsonOrThrow(await fetch(`/api/transactions?${q}`))
}

export async function deleteTransaction(userId, transactionId) {
  const q = new URLSearchParams({ userId })
  return jsonOrThrow(
    await fetch(`/api/transactions/${encodeURIComponent(transactionId)}?${q}`, { method: 'DELETE' }),
  )
}

// 기간 지출 요약 (사이드바 카드): rag_backend 구조화 조회 (startDate 포함, endDate 미포함)
export async function spendingSummary({ userId, startDate, endDate }) {
  return jsonOrThrow(
    await fetch('/api/transactions/query', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ userId, mode: 'AGGREGATE', startDate, endDate, groupBy: 'CATEGORY', limit: 1 }),
    }),
  )
}
