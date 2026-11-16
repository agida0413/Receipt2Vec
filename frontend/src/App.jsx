import { useCallback, useEffect, useState } from 'react'
import { deleteTransaction, listTransactions, spendingSummary } from './api.js'
import { loginErrorMessage, logout, onAuthChange, refreshSession } from './auth.js'
import ChatPanel from './components/ChatPanel.jsx'
import { IconMenu, IconPlus, IconUpload } from './components/icons.jsx'
import LoginScreen from './components/LoginScreen.jsx'
import Sidebar from './components/Sidebar.jsx'
import { useToast } from './components/Toast.jsx'
import UploadDialog from './components/UploadDialog.jsx'

const iso = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`

/** 로그인 확인 → 로그인 화면 또는 서비스 화면 */
export default function App() {
  const toast = useToast()
  const [user, setUser] = useState(undefined) // undefined = 확인 중, null = 로그인 필요
  const [loginError, setLoginError] = useState(null)

  useEffect(() => {
    // 소셜 로그인 콜백 후 돌아온 경우: ?login=success / ?login_error=코드 → 안내 후 주소 정리
    const params = new URLSearchParams(window.location.search)
    const err = params.get('login_error')
    const success = params.get('login') === 'success'
    if (err || success) {
      params.delete('login')
      params.delete('login_error')
      const rest = params.toString()
      window.history.replaceState(null, '', window.location.pathname + (rest ? `?${rest}` : ''))
    }
    if (err) setLoginError(loginErrorMessage(err))
    const off = onAuthChange((u) => setUser(u))
    refreshSession().then((u) => {
      setUser(u)
      if (u && success) toast(`${u.name || '사용자'} 님, 환영해요`, 'success')
    })
    return off
  }, [toast])

  async function signOut() {
    await logout()
    toast('로그아웃했어요', 'info')
  }

  if (user === undefined) return <div className="boot-screen">불러오는 중…</div>
  if (!user) return <LoginScreen error={loginError} />
  return <Workspace user={user} onLogout={signOut} />
}

function Workspace({ user, onLogout }) {
  const toast = useToast()
  const userId = user.id // 거래·챗봇 데이터는 로그인 계정 ID 로 구분
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
        user={user}
        onLogout={onLogout}
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
