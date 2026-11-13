import { useEffect, useRef, useState } from 'react'
import { STATUS_KO, via, won } from '../format.js'
import { IconChevron, IconClose, IconLogo, IconPlus, IconRefresh, IconTrash, IconUpload } from './icons.jsx'

// 날짜 머리글: 오늘 / 어제 / 11월 3일 (수)
function dayLabel(d) {
  if (!d) return '날짜 미상'
  const date = new Date(`${d}T00:00:00`)
  const today = new Date()
  today.setHours(0, 0, 0, 0)
  const diff = Math.round((today - date) / 86400000)
  if (diff === 0) return '오늘'
  if (diff === 1) return '어제'
  const wd = '일월화수목금토'[date.getDay()]
  const y = date.getFullYear() !== today.getFullYear() ? `${date.getFullYear()}년 ` : ''
  return `${y}${date.getMonth() + 1}월 ${date.getDate()}일 (${wd})`
}

export default function Sidebar({
  open, onClose, userId, onChangeUser, onNewChat, onUpload, txs, txLoading, txError, onRefresh, onDelete, summary,
}) {
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(userId)
  const [confirmId, setConfirmId] = useState(null)
  const inputRef = useRef(null)

  useEffect(() => setDraft(userId), [userId])
  useEffect(() => {
    if (editing) inputRef.current?.select()
  }, [editing])

  const groups = []
  for (const t of txs) {
    const key = t.date || ''
    if (!groups.length || groups[groups.length - 1].key !== key) groups.push({ key, items: [] })
    groups[groups.length - 1].items.push(t)
  }

  function submitUser(e) {
    e.preventDefault()
    const id = draft.trim()
    if (id) onChangeUser(id)
    setEditing(false)
  }

  const now = new Date()
  return (
    <>
      <div className={`sidebar-backdrop ${open ? 'show' : ''}`} onClick={onClose} />
      <aside className={`sidebar ${open ? 'open' : ''}`}>
        <div className="sidebar-top">
          <div className="brand">
            <IconLogo size={30} />
            <div>
              <b>Receipt2Vec</b>
              <span>Expense AI</span>
            </div>
          </div>
          <button className="icon-btn only-mobile" onClick={onClose} aria-label="메뉴 닫기"><IconClose /></button>
        </div>

        <div className="sidebar-actions">
          <button className="btn primary block" onClick={onUpload}><IconUpload size={17} />영수증 추가</button>
          <button className="btn outline block" onClick={onNewChat}><IconPlus size={17} />새 대화</button>
        </div>

        <div className="summary-card">
          <div className="summary-label">{now.getMonth() + 1}월 지출</div>
          {summary ? (
            <>
              <div className="summary-value">{won(summary.sum)}</div>
              <div className="summary-sub">{summary.count}건 결제{summary.top ? ` · 최다 ${summary.top}` : ''}</div>
              {summary.groups?.length > 0 && (
                <div className="summary-bar">
                  {summary.groups.slice(0, 5).map((g, i) => (
                    <span key={g.key} className={`seg s${i}`} style={{ flex: Math.max(g.sum, 1) }} title={`${g.key} ${won(g.sum)}`} />
                  ))}
                </div>
              )}
            </>
          ) : (
            <div className="skeleton" style={{ height: 44 }} />
          )}
        </div>

        <div className="section-head">
          <span>최근 거래</span>
          <button className="icon-btn sm" onClick={onRefresh} aria-label="새로고침" title="새로고침">
            <IconRefresh size={15} className={txLoading ? 'spin' : ''} />
          </button>
        </div>

        <div className="tx-list">
          {txError && <div className="error-box small">{txError}</div>}
          {!txError && txLoading && txs.length === 0 && (
            <>{[0, 1, 2, 3].map((i) => <div key={i} className="skeleton row" />)}</>
          )}
          {!txError && !txLoading && txs.length === 0 && (
            <div className="empty-list">
              <p>아직 저장된 거래가 없어요</p>
              <button className="link-btn" onClick={onUpload}>첫 영수증 추가하기 <IconChevron size={14} /></button>
            </div>
          )}
          {groups.map((g) => (
            <div key={g.key} className="tx-group">
              <div className="tx-day">{dayLabel(g.key)}</div>
              {g.items.map((t) => (
                <div key={t.transactionId} className={`tx-item ${t.status !== 'APPROVED' ? 'is-cancel' : ''}`}>
                  <div className="tx-item-main">
                    <span className="tx-item-name" title={t.merchant}>{t.merchant || '가맹점 미상'}</span>
                    <span className="tx-item-sub">
                      {t.time || ''}{t.category ? ` · ${t.category}` : ''}{via(t) ? ` · ${via(t)}` : ''}
                      {t.status !== 'APPROVED' ? ` · ${STATUS_KO[t.status]}` : ''}
                    </span>
                  </div>
                  <span className="tx-item-amt">{won(t.amount)}</span>
                  {confirmId === t.transactionId ? (
                    <span className="confirm">
                      <button className="mini danger" onClick={() => { onDelete(t.transactionId); setConfirmId(null) }}>삭제</button>
                      <button className="mini" onClick={() => setConfirmId(null)}>취소</button>
                    </span>
                  ) : (
                    <button className="icon-btn sm del" onClick={() => setConfirmId(t.transactionId)} aria-label="삭제" title="삭제">
                      <IconTrash size={14} />
                    </button>
                  )}
                </div>
              ))}
            </div>
          ))}
        </div>

        <div className="profile">
          {editing ? (
            <form onSubmit={submitUser} className="profile-form">
              <input ref={inputRef} value={draft} onChange={(e) => setDraft(e.target.value)} onBlur={submitUser}
                aria-label="사용자 ID" placeholder="사용자 ID" />
            </form>
          ) : (
            <button className="profile-btn" onClick={() => setEditing(true)} title="사용자 전환">
              <span className="profile-avatar">{(userId || '?').slice(0, 1).toUpperCase()}</span>
              <span className="profile-name">
                <b>{userId}</b>
                <span>사용자 전환</span>
              </span>
            </button>
          )}
        </div>
      </aside>
    </>
  )
}
