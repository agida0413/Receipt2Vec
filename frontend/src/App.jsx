import { useCallback, useEffect, useState } from 'react'
import { deleteTransaction, listTransactions, spendingSummary } from './api.js'
import ChatPanel from './components/ChatPanel.jsx'
import { IconMenu, IconPlus, IconUpload } from './components/icons.jsx'
import Sidebar from './components/Sidebar.jsx'
import { useToast } from './components/Toast.jsx'
import UploadDialog from './components/UploadDialog.jsx'

const STORAGE_KEY = 'receipt2vec.userId'

function loadUserId() {
  try {
    return localStorage.getItem(STORAGE_KEY) || 'user01'
  } catch {
    return 'user01'
  }
}

const iso = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`

export default function App() {
  const toast = useToast()
  const [userId, setUserId] = useState(loadUserId)
  const [uploading, setUploading] = useState(false)
  const [menuOpen, setMenuOpen] = useState(false)
  const [chatKey, setChatKey] = useState(0)
  const [txs, setTxs] = useState([])
  const [txLoading, setTxLoading] = useState(false)
  const [txError, setTxError] = useState(null)
  const [summary, setSummary] = useState(null)

  const refresh = useCallback(async () => {
    if (!userId) return
    setTxLoading(true)
    const now = new Date()
    const start = new Date(now.getFullYear(), now.getMonth(), 1)
    const end = new Date(now.getFullYear(), now.getMonth() + 1, 1)
    try {
      const [rows, month] = await Promise.all([
        listTransactions(userId, 50),
        spendingSummary({ userId, startDate: iso(start), endDate: iso(end) }).catch(() => null),
      ])
      setTxs(rows)
      setTxError(null)
      if (month) {
        const groups = (month.groups || []).filter((g) => g.key !== '기타' || month.groups.length === 1)
        setSummary({
          sum: month.aggregates?.sum || 0,
          count: Math.round(month.aggregates?.count || 0),
          top: groups[0]?.key,
          groups: month.groups || [],
        })
      }
    } catch (e) {
      setTxError(e.message)
    } finally {
      setTxLoading(false)
    }
  }, [userId])

  useEffect(() => {
    setSummary(null)
    setTxs([])
    refresh()
  }, [refresh])

  function changeUser(id) {
    if (id === userId) return
    setUserId(id)
    setMenuOpen(false)
    try {
      localStorage.setItem(STORAGE_KEY, id)
    } catch {
      /* 저장 불가 환경 무시 */
    }
    toast(`${id} 님으로 전환했어요`, 'info')
  }

  async function remove(id) {
    try {
      await deleteTransaction(userId, id)
      toast('거래를 삭제했어요', 'success')
      refresh()
    } catch (e) {
      toast(`삭제하지 못했어요: ${e.message}`, 'error')
    }
  }

  const newChat = () => {
    setChatKey((k) => k + 1)
    setMenuOpen(false)
  }

  return (
    <div className="app">
      <Sidebar
        open={menuOpen}
        onClose={() => setMenuOpen(false)}
        userId={userId}
        onChangeUser={changeUser}
        onNewChat={newChat}
        onUpload={() => { setUploading(true); setMenuOpen(false) }}
        txs={txs}
        txLoading={txLoading}
        txError={txError}
        onRefresh={refresh}
        onDelete={remove}
        summary={summary}
      />

      <main className="main">
        <header className="topbar">
          <button className="icon-btn only-mobile" onClick={() => setMenuOpen(true)} aria-label="메뉴 열기"><IconMenu /></button>
          <div className="topbar-title">
            <b>Expense AI</b>
            <span className="status-dot" title="연결됨" />
          </div>
          <div className="topbar-actions">
            <button className="icon-btn" onClick={newChat} aria-label="새 대화" title="새 대화"><IconPlus /></button>
            <button className="btn primary sm hide-mobile" onClick={() => setUploading(true)}><IconUpload size={16} />영수증 추가</button>
          </div>
        </header>
        <ChatPanel userId={userId} resetKey={chatKey} />
      </main>

      {uploading && <UploadDialog userId={userId} onClose={() => setUploading(false)} onSaved={refresh} />}
    </div>
  )
}
