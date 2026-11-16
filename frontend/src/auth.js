// 로그인 상태 관리 (auth-server)
//  - refresh token: HttpOnly 쿠키 (자바스크립트에서 읽을 수 없음 → XSS 로 탈취 불가)
//  - access token : 메모리에만 보관. 새로고침하면 쿠키로 다시 발급받음 (localStorage 에 저장하지 않음)
//  - 만료 1분 전에 자동 갱신, API 가 401 을 주면 한 번 갱신 후 재시도

let accessToken = null
let refreshTimer = null
let inflight = null
const listeners = new Set()

export const PROVIDERS = [
  { id: 'google', name: 'Google' },
  { id: 'naver', name: '네이버' },
  { id: 'kakao', name: '카카오' },
]

export function onAuthChange(fn) {
  listeners.add(fn)
  return () => listeners.delete(fn)
}

function emit(user) {
  listeners.forEach((fn) => fn(user))
}

export function loginUrl(provider) {
  const redirect = window.location.pathname || '/'
  return `/oauth2/authorization/${provider}?redirect=${encodeURIComponent(redirect)}`
}

/** 쿠키로 access token 재발급 → user (로그인 안 되어 있으면 null) */
export function refreshSession() {
  if (inflight) return inflight // 동시에 여러 요청이 401 을 받아도 갱신은 한 번만
  inflight = (async () => {
    try {
      const call = () => fetch('/api/auth/token/refresh', { method: 'POST', credentials: 'same-origin' })
      let res = await call()
      if (res.status === 409) {
        // 다른 탭이 방금 토큰을 갱신함 → 갱신된 쿠키로 한 번 더
        await new Promise((r) => setTimeout(r, 600))
        res = await call()
      }
      if (!res.ok) {
        setToken(null)
        return null
      }
      const body = await res.json()
      setToken(body.accessToken, body.expiresIn)
      emit(body.user)
      return body.user
    } catch {
      return null
    } finally {
      inflight = null
    }
  })()
  return inflight
}

function setToken(token, expiresIn) {
  accessToken = token
  clearTimeout(refreshTimer)
  if (token && expiresIn) {
    refreshTimer = setTimeout(refreshSession, Math.max(10, expiresIn - 60) * 1000)
  }
}

export async function logout() {
  try {
    await fetch('/api/auth/logout', {
      method: 'POST',
      credentials: 'same-origin',
      headers: accessToken ? { Authorization: `Bearer ${accessToken}` } : {},
    })
  } finally {
    setToken(null)
    emit(null)
  }
}

/** fetch + Authorization 헤더. 401 이면 토큰을 갱신해 한 번 재시도 */
export async function authFetch(url, options = {}) {
  const withAuth = () => {
    const headers = new Headers(options.headers || {})
    if (accessToken) headers.set('Authorization', `Bearer ${accessToken}`)
    return fetch(url, { ...options, headers })
  }
  let res = await withAuth()
  if (res.status === 401 && accessToken !== null) {
    const user = await refreshSession()
    if (user) res = await withAuth()
    else emit(null)
  }
  return res
}

const LOGIN_ERRORS = {
  access_denied: '로그인을 취소했어요.',
  invalid_state: '로그인 시간이 지났어요. 다시 시도해 주세요.',
  token_exchange_failed: '로그인 제공자와 통신하지 못했어요. 잠시 후 다시 시도해 주세요.',
  profile_failed: '사용자 정보를 가져오지 못했어요.',
  user_blocked: '이용이 제한된 계정이에요.',
  server_error: '로그인 처리 중 오류가 났어요.',
}

export function loginErrorMessage(code) {
  return LOGIN_ERRORS[code] || `로그인하지 못했어요 (${code})`
}
