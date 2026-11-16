import { useState } from 'react'
import { PROVIDERS, loginUrl } from '../auth.js'
import { IconLogo } from './icons.jsx'

// 제공자 공식 로고 색상 가이드에 맞춘 버튼 (구글: 흰 배경, 네이버: 초록, 카카오: 노랑)
const MARKS = {
  google: (
    <svg width="18" height="18" viewBox="0 0 48 48" aria-hidden="true">
      <path fill="#EA4335" d="M24 9.5c3.5 0 6.6 1.2 9 3.5l6.7-6.7C35.6 2.4 30.2 0 24 0 14.6 0 6.6 5.4 2.7 13.2l7.8 6.1C12.4 13.6 17.7 9.5 24 9.5z" />
      <path fill="#4285F4" d="M46.5 24.5c0-1.6-.1-3.1-.4-4.5H24v9h12.7c-.6 3-2.3 5.5-4.8 7.2l7.6 5.9c4.4-4.1 7-10.1 7-17.6z" />
      <path fill="#FBBC05" d="M10.5 28.7c-.5-1.5-.8-3-.8-4.7s.3-3.2.8-4.7l-7.8-6.1C1 16.5 0 20.1 0 24s1 7.5 2.7 10.8l7.8-6.1z" />
      <path fill="#34A853" d="M24 48c6.5 0 11.9-2.1 15.9-5.8l-7.6-5.9c-2.1 1.4-4.9 2.3-8.3 2.3-6.3 0-11.6-4.1-13.5-9.8l-7.8 6.1C6.6 42.6 14.6 48 24 48z" />
    </svg>
  ),
  naver: (
    <svg width="16" height="16" viewBox="0 0 20 20" aria-hidden="true">
      <path fill="#fff" d="M13.6 10.7 6.1 0H0v20h6.4V9.3L13.9 20H20V0h-6.4z" />
    </svg>
  ),
  kakao: (
    <svg width="18" height="18" viewBox="0 0 24 24" aria-hidden="true">
      <path fill="#191919" d="M12 3C6.5 3 2 6.5 2 10.8c0 2.8 1.9 5.2 4.7 6.6l-1 3.6c-.1.3.3.6.6.4l4.2-2.8c.5.1 1 .1 1.5.1 5.5 0 10-3.5 10-7.9S17.5 3 12 3z" />
    </svg>
  ),
}

export default function LoginScreen({ error }) {
  const [pending, setPending] = useState(null)
  return (
    <div className="login-page">
      <div className="login-card">
        <div className="login-brand">
          <IconLogo size={44} />
          <h1>Expense AI</h1>
          <p>영수증을 찍으면 지출이 정리되고,<br />물어보면 AI 가 답해 드려요.</p>
        </div>
        {error && <div className="error-box small" role="alert">{error}</div>}
        <div className="login-buttons">
          {PROVIDERS.map((p) => (
            <a
              key={p.id}
              className={`social-btn ${p.id} ${pending === p.id ? 'pending' : ''}`}
              href={loginUrl(p.id)}
              onClick={() => setPending(p.id)}
            >
              <span className="social-mark">{MARKS[p.id]}</span>
              <span>{pending === p.id ? '이동 중…' : `${p.name}로 계속하기`}</span>
            </a>
          ))}
        </div>
        <p className="login-note">처음이면 자동으로 가입돼요. 같은 이메일(인증된 이메일)의 계정은 하나로 연결돼요.</p>
      </div>
    </div>
  )
}
