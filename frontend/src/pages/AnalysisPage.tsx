import { useEffect, useRef, useState } from 'react'
import { BarChart2, Loader, X } from 'lucide-react'
import { startAnalysis, analysisStreamUrl } from '../api'
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

interface TagSummary {
  tag: string
  patterns: number
  total: number
  noise: number
}

function now() {
  return new Date().toLocaleTimeString('en-GB', { hour12: false })
}

export function AnalysisPage() {
  const { knownProducts, addProducts } = useApp()
  const [tags, setTags] = useState<string[]>(knownProducts)
  const [tagInput, setTagInput] = useState('')
  const [windowDays, setWindowDays] = useState(30)
  const [running, setRunning] = useState(false)
  const [log, setLog] = useState<LogEntry[]>([])
  const [tagSummaries, setTagSummaries] = useState<TagSummary[]>([])
  const [error, setError] = useState<string | null>(null)
  const logRef = useRef<HTMLDivElement>(null)
  const cleanupRef = useRef<(() => void) | null>(null)

  // Keep tags in sync when knownProducts grows
  useEffect(() => {
    if (knownProducts.length && !tags.length) setTags(knownProducts)
  }, [knownProducts, tags.length])

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
    if (e.key === 'Backspace' && !tagInput && tags.length) setTags((prev) => prev.slice(0, -1))
  }

  const handleEvent = (ev: SseEvent) => {
    if (ev.type === 'tag_start') {
      setLog((l) => [...l, { ts: now(), msg: `▶ Processing tag: ${String(ev.tag)}`, kind: 'info' }])
    } else if (ev.type === 'tag_done') {
      const tag = String(ev.tag)
      const patterns = Number(ev.patterns ?? 0)
      const total = Number(ev.total ?? 0)
      const noise = Number(ev.noise ?? 0)
      setTagSummaries((prev) => [...prev, { tag, patterns, total, noise }])
      setLog((l) => [
        ...l,
        { ts: now(), msg: `✓ ${tag}: ${total} signal, ${noise} noise, ${patterns} patterns`, kind: 'info' },
      ])
    } else {
      const detail = Object.entries(ev).filter(([k]) => k !== 'type').map(([k, v]) => `${k}=${String(v)}`).join(' ')
      setLog((l) => [...l, { ts: now(), msg: `[${ev.type}] ${detail}`, kind: 'info' }])
    }
  }

  const handleStart = async () => {
    if (!tags.length) return
    setRunning(true)
    setError(null)
    setTagSummaries([])
    setLog([{ ts: now(), msg: `Starting analysis for [${tags.join(', ')}] over ${windowDays}d window…`, kind: 'info' }])

    try {
      const res = await startAnalysis({ products: tags, window_days: windowDays })
      addProducts(tags)
      setLog((l) => [...l, { ts: now(), msg: `Run ${res.data.run_id} started…`, kind: 'info' }])

      cleanupRef.current?.()
      cleanupRef.current = connectSSE(
        analysisStreamUrl(res.data.run_id),
        handleEvent,
        () => {
          setLog((l) => [...l, { ts: now(), msg: '✓ Analysis complete.', kind: 'done' }])
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
        <div className="page-title">Analysis</div>
        <div className="page-subtitle">Classify questions and detect patterns per product/tag</div>
      </div>

      <div className="card" style={{ maxWidth: 680 }}>
        <div className="card-title">Products to analyse</div>

        <div className="form-group">
          <label>Tags <span className="hint">press Enter or comma to add</span></label>
          <div className="tag-input-wrap" onClick={() => document.getElementById('atag')?.focus()}>
            {tags.map((t) => (
              <span key={t} className="tag-chip">
                {t}
                <button type="button" onClick={() => removeTag(t)}><X size={10} /></button>
              </span>
            ))}
            <input
              id="atag"
              className="tag-bare-input"
              placeholder={tags.length ? '' : 'e.g. python…'}
              value={tagInput}
              onChange={(e) => setTagInput(e.target.value)}
              onKeyDown={handleKeyDown}
              onBlur={addTag}
              disabled={running}
            />
          </div>
          {knownProducts.length > 0 && !tags.length && (
            <div className="text-sm text-muted mt-4">
              From previous fetch:{' '}
              {knownProducts.map((p) => (
                <button key={p} className="btn btn-ghost btn-sm" onClick={() => setTags((prev) => [...new Set([...prev, p])])}>
                  + {p}
                </button>
              ))}
            </div>
          )}
        </div>

        <div className="form-group">
          <label>Time window</label>
          <div className="window-tabs">
            {WINDOWS.map((w) => (
              <button
                key={w}
                className={`window-tab${windowDays === w ? ' active' : ''}`}
                onClick={() => setWindowDays(w)}
                disabled={running}
              >
                {w}d
              </button>
            ))}
          </div>
        </div>

        {error && <div className="alert alert-error">{error}</div>}

        <button
          className="btn btn-primary"
          onClick={handleStart}
          disabled={running || !tags.length}
        >
          {running ? <Loader size={14} className="spin" /> : <BarChart2 size={14} />}
          {running ? 'Analysing…' : 'Start analysis'}
        </button>
      </div>

      {tagSummaries.length > 0 && (
        <div className="card" style={{ maxWidth: 680, marginTop: 16 }}>
          <div className="card-title">Results by tag</div>
          <table className="table">
            <thead>
              <tr>
                <th>Tag</th>
                <th>Signal questions</th>
                <th>Noise</th>
                <th>Patterns</th>
              </tr>
            </thead>
            <tbody>
              {tagSummaries.map((s) => (
                <tr key={s.tag}>
                  <td><span className="badge badge-blue">{s.tag}</span></td>
                  <td>{s.total}</td>
                  <td>{s.noise}</td>
                  <td>
                    <span className={`badge ${s.patterns > 0 ? 'badge-green' : 'badge-gray'}`}>
                      {s.patterns}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {log.length > 0 && (
        <div className="card" style={{ maxWidth: 680, marginTop: 16 }}>
          <div className="card-title" style={{ marginBottom: 10 }}>Progress log</div>
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
    </>
  )
}
