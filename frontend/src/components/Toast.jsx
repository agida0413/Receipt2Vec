import { createContext, useCallback, useContext, useRef, useState } from 'react'
import { IconAlert, IconCheck, IconInfo } from './icons.jsx'

const ToastContext = createContext(() => {})

export function useToast() {
  return useContext(ToastContext)
}

// 화면 하단 알림 (저장 완료, 오류 등). toast('메시지', 'success' | 'error' | 'info')
export function ToastProvider({ children }) {
  const [items, setItems] = useState([])
  const seq = useRef(0)

  const toast = useCallback((message, kind = 'info') => {
    const id = ++seq.current
    setItems((xs) => [...xs, { id, message, kind }])
    setTimeout(() => setItems((xs) => xs.filter((x) => x.id !== id)), 3500)
  }, [])

  return (
    <ToastContext.Provider value={toast}>
      {children}
      <div className="toasts" role="status" aria-live="polite">
        {items.map((t) => (
          <div key={t.id} className={`toast ${t.kind}`}>
            {t.kind === 'success' ? <IconCheck size={16} /> : t.kind === 'error' ? <IconAlert size={16} /> : <IconInfo size={16} />}
            <span>{t.message}</span>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  )
}
