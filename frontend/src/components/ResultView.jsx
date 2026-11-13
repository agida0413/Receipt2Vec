import { useState } from 'react'
import { METHOD_KO, PROVIDER_KO, STATUS_KO, shortDate, via, won } from '../format.js'
import { IconChevron } from './icons.jsx'

// 검색어가 있을 때 어떤 단계에서 찾았는지 (rag_backend 의 단계적 완화 검색)
const MATCH_LABEL = {
  RELAXED: { label: '일부 조건 일치', title: '모든 검색 조건에 맞는 거래가 없어 일부 조건만 맞는 거래를 보여줍니다' },
  SEMANTIC: { label: '비슷한 거래', title: '이름이 일치하는 거래가 없어 의미가 비슷한 거래를 찾았습니다' },
}

// 챗봇 답변 아래 '근거 데이터' 카드 (집계 숫자 + 그룹 막대 + 조회된 거래)
export default function ResultView({ result }) {
  const [open, setOpen] = useState(false)
  if (!result || result.total === 0) return null
  const { intent, aggregates = {}, groups = [], transactions = [], total, periodLabel, matchType } = result
  const maxGroup = Math.max(1, ...groups.map((g) => g.sum || 0))
  const groupName = (k) => METHOD_KO[k] || PROVIDER_KO[k] || k
  const match = MATCH_LABEL[matchType]
  // 품목 질문('담배에 쓴 돈', '뭐 많이 폈지')은 거래 전체 금액이 아니라 품목 기준 숫자를 보여준다
  const ia = result.itemAggregates || {}
  const itemBased = intent === 'AGGREGATE' && (aggregates.quantity != null || ia.sum != null)

  let stats = []
  if (itemBased) {
    stats = [
      { label: '품목 금액', value: won(aggregates.quantity != null ? aggregates.sum : ia.sum) },
      { label: '수량', value: `${Math.round((aggregates.quantity ?? ia.quantity) || 0)}개` },
      { label: '결제', value: `${total}건` },
    ]
  } else if (intent === 'AGGREGATE') {
    stats = [
      { label: '합계', value: won(aggregates.sum) },
      { label: '건수', value: `${Math.round(aggregates.count ?? 0)}건` },
      aggregates.dailyAvg != null
        ? { label: '하루 평균', value: won(aggregates.dailyAvg) }
        : { label: '평균', value: won(aggregates.avg ?? (aggregates.count ? aggregates.sum / aggregates.count : null)) },
      aggregates.max != null ? { label: '최대', value: won(aggregates.max) } : null,
    ].filter(Boolean)
  }

  return (
    <div className="result-card">
      <div className="result-head">
        <span className="chip">{periodLabel}</span>
        <span className="muted small">조건에 맞는 거래 {total}건</span>
        {match && <span className="chip warn" title={match.title}>{match.label}</span>}
      </div>

      {stats.length > 0 && (
        <div className="stats">
          {stats.map((s) => (
            <div className="stat" key={s.label}>
              <div className="stat-label">{s.label}</div>
              <div className="stat-value">{s.value}</div>
            </div>
          ))}
        </div>
      )}

      {groups.length > 0 && (
        <div className="groups">
          {groups.slice(0, 6).map((g) => (
            <div className="group-row" key={g.key}>
              <span className="group-name" title={groupName(g.key)}>{groupName(g.key)}</span>
              <span className="bar"><span style={{ width: `${Math.max(3, ((g.sum || 0) / maxGroup) * 100)}%` }} /></span>
              <span className="group-val">{won(g.sum)} <span className="muted">· {g.count}{g.lines != null ? '개' : '건'}</span></span>
            </div>
          ))}
        </div>
      )}

      {transactions.length > 0 && (
        <>
          <button className={`disclosure ${open ? 'open' : ''}`} onClick={() => setOpen((v) => !v)}>
            <IconChevron size={14} />
            {open ? '근거 거래 접기' : `근거 거래 ${transactions.length}건 보기`}
          </button>
          {open && (
            <div className="tx-rows">
              {transactions.map((t) => (
                <div key={t.transactionId} className={`tx-row ${t.status !== 'APPROVED' ? 'is-cancel' : ''}`}>
                  <div className="tx-date">
                    <b>{shortDate(t.date)}</b>
                    <span>{t.time || ''}</span>
                  </div>
                  <div className="tx-info">
                    <div className="tx-name">
                      {t.merchant || '가맹점 미상'}
                      {t.category && <span className="tag">{t.category}</span>}
                    </div>
                    {(t.matchedItems?.length > 0 || t.items?.length > 0) && (
                      <div className="tx-items">{(t.matchedItems?.length ? t.matchedItems : t.items).slice(0, 4).join(' · ')}</div>
                    )}
                  </div>
                  <div className="tx-amt">
                    <b>{won(t.amount)}</b>
                    <span>{via(t)}{t.status !== 'APPROVED' ? ` · ${STATUS_KO[t.status]}` : ''}</span>
                  </div>
                </div>
              ))}
            </div>
          )}
        </>
      )}
    </div>
  )
}
