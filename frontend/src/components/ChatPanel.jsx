import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { streamChat } from '../api.js'
import { IconChart, IconCheck, IconCoffee, IconCopy, IconInfo, IconLogo, IconSend, IconSparkle, IconStop,
  IconStore, IconWallet } from './icons.jsx'
import Markdown from './Markdown.jsx'
import ResultView from './ResultView.jsx'

const SUGGESTIONS = [
  { icon: IconWallet, title: '이번 달 지출', q: '이번 달 얼마 썼어?' },
  { icon: IconStore, title: '자주 간 곳', q: '식당 어디를 제일 많이 갔지?' },
  { icon: IconCoffee, title: '품목으로 찾기', q: '이번 주 커피에 쓴 돈 얼마야?' },
  { icon: IconChart, title: '업종별 분석', q: '이번 달 업종별 지출 알려줘' },
]

let seq = 0
const newId = () => `m${Date.now()}-${seq++}`

// ---------------------------------------------------------------------------
// 한 글자씩 출력 (서버 토큰이 몰려 와도 일정한 속도로, 밀리면 조금 빠르게)
// ---------------------------------------------------------------------------
function useTypewriter(text, streaming, onTick) {
  const [shown, setShown] = useState(0)
  const ref = useRef({ text, streaming, onTick })
  ref.current = { text, streaming, onTick }

  useEffect(() => {
    let raf
    let last = performance.now()
    let carry = 0
    let cur = 0
    const tick = (now) => {
      const { text: full, streaming: live, onTick: cb } = ref.current
      const dt = Math.min(100, now - last)
      last = now
      if (cur < full.length) {
        const backlog = full.length - cur
        // 기본 초당 45자, 밀린 글자가 많으면 가속 (답변이 끝났는데 한참 타이핑하지 않도록)
        const cps = backlog > 400 ? 600 : backlog > 150 ? 220 : backlog > 40 ? 90 : 45
        carry += (cps * dt) / 1000
        const step = Math.floor(carry)
        if (step >= 1) {
          carry -= step
          cur = Math.min(full.length, cur + step)
          setShown(cur)
          cb?.()
        }
      }
      // 스트리밍이 끝나고 다 보여줬으면 멈춤
      if (live || cur < full.length) raf = requestAnimationFrame(tick)
    }
    raf = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf)
  }, [])

  return Math.min(shown, text.length)
}

