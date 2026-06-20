import { useEffect, useRef, useState } from 'react'
import { BarChart2, Loader, X } from 'lucide-react'
import { useApp } from '../context/AppContext'
import { useRuns } from '../context/RunsContext'

const WINDOWS = [7, 14, 30, 60, 90]

export function AnalysisPage() {
  const { knownProducts } = useApp()
  const { analysis, patchAnalysis, startAnalysis } = useRuns()
  const { tags, windowDays, fromDate, toDate, running, log, error, tagSummaries } = analysis

  const [tagInput, setTagInput] = useState('')
  const logRef = useRef<HTMLDivElement>(null)

  // Seed tags from previously fetched products the first time, only if empty.
  useEffect(() => {
    if (tags.length === 0 && knownProducts.length > 0) patchAnalysis({ tags: knownProducts })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [knownProducts])

  useEffect(() => {
    logRef.current?.scrollTo({ top: logRef.current.scrollHeight, behavior: 'smooth' })
  }, [log])

  const addTag = () => {
    const t = tagInput.trim().toLowerCase()
    if (t && !tags.includes(t)) patchAnalysis({ tags: [...tags, t] })
    setTagInput('')
  }
  const removeTag = (tag: string) => patchAnalysis({ tags: tags.filter((t) => t !== tag) })
  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' || e.key === ',') { e.preventDefault(); addTag() }
    if (e.key === 'Backspace' && !tagInput && tags.length) patchAnalysis({ tags: tags.slice(0, -1) })
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
                <button key={p} className="btn btn-ghost btn-sm" onClick={() => patchAnalysis({ tags: [...new Set([...tags, p])] })}>
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
                className={`window-tab${windowDays === w && !fromDate && !toDate ? ' active' : ''}`}
                onClick={() => patchAnalysis({ windowDays: w, fromDate: '', toDate: '' })}
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
            <input
              className="input"
              type="date"
              value={fromDate}
              onChange={(e) => patchAnalysis({ fromDate: e.target.value })}
              disabled={running}
            />
            <input
              className="input"
              type="date"
              value={toDate}
              onChange={(e) => patchAnalysis({ toDate: e.target.value })}
              disabled={running}
            />
          </div>
        </div>

        <div className="alert alert-info" style={{ fontSize: 13 }}>
          Analysis is always incremental — only questions not yet classified are sent to the model.
          Previously classified questions load instantly from the database.
        </div>

        {error && <div className="alert alert-error">{error}</div>}

        <button
          className="btn btn-primary"
          onClick={() => { addTag(); void startAnalysis() }}
          disabled={running || !tags.length}
        >
          {running ? <Loader size={14} className="spin" /> : <BarChart2 size={14} />}
          {running ? 'Analysing…' : 'Start analysis'}
        </button>
        {running && (
          <div className="hint" style={{ marginTop: 8 }}>
            This run keeps going if you switch tabs — come back any time to see progress.
          </div>
        )}
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
