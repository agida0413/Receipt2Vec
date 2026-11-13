import { useEffect, useRef, useState } from 'react'
import { recognizeReceipt, saveTransactions } from '../api.js'
import { METHOD_KO, REVIEW_REASON_KO, STATUS_KO, won } from '../format.js'
import { IconAlert, IconCheck, IconClose, IconImage, IconSparkle, IconUpload } from './icons.jsx'
import { useToast } from './Toast.jsx'

const STEPS = ['사진 선택', '인식 결과 확인', '저장 완료']
const RECOGNIZE_MESSAGES = ['사진을 불러오는 중', '글자를 읽는 중', '가맹점·금액을 찾는 중', '항목을 정리하는 중']

// 영수증 업로드 → OCR → 사용자 확인/수정 → 저장
export default function UploadDialog({ userId, onClose, onSaved }) {
  const toast = useToast()
  const [file, setFile] = useState(null)
  const [preview, setPreview] = useState(null)
  const [forceLlm, setForceLlm] = useState(false)
  const [phase, setPhase] = useState('select') // select | recognizing | review | saving | done
  const [error, setError] = useState(null)
  const [ocr, setOcr] = useState(null)
  const [rows, setRows] = useState([])
  const [saveResult, setSaveResult] = useState(null)
  const [dragging, setDragging] = useState(false)
  const [msgIndex, setMsgIndex] = useState(0)
  const fileInput = useRef(null)

  const busy = phase === 'recognizing' || phase === 'saving'
  const step = phase === 'done' ? 2 : phase === 'review' || phase === 'saving' ? 1 : 0

  useEffect(() => {
    if (!file) return undefined
    const url = URL.createObjectURL(file)
    setPreview(url)
    return () => URL.revokeObjectURL(url)
  }, [file])

  // 인식 중 안내 문구 순환
  useEffect(() => {
    if (phase !== 'recognizing') return undefined
    setMsgIndex(0)
    const t = setInterval(() => setMsgIndex((i) => Math.min(i + 1, RECOGNIZE_MESSAGES.length - 1)), 2200)
    return () => clearInterval(t)
  }, [phase])

  // ESC 로 닫기 (처리 중에는 막음)
  useEffect(() => {
    const onKey = (e) => e.key === 'Escape' && !busy && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [busy, onClose])

  function pick(f) {
    if (!f) return
    if (!f.type.startsWith('image/')) {
      setError('이미지 파일만 올릴 수 있어요.')
      return
    }
    setError(null)
    setFile(f)
  }

  async function recognize() {
    setPhase('recognizing')
    setError(null)
    try {
      const res = await recognizeReceipt({ file, userId, forceLlm })
      setOcr(res)
      setRows(res.transactions.map(toEditable))
      setPhase('review')
    } catch (e) {
      setError(e.message)
      setPhase('select')
    }
  }

  async function save() {
    const selected = rows.filter((r) => r.include)
    if (!selected.length) return
    setPhase('saving')
    setError(null)
    try {
      const res = await saveTransactions({ userId, transactions: selected.map(fromEditable) })
      setSaveResult(res)
      setPhase('done')
      onSaved?.()
      toast(
        res.saved.length
          ? `거래 ${res.saved.length}건을 저장했어요${res.duplicates.length ? ` (중복 ${res.duplicates.length}건 제외)` : ''}`
          : '이미 저장된 거래예요',
        res.saved.length ? 'success' : 'info',
      )
    } catch (e) {
      setError(e.message)
      setPhase('review')
    }
  }

  function restart() {
    setFile(null)
    setPreview(null)
    setOcr(null)
    setRows([])
    setSaveResult(null)
    setError(null)
    setPhase('select')
  }

  const update = (i, field, value) => setRows((rs) => rs.map((r, j) => (j === i ? { ...r, [field]: value } : r)))
  const invalid = (r) => !r.merchant.trim() || !r.date || !(Number(r.amount) > 0)
  const selectedCount = rows.filter((r) => r.include).length

  return (
    <div className="modal-backdrop" onMouseDown={(e) => e.target === e.currentTarget && !busy && onClose()}>
      <div className="modal" role="dialog" aria-modal="true" aria-labelledby="upload-title">
        <div className="modal-head">
          <div>
            <h3 id="upload-title">영수증 · 결제내역 추가</h3>
            <div className="steps">
              {STEPS.map((s, i) => (
                <span key={s} className={`step ${i < step ? 'done' : ''} ${i === step ? 'active' : ''}`}>
                  <i>{i < step ? <IconCheck size={12} /> : i + 1}</i>{s}
                </span>
              ))}
            </div>
          </div>
          <button className="icon-btn" onClick={onClose} disabled={busy} aria-label="닫기"><IconClose /></button>
        </div>

        <div className="modal-body">
          {error && <div className="error-box"><IconAlert size={16} />{error}</div>}

          {(phase === 'select' || phase === 'recognizing') && (
            <>
              <div
                className={`dropzone ${dragging ? 'dragging' : ''} ${preview ? 'has-file' : ''}`}
                onClick={() => !busy && fileInput.current?.click()}
                onDragOver={(e) => { e.preventDefault(); setDragging(true) }}
                onDragLeave={() => setDragging(false)}
                onDrop={(e) => { e.preventDefault(); setDragging(false); if (!busy) pick(e.dataTransfer.files?.[0]) }}
                role="button"
                tabIndex={0}
              >
                <input ref={fileInput} type="file" accept="image/*" hidden onChange={(e) => pick(e.target.files?.[0])} />
                {preview ? (
                  <div className="preview">
                    <img src={preview} alt="선택한 사진" />
                    {phase === 'recognizing' && (
                      <div className="scan-overlay">
                        <div className="scan-line" />
                        <div className="scan-status">
                          <span className="spinner" />
                          <span>{RECOGNIZE_MESSAGES[msgIndex]}…</span>
                        </div>
                      </div>
                    )}
                  </div>
                ) : (
                  <div className="drop-empty">
                    <span className="drop-icon"><IconUpload size={26} /></span>
                    <b>사진을 끌어다 놓거나 클릭해서 선택하세요</b>
                    <span className="muted small">종이 영수증, 카카오페이·토스 결제내역 캡처 (JPG·PNG)</span>
                  </div>
                )}
              </div>

              {file && (
                <div className="file-line">
                  <IconImage size={16} />
                  <span className="file-name">{file.name}</span>
                  <span className="muted small">{(file.size / 1024 / 1024).toFixed(1)}MB</span>
                  {!busy && <button className="link-btn" onClick={() => fileInput.current?.click()}>다른 사진</button>}
                </div>
              )}

              <label className="toggle">
                <input type="checkbox" checked={forceLlm} disabled={busy} onChange={(e) => setForceLlm(e.target.checked)} />
                <span className="toggle-track"><span /></span>
                <span>
                  <b>AI로 정밀 추출</b>
                  <span className="muted small"> · 복잡한 영수증에 유리하지만 조금 더 걸려요</span>
                </span>
              </label>
            </>
          )}

          {(phase === 'review' || phase === 'saving') && ocr && (
            <div className="review">
              <div className="review-summary">
                <span className="chip">{ocr.documentType === 'receipt' ? '영수증' : '결제앱 화면'}</span>
                <span className="chip">{ocr.extraction.method === 'llm' ? <><IconSparkle size={12} /> AI 추출</> : '자동 추출'}</span>
                <span className="muted small">
                  거래 {rows.length}건 · 인식 정확도 {ocr.ocrScore != null ? `${Math.round(ocr.ocrScore * 100)}%` : '-'}
                  · {(ocr.elapsedMs / 1000).toFixed(1)}초
                </span>
              </div>
              {(ocr.extraction.llmError || ocr.extraction.llmSkippedReason) && (
                <div className="notice-line"><IconAlert size={14} />{ocr.extraction.llmError || ocr.extraction.llmSkippedReason}</div>
              )}
              {rows.length === 0 && <div className="error-box"><IconAlert size={16} />거래를 찾지 못했어요. 다른 사진으로 시도해 주세요.</div>}

              <p className="muted small review-guide">인식된 내용을 확인하고, 틀린 값은 바로 고쳐 주세요.</p>

              {rows.map((r, i) => (
                <div className={`tx-card ${r.include ? '' : 'excluded'}`} key={r.transactionId}>
                  <div className="tx-card-head">
                    <label className="check">
                      <input type="checkbox" checked={r.include} disabled={busy}
                        onChange={(e) => update(i, 'include', e.target.checked)} />
                      <span className="tx-card-title">{r.merchant || '가맹점 미상'}</span>
                    </label>
                    <span className="tx-card-amount">{r.amount ? won(Number(r.amount)) : '-'}</span>
                  </div>
                  {r.reasons.length > 0 && (
                    <div className="reasons">
                      {r.reasons.map((x) => <span key={x} className="badge warn">{REVIEW_REASON_KO[x] || x}</span>)}
                    </div>
                  )}
                  <div className="grid">
                    <Field label="가맹점" wide>
                      <input value={r.merchant} disabled={busy} onChange={(e) => update(i, 'merchant', e.target.value)} />
                    </Field>
                    <Field label="날짜">
                      <input type="date" value={r.date} disabled={busy} onChange={(e) => update(i, 'date', e.target.value)} />
                    </Field>
                    <Field label="시간">
                      <input type="time" step="1" value={r.time} disabled={busy} onChange={(e) => update(i, 'time', e.target.value)} />
                    </Field>
                    <Field label="금액(원)">
                      <input inputMode="numeric" value={r.amount} disabled={busy}
                        onChange={(e) => update(i, 'amount', e.target.value.replace(/[^\d]/g, ''))} />
                    </Field>
                    <Field label="결제수단">
                      <select value={r.method} disabled={busy} onChange={(e) => update(i, 'method', e.target.value)}>
                        {Object.entries(METHOD_KO).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
                      </select>
                    </Field>
                    <Field label="상태">
                      <select value={r.status} disabled={busy} onChange={(e) => update(i, 'status', e.target.value)}>
                        {Object.entries(STATUS_KO).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
                      </select>
                    </Field>
                  </div>
                  {r.items.length > 0 && (
                    <div className="item-chips">
                      {r.items.map((it, k) => (
                        <span key={k} className="item-chip">
                          {it.name}{it.quantity > 1 ? ` ×${it.quantity}` : ''}<b>{it.amount ? won(it.amount) : '가격 없음'}</b>
                        </span>
                      ))}
                    </div>
                  )}
                  {r.include && invalid(r) && <div className="field-error">가맹점·날짜·금액은 꼭 필요해요.</div>}
                </div>
              ))}
              <details className="raw">
                <summary>인식된 원문 보기</summary>
                <pre>{ocr.rawText}</pre>
              </details>
            </div>
          )}

          {phase === 'done' && saveResult && (
            <div className="done-screen">
              <div className="done-check"><IconCheck size={30} /></div>
              <h4>{saveResult.saved.length ? '저장했어요' : '이미 저장된 거래예요'}</h4>
              <p className="muted">
                {saveResult.saved.length > 0 && <>거래 <b>{saveResult.saved.length}건</b>을 추가했어요. </>}
                {saveResult.duplicates.length > 0 && <>이미 있던 {saveResult.duplicates.length}건은 건너뛰었어요.</>}
                <br />이제 챗봇에게 물어보세요.
              </p>
            </div>
          )}
        </div>

        <div className="modal-foot">
          {(phase === 'select' || phase === 'recognizing') && (
            <>
              <button className="btn ghost" onClick={onClose} disabled={busy}>취소</button>
              <button className="btn primary" disabled={!file || busy} onClick={recognize}>
                {phase === 'recognizing' ? <><span className="spinner sm" />인식하는 중</> : '인식하기'}
              </button>
            </>
          )}
          {(phase === 'review' || phase === 'saving') && (
            <>
              <button className="btn ghost" onClick={restart} disabled={busy}>다시 선택</button>
              <button
                className="btn primary"
                onClick={save}
                disabled={busy || !selectedCount || rows.some((r) => r.include && invalid(r))}
              >
                {phase === 'saving' ? <><span className="spinner sm" />저장하는 중</> : `${selectedCount}건 저장하기`}
              </button>
            </>
          )}
          {phase === 'done' && (
            <>
              <button className="btn ghost" onClick={restart}>계속 추가하기</button>
              <button className="btn primary" onClick={onClose}>완료</button>
            </>
          )}
        </div>

        {phase === 'saving' && (
          <div className="modal-overlay">
            <span className="spinner lg" />
            <span>거래를 저장하는 중이에요</span>
          </div>
        )}
      </div>
    </div>
  )
}

function Field({ label, wide, children }) {
  return (
    <label className={`field ${wide ? 'wide' : ''}`}>
      <span>{label}</span>
      {children}
    </label>
  )
}

// 서버 Transaction ↔ 편집 폼
function toEditable(tx) {
  const at = tx.transactedAt || ''
  return {
    transactionId: tx.transactionId,
    original: tx,
    include: true,
    merchant: tx.merchant?.name || '',
    date: tx.transactedDate || '',
    time: at.length >= 19 ? at.slice(11, 19) : '',
    amount: tx.amount?.total != null ? String(tx.amount.total) : '',
    method: tx.payment?.method || 'UNKNOWN',
    status: tx.status || 'APPROVED',
    reasons: tx.quality?.reviewReasons || [],
    items: tx.items || [],
  }
}

function fromEditable(r) {
  const tx = structuredClone(r.original)
  tx.merchant = { ...tx.merchant, name: r.merchant.trim() }
  tx.transactedDate = r.date || null
  const time = r.time && r.time.length === 5 ? `${r.time}:00` : r.time
  tx.transactedAt = r.date && time ? `${r.date}T${time}+09:00` : null
  tx.amount = { ...tx.amount, total: r.amount ? Number(r.amount) : null }
  tx.payment = { ...tx.payment, method: r.method }
  tx.status = r.status
  return tx
}