export default function ChatPanel({ userId, resetKey, onAsked }) {
  const [messages, setMessages] = useState([])
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const abortRef = useRef(null)
  const scrollRef = useRef(null)
  const stickRef = useRef(true)
  const inputRef = useRef(null)

  // 사용자 전환·새 대화 → 초기화 (사용자별 챗봇)
  useEffect(() => {
    abortRef.current?.abort()
    setMessages([])
    setInput('')
  }, [userId, resetKey])

  const scrollToBottom = useCallback((force = false) => {
    const el = scrollRef.current
    if (el && (force || stickRef.current)) el.scrollTop = el.scrollHeight
  }, [])

  useLayoutEffect(() => scrollToBottom(), [messages, scrollToBottom])

  // 입력창 높이 자동 조절
  useLayoutEffect(() => {
    const el = inputRef.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = `${Math.min(el.scrollHeight, 200)}px`
  }, [input])

  const patch = (id, fn) => setMessages((ms) => ms.map((m) => (m.id === id ? { ...m, ...fn(m) } : m)))

  async function send(text) {
    const message = text.trim()
    if (!message || busy || !userId) return
    setInput('')
    stickRef.current = true

    // 후속 질문 해석용 이전 대화 (완료된 메시지만)
    const history = messages
      .filter((m) => m.content && !m.error)
      .slice(-6)
      .map((m) => ({ role: m.role, content: m.content }))

    const botId = newId()
    setMessages((ms) => [
      ...ms,
      { id: newId(), role: 'user', content: message },
      { id: botId, role: 'assistant', content: '', streaming: true, status: '질문을 이해하는 중', notices: [] },
    ])
    setBusy(true)
    requestAnimationFrame(() => scrollToBottom(true))

    const controller = new AbortController()
    abortRef.current = controller
    try {
      await streamChat({
        userId,
        message,
        history,
        signal: controller.signal,
        onEvent: (event, data) => {
          switch (event) {
            case 'status':
              patch(botId, () => ({ status: data.message?.replace(/…$/, '') }))
              break
            case 'plan':
              patch(botId, () => ({ plan: data }))
              break
            case 'result':
              patch(botId, () => ({ result: data }))
              break
            case 'token':
              patch(botId, (m) => ({ content: m.content + data.text, status: null }))
              break
            case 'notice':
              patch(botId, (m) => (m.notices.includes(data.message) ? {} : { notices: [...m.notices, data.message] }))
              break
            case 'error':
              patch(botId, () => ({ error: data.message, status: null }))
              break
            case 'done':
              patch(botId, () => ({ meta: data, status: null }))
              break
            default:
          }
        },
      })
    } catch (e) {
      if (e.name === 'AbortError') patch(botId, (m) => ({ status: null, stopped: true, content: m.content }))
      else patch(botId, () => ({ error: `요청에 실패했어요: ${e.message}`, status: null }))
    } finally {
      patch(botId, () => ({ streaming: false }))
      setBusy(false)
      abortRef.current = null
      onAsked?.()
      inputRef.current?.focus()
    }
  }

  const onScroll = () => {
    const el = scrollRef.current
    if (el) stickRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80
  }

  const empty = messages.length === 0

  return (
    <section className="chat">
      <div className="chat-scroll" ref={scrollRef} onScroll={onScroll}>
        {empty ? (
          <div className="welcome">
            <div className="welcome-logo"><IconLogo size={48} /></div>
            <h1>무엇이 궁금하세요?</h1>
            <p className="muted">업로드한 영수증과 결제내역을 근거로, 지출을 찾아보고 계산해 드려요.</p>
            <div className="suggestions">
              {SUGGESTIONS.map(({ icon: Icon, title, q }) => (
                <button key={q} className="suggestion" onClick={() => send(q)} disabled={busy}>
                  <span className="suggestion-icon"><Icon size={18} /></span>
                  <span className="suggestion-text">
                    <b>{title}</b>
                    <span>{q}</span>
                  </span>
                </button>
              ))}
            </div>
          </div>
        ) : (
          <div className="thread">
            {messages.map((m) =>
              m.role === 'user' ? (
                <div key={m.id} className="msg user"><div className="bubble">{m.content}</div></div>
              ) : (
                <BotMessage key={m.id} m={m} onTick={scrollToBottom} />
              ),
            )}
          </div>
        )}
      </div>

      <div className="composer-wrap">
        <form
          className={`composer ${busy ? 'is-busy' : ''}`}
          onSubmit={(e) => {
            e.preventDefault()
            send(input)
          }}
        >
          <textarea
            ref={inputRef}
            rows={1}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
                e.preventDefault()
                send(input)
              }
            }}
            placeholder={userId ? '지출에 대해 무엇이든 물어보세요' : '먼저 사용자를 선택하세요'}
            disabled={!userId}
            maxLength={500}
          />
          {busy ? (
            <button type="button" className="send-btn stop" onClick={() => abortRef.current?.abort()} aria-label="생성 중지" title="생성 중지">
              <IconStop size={12} />
            </button>
          ) : (
            <button type="submit" className="send-btn" disabled={!input.trim() || !userId} aria-label="보내기" title="보내기 (Enter)">
              <IconSend size={18} />
            </button>
          )}
        </form>
        <p className="composer-hint">답변은 저장된 거래만 근거로 하며, 금액·건수는 데이터에서 직접 계산해요.</p>
      </div>
    </section>
  )
}

function BotMessage({ m, onTick }) {
  const shown = useTypewriter(m.content, m.streaming, onTick)
  const typing = m.streaming || shown < m.content.length
  const visible = m.content.slice(0, shown)
  const [copied, setCopied] = useState(false)
  // 타이핑이 끝나면 근거 카드가 나타나므로 한 번 더 아래로
  useEffect(() => {
    if (!typing) requestAnimationFrame(() => onTick?.())
  }, [typing, onTick])

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(m.content)
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    } catch {
      /* 클립보드 불가 */
    }
  }

  const waiting = m.streaming && !m.content && !m.error
  return (
    <div className="msg bot">
      <div className="avatar"><IconSparkle size={16} /></div>
      <div className="msg-body">
        {waiting && (
          <div className="thinking">
            <span className="dots"><i /><i /><i /></span>
            <span>{m.status || '답변을 준비하는 중'}</span>
          </div>
        )}
        {m.content && <Markdown text={visible} caret={typing} />}
        {m.stopped && <div className="muted small">생성을 중지했어요.</div>}
        {m.error && <div className="error-box"><IconInfo size={16} />{m.error}</div>}

        {!typing && <ResultView result={m.result} />}

        {!typing && m.notices.length > 0 && (
          <div className="notices">
            {m.notices.map((n, i) => <div key={i}><IconInfo size={14} />{n}</div>)}
          </div>
        )}

        {!typing && m.content && (
          <div className="msg-actions">
            <button className="ghost-btn" onClick={copy} title="답변 복사">
              {copied ? <IconCheck size={15} /> : <IconCopy size={15} />}
              <span>{copied ? '복사됨' : '복사'}</span>
            </button>
            {m.plan?.searchTerms?.length > 0 && (
              <span className="chip subtle">검색어 · {m.plan.searchTerms.join(', ')}</span>
            )}
            {m.meta && (
              <span className="meta" title={`해석 ${m.meta.planner} · 답변 ${m.meta.answerer}`}>
                {(m.meta.elapsedMs / 1000).toFixed(1)}초
              </span>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
