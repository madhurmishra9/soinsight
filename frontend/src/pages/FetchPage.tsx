import { useEffect, useRef, useState } from 'react'
import { Download, Loader, Plus, X } from 'lucide-react'
import { fetchQuestions, questionStreamUrl } from '../api'
import { connectSSE } from '../api/sse'
import { errorMessage } from '../api/client'
import { useApp } from '../context/AppContext'
import type { SseEvent } from '../types/api'

const WINDOWS = [7, 14, 30, 60, 90]

interface LogEntry {
  ts: string
  msg: string
  kind: 'info' | 'done' | 'error'
}

function now() {
  return new Date().toLocaleTimeString('en-GB', { hour12: false })
}

function eventToEntry(ev: SseEvent): LogEntry {
  if (ev.type === 'progress') {
    return { ts: now(), msg: String(ev.message ?? JSON.stringify(ev)), kind: 'info' }
  }
  if (ev.type === 'tag_done' || ev.type === 'page_done') {
    const detail = Object.entries(ev)
      .filter(([k]) => k !== 'type')
      .map(([k, v]) => `${k}=${String(v)}`)
      .join(' ')
    return { ts: now(), msg: detail, kind: 'info' }
  }
  const detail = Object.entries(ev)
    .filter(([k]) => k !== 'type')
    .map(([k, v]) => `${k}=${String(v)}`)
    .join(' ')
  return { ts: now(), msg: `[${ev.type}] ${detail}`, kind: 'info' }
}

