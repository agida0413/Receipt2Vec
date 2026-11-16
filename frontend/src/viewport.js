// 모바일 화면 높이 보정
//  - 모바일 브라우저는 주소창·키보드 때문에 100vh 가 실제 보이는 높이와 다름
//  - iOS Safari 는 키보드가 열려도 레이아웃 높이를 줄이지 않아 입력창이 키보드에 가려짐
//  → 실제 보이는 영역(visualViewport) 높이를 CSS 변수 --app-h 로 넣어 레이아웃이 따라가게 한다
export function installViewportHeight() {
  const vv = window.visualViewport
  const root = document.documentElement
  const apply = () => {
    const h = vv ? vv.height : window.innerHeight
    root.style.setProperty('--app-h', `${Math.round(h)}px`)
    // 키보드가 열린 상태 (보이는 높이가 창 높이보다 많이 작음) → 하단 여백(홈 바) 제거용
    root.classList.toggle('kb-open', !!vv && window.innerHeight - vv.height > 120)
    // iOS 는 키보드를 열면 페이지 전체를 위로 밀어 올림 → 원위치 (레이아웃은 --app-h 로 맞춤)
    if (vv && vv.offsetTop > 0) window.scrollTo(0, 0)
  }
  apply()
  vv?.addEventListener('resize', apply)
  vv?.addEventListener('scroll', apply)
  window.addEventListener('resize', apply)
  window.addEventListener('orientationchange', apply)
}
