// LLM 답변용 아주 작은 마크다운 렌더러: 문단, 글머리 목록(- * •), 번호 목록, **굵게**
// (외부 라이브러리 없이 안전하게: HTML 을 해석하지 않고 React 요소로만 만든다)

function inline(text, keyBase) {
  const parts = []
  const re = /\*\*(.+?)\*\*/g
  let last = 0
  let m
  let i = 0
  while ((m = re.exec(text))) {
    if (m.index > last) parts.push(text.slice(last, m.index))
    parts.push(<strong key={`${keyBase}-b${i++}`}>{m[1]}</strong>)
    last = m.index + m[0].length
  }
  if (last < text.length) parts.push(text.slice(last))
  return parts
}

export default function Markdown({ text, caret }) {
  const lines = (text || '').replace(/\r\n/g, '\n').split('\n')
  const blocks = []
  let list = null

  const flush = () => {
    if (list) blocks.push(list)
    list = null
  }

  lines.forEach((raw, idx) => {
    const line = raw.trimEnd()
    const bullet = line.match(/^\s*(?:[-*•])\s+(.*)$/)
    const ordered = line.match(/^\s*(\d+)[.)]\s+(.*)$/)
    if (bullet || ordered) {
      const type = bullet ? 'ul' : 'ol'
      if (!list || list.type !== type) {
        flush()
        list = { type, items: [] }
      }
      list.items.push({ key: idx, text: bullet ? bullet[1] : ordered[2] })
      return
    }
    flush()
    if (line.trim()) blocks.push({ type: 'p', key: idx, text: line.replace(/^#+\s*/, '') })
  })
  flush()

  const lastIndex = blocks.length - 1
  const caretEl = caret ? <span className="caret" /> : null

  if (blocks.length === 0) return <div className="md">{caretEl}</div>

  return (
    <div className="md">
      {blocks.map((b, bi) => {
        const isLast = bi === lastIndex
        if (b.type === 'p') {
          return <p key={b.key}>{inline(b.text, b.key)}{isLast && caretEl}</p>
        }
        const Tag = b.type
        return (
          <Tag key={`l${bi}`}>
            {b.items.map((it, ii) => (
              <li key={it.key}>{inline(it.text, it.key)}{isLast && ii === b.items.length - 1 && caretEl}</li>
            ))}
          </Tag>
        )
      })}
    </div>
  )
}
