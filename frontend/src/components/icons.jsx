// 인라인 SVG 아이콘 (외부 의존성 없음). stroke 는 currentColor 를 따른다.
const base = { width: 18, height: 18, viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', strokeWidth: 1.8,
  strokeLinecap: 'round', strokeLinejoin: 'round' }

const make = (paths) => function Icon({ size = 18, className, ...rest }) {
  return (
    <svg {...base} width={size} height={size} className={className} aria-hidden="true" {...rest}>
      {paths}
    </svg>
  )
}

export const IconReceipt = make(<>
  <path d="M6 3h12v18l-3-2-3 2-3-2-3 2z" /><path d="M9 8h6M9 12h6M9 16h3" />
</>)
export const IconPlus = make(<path d="M12 5v14M5 12h14" />)
export const IconUpload = make(<>
  <path d="M12 16V4M7 9l5-5 5 5" /><path d="M4 16v3a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-3" />
</>)
export const IconSend = make(<path d="M12 19V5M5 12l7-7 7 7" />)
export const IconStop = (props) => (
  <svg width={props.size || 14} height={props.size || 14} viewBox="0 0 24 24" aria-hidden="true">
    <rect x="5" y="5" width="14" height="14" rx="2.5" fill="currentColor" />
  </svg>
)
export const IconMenu = make(<path d="M4 6h16M4 12h16M4 18h16" />)
export const IconClose = make(<path d="M6 6l12 12M18 6L6 18" />)
export const IconRefresh = make(<>
  <path d="M20 11a8 8 0 0 0-14.9-3.9L4 9" /><path d="M4 4v5h5" />
  <path d="M4 13a8 8 0 0 0 14.9 3.9L20 15" /><path d="M20 20v-5h-5" />
</>)
export const IconTrash = make(<>
  <path d="M4 7h16M10 11v6M14 11v6" /><path d="M6 7l1 13h10l1-13M9 7V4h6v3" />
</>)
export const IconCopy = make(<>
  <rect x="9" y="9" width="11" height="11" rx="2" /><path d="M5 15V5a1 1 0 0 1 1-1h10" />
</>)
export const IconCheck = make(<path d="M5 12.5l4.5 4.5L19 7.5" />)
export const IconSparkle = make(<>
  <path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z" /><path d="M19 15l.7 2 2 .7-2 .7-.7 2-.7-2-2-.7 2-.7z" />
</>)
export const IconChevron = make(<path d="M9 6l6 6-6 6" />)
export const IconImage = make(<>
  <rect x="3" y="4" width="18" height="16" rx="2" /><circle cx="9" cy="10" r="2" /><path d="M21 16l-5-5-9 9" />
</>)
export const IconWallet = make(<>
  <path d="M4 7a2 2 0 0 1 2-2h12v4" /><rect x="3" y="7" width="18" height="13" rx="2" /><path d="M16 13.5h2" />
</>)
export const IconCoffee = make(<>
  <path d="M4 9h13v5a5 5 0 0 1-5 5H9a5 5 0 0 1-5-5z" /><path d="M17 11h1.5a2.5 2.5 0 0 1 0 5H17" /><path d="M8 3v3M12 3v3" />
</>)
export const IconStore = make(<>
  <path d="M4 10v10h16V10" /><path d="M3 10l2-6h14l2 6z" /><path d="M10 20v-5h4v5" />
</>)
export const IconChart = make(<path d="M5 20V10M12 20V4M19 20v-7" />)
export const IconSearch = make(<><circle cx="11" cy="11" r="6.5" /><path d="M20 20l-4-4" /></>)
export const IconInfo = make(<><circle cx="12" cy="12" r="9" /><path d="M12 11v5M12 8h.01" /></>)
export const IconAlert = make(<><path d="M12 4l9 16H3z" /><path d="M12 10v4M12 17h.01" /></>)
export const IconUser = make(<><circle cx="12" cy="8" r="4" /><path d="M4 21a8 8 0 0 1 16 0" /></>)
export const IconLogo = ({ size = 28 }) => (
  <svg width={size} height={size} viewBox="0 0 32 32" aria-hidden="true">
    <rect width="32" height="32" rx="9" fill="var(--brand)" />
    <path d="M10 8h12v16l-2.5-1.6L17 24l-2.5-1.6L12 24l-2-1.3z" fill="#fff" opacity=".95" />
    <path d="M13 13h6M13 16.5h6M13 20h3" stroke="var(--brand)" strokeWidth="1.6" strokeLinecap="round" />
  </svg>
)