export function FetchPage() {
  const { knownProducts, addProducts } = useApp()
  const [tags, setTags] = useState<string[]>(knownProducts.length ? knownProducts : [])

  useEffect(() => {
    setTags((prev) => (prev.length === 0 && knownProducts.length > 0 ? knownProducts : prev))
  }, [knownProducts])
  const [tagInput, setTagInput] = useState('')
  const [windowDays, setWindowDays] = useState(30)
  const [fromDate, setFromDate] = useState('')
  const [toDate, setToDate] = useState('')
  const [incremental, setIncremental] = useState(true)
  const [running, setRunning] = useState(false)
  const [log, setLog] = useState<LogEntry[]>([])
  const [error, setError] = useState<string | null>(null)
  const logRef = useRef<HTMLDivElement>(null)
  const cleanupRef = useRef<(() => void) | null>(null)

  useEffect(() => () => { cleanupRef.current?.() }, [])

  useEffect(() => {
    logRef.current?.scrollTo({ top: logRef.current.scrollHeight, behavior: 'smooth' })
  }, [log])

  const addTag = () => {
    const t = tagInput.trim().toLowerCase()
    if (t && !tags.includes(t)) setTags((prev) => [...prev, t])
    setTagInput('')
  }

  const removeTag = (tag: string) => setTags((prev) => prev.filter((t) => t !== tag))

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' || e.key === ',') { e.preventDefault(); addTag() }
    if (e.key === 'Backspace' && tagInput === '' && tags.length) {
      setTags((prev) => prev.slice(0, -1))
    }
  }

  const handleFetch = async () => {
    if (!tags.length) return
    setRunning(true)
    setError(null)
    const rangeLabel = fromDate && toDate
      ? `${fromDate} → ${toDate}`
      : fromDate
      ? `${fromDate} → now`
      : `last ${windowDays}d`
    setLog([{ ts: now(), msg: `Starting fetch for [${tags.join(', ')}] over ${rangeLabel}…`, kind: 'info' }])

    try {
      const res = await fetchQuestions({ products: tags, window_days: windowDays, from_date: fromDate || undefined, to_date: toDate || undefined, incremental })
      const { run_id } = res.data
      addProducts(tags)

      setLog((l) => [...l, { ts: now(), msg: `Run ${run_id} started, streaming…`, kind: 'info' }])

      cleanupRef.current?.()
      cleanupRef.current = connectSSE(
        questionStreamUrl(run_id),
        (ev) => setLog((l) => [...l, eventToEntry(ev)]),
        () => {
          setLog((l) => [...l, { ts: now(), msg: '✓ Fetch complete.', kind: 'done' }])
          setRunning(false)
        },
        (msg) => {
          setLog((l) => [...l, { ts: now(), msg: `Error: ${msg}`, kind: 'error' }])
          setError(msg)
          setRunning(false)
        },
      )
    } catch (err) {
      const msg = errorMessage(err)
      setError(msg)
      setLog((l) => [...l, { ts: now(), msg: `Error: ${msg}`, kind: 'error' }])
      setRunning(false)
    }
  }

  return (
    <>
      <div className="page-header">
        <div className="page-title">Fetch Questions</div>
        <div className="page-subtitle">Pull tagged questions from Stack Overflow Enterprise into the local DB</div>
      </div>

      <div className="card" style={{ maxWidth: 680 }}>
        <div className="card-title">Products / tags</div>

        <div className="form-group">
          <label>Tags to ingest <span className="hint">press Enter or comma to add</span></label>
          <div className="tag-input-wrap" onClick={() => document.getElementById('tag-bare')?.focus()}>
            {tags.map((t) => (
              <span key={t} className="tag-chip">
                {t}
                <button type="button" onClick={() => removeTag(t)}><X size={10} /></button>
              </span>
            ))}
            <input
              id="tag-bare"
              className="tag-bare-input"
              placeholder={tags.length ? '' : 'e.g. python, api-gateway…'}
              value={tagInput}
              onChange={(e) => setTagInput(e.target.value)}
              onKeyDown={handleKeyDown}
              onBlur={addTag}
              disabled={running}
            />
          </div>
        </div>

        <div className="form-group">
          <label>Time window</label>
          <div className="window-tabs">
            {WINDOWS.map((w) => (
              <button
                key={w}
                className={`window-tab${windowDays === w && !fromDate && !toDate ? ' active' : ''}`}
                onClick={() => { setWindowDays(w); setFromDate(''); setToDate('') }}
                disabled={running}
              >
                {w}d
              </button>
            ))}
          </div>
        </div>

        <div className="form-group">
          <label>Custom date range <span className="hint">overrides the window when set</span></label>
          <div className="flex items-center gap-12" style={{ flexWrap: 'wrap' }}>
            <input className="input" type="date" value={fromDate} onChange={(e) => setFromDate(e.target.value)} disabled={running} />
            <input className="input" type="date" value={toDate} onChange={(e) => setToDate(e.target.value)} disabled={running} />
          </div>
        </div>

        <div className="form-group">
          <label style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer' }}>
            <input
              type="checkbox"
              checked={incremental}
              onChange={(e) => setIncremental(e.target.checked)}
              disabled={running}
            />
            <span>Incremental fetch <span className="hint">(only questions newer than last fetch per tag — much faster)</span></span>
          </label>
          {!incremental && (
            <div className="alert alert-info" style={{ marginTop: 6 }}>
              Full fetch: will re-download all questions in the selected date range.
            </div>
          )}
        </div>

        {error && <div className="alert alert-error">{error}</div>}

        <button
          className="btn btn-primary"
          onClick={handleFetch}
          disabled={running || !tags.length}
        >
          {running ? <Loader size={14} className="spin" /> : <Download size={14} />}
          {running ? 'Fetching…' : 'Fetch questions'}
        </button>
      </div>

      {log.length > 0 && (
        <div className="card" style={{ maxWidth: 680, marginTop: 16 }}>
          <div className="card-title" style={{ marginBottom: 10 }}>Progress</div>
          <div className="progress-log" ref={logRef}>
            {log.map((entry, i) => (
              <div key={i} className={`progress-event ev-${entry.kind}`}>
                <span className="ts">{entry.ts}</span>
                <span className="msg">{entry.msg}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {!tags.length && knownProducts.length === 0 && (
        <div className="card" style={{ maxWidth: 680, marginTop: 16 }}>
          <div className="alert alert-info" style={{ marginBottom: 0 }}>
            <Plus size={14} />
            Type a product tag above (e.g. <code>python</code>, <code>api-gateway</code>) and press Enter to add it.
          </div>
        </div>
      )}
    </>
  )
}
