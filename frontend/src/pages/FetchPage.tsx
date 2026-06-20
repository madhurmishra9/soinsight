import { useEffect, useRef, useState } from 'react'
import { AlertCircle, Download, Loader, Plus, X } from 'lucide-react'
import { getCoverage, validateTags } from '../api'
import { useApp } from '../context/AppContext'
import { useRuns } from '../context/RunsContext'
import type { TagCoverage, TagValidation } from '../types/api'

const WINDOWS = [7, 14, 30, 60, 90]
type TagStatus = TagValidation['status']

function fmtDate(iso: string | null): string {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return '—'
  return d.toLocaleString(undefined, {
    year: 'numeric', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
  })
}

export function FetchPage() {
  const { knownProducts } = useApp()
  const { fetch, patchFetch, startFetch } = useRuns()
  const { tags, windowDays, fromDate, toDate, incremental, running, log, error } = fetch

  const [tagInput, setTagInput] = useState('')
  const [coverage, setCoverage] = useState<TagCoverage[]>([])
  const [tagStatus, setTagStatus] = useState<Record<string, TagStatus>>({})
  const logRef = useRef<HTMLDivElement>(null)
  const wasRunning = useRef(running)

  const loadCoverage = (forTags: string[]) => {
    if (!forTags.length) { setCoverage([]); return }
    getCoverage(forTags).then((r) => setCoverage(r.data)).catch(() => undefined)
  }

  // Seed tags from previously fetched products the first time, only if empty.
  useEffect(() => {
    if (tags.length === 0 && knownProducts.length > 0) patchFetch({ tags: knownProducts })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [knownProducts])

  // Refresh coverage from the DB whenever the tag set changes.
  useEffect(() => {
    loadCoverage(tags)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tags.join(',')])

  // Validate tags against the SO instance (debounced). Tags not found there are
  // flagged; tags we couldn't verify (SO unreachable) are left in the normal colour.
  useEffect(() => {
    // Drop statuses for tags no longer present.
    setTagStatus((prev) => {
      const next: Record<string, TagStatus> = {}
      for (const t of tags) if (prev[t]) next[t] = prev[t]
      return next
    })
    if (!tags.length) return
    const handle = setTimeout(() => {
      validateTags(tags)
        .then((r) => {
          setTagStatus((prev) => {
            const next = { ...prev }
            for (const v of r.data) next[v.tag] = v.status
            return next
          })
        })
        .catch(() => undefined)
    }, 400)
    return () => clearTimeout(handle)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tags.join(',')])

  // Refresh coverage right after a fetch finishes (running: true → false).
  useEffect(() => {
    if (wasRunning.current && !running) loadCoverage(tags)
    wasRunning.current = running
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [running])

  useEffect(() => {
    logRef.current?.scrollTo({ top: logRef.current.scrollHeight, behavior: 'smooth' })
  }, [log])

  const addTag = () => {
    const t = tagInput.trim().toLowerCase()
    if (t && !tags.includes(t)) patchFetch({ tags: [...tags, t] })
    setTagInput('')
  }
  const removeTag = (tag: string) => patchFetch({ tags: tags.filter((t) => t !== tag) })

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' || e.key === ',') { e.preventDefault(); addTag() }
    if (e.key === 'Backspace' && tagInput === '' && tags.length) {
      patchFetch({ tags: tags.slice(0, -1) })
    }
  }

  return (
    <>
      <div className="page-header">
        <div className="page-title">Fetch Questions</div>
        <div className="page-subtitle">Pull tagged questions (and their answers) from Stack Overflow Enterprise into the local DB</div>
      </div>

      <div className="card" style={{ maxWidth: 680 }}>
        <div className="card-title">Products / tags</div>

        <div className="form-group">
          <label>Tags to ingest <span className="hint">press Enter or comma to add</span></label>
          <div className="tag-input-wrap" onClick={() => document.getElementById('tag-bare')?.focus()}>
            {tags.map((t) => {
              const unavailable = tagStatus[t] === 'unavailable'
              return (
                <span
                  key={t}
                  className="tag-chip"
                  title={unavailable ? `"${t}" was not found on your Stack Overflow instance` : undefined}
                  style={unavailable ? { borderColor: '#dc2626', color: '#dc2626', background: 'rgba(220,38,38,0.08)' } : undefined}
                >
                  {unavailable && <AlertCircle size={10} style={{ marginRight: 2 }} />}
                  {t}
                  <button type="button" onClick={() => removeTag(t)}><X size={10} /></button>
                </span>
              )
            })}
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
          {tags.some((t) => tagStatus[t] === 'unavailable') && (
            <div className="text-sm" style={{ color: '#dc2626', marginTop: 6, display: 'flex', alignItems: 'center', gap: 6 }}>
              <AlertCircle size={13} />
              Tags shown in red weren’t found on your Stack Overflow instance — check the spelling.
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
                onClick={() => patchFetch({ windowDays: w, fromDate: '', toDate: '' })}
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
            <input className="input" type="date" value={fromDate} onChange={(e) => patchFetch({ fromDate: e.target.value })} disabled={running} />
            <input className="input" type="date" value={toDate} onChange={(e) => patchFetch({ toDate: e.target.value })} disabled={running} />
          </div>
        </div>

        <div className="form-group">
          <label style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer' }}>
            <input
              type="checkbox"
              checked={incremental}
              onChange={(e) => patchFetch({ incremental: e.target.checked })}
              disabled={running}
            />
            <span>Incremental fetch <span className="hint">(only questions newer than last fetch per tag — much faster)</span></span>
          </label>
          {!incremental && (
            <div className="alert alert-info" style={{ marginTop: 6 }}>
              Full fetch: will re-download all questions in the selected date range.
            </div>
          )}

          {/* Local data coverage — read live from the DB */}
          <div className="coverage-panel" style={{ marginTop: 10 }}>
            <div className="card-subtitle" style={{ marginBottom: 6 }}>
              Local data coverage <span className="hint">— straight from the database</span>
            </div>
            {coverage.length === 0 ? (
              <div className="text-muted text-sm">
                No questions stored yet for {tags.length ? 'these tags' : 'any tag'}. Run a fetch to populate.
              </div>
            ) : (
              <table className="table" style={{ fontSize: 13 }}>
                <thead>
                  <tr>
                    <th>Tag</th>
                    <th>Questions</th>
                    <th>Answers</th>
                    <th>Data fetched till</th>
                    <th>Last fetch run</th>
                  </tr>
                </thead>
                <tbody>
                  {coverage.map((c) => (
                    <tr key={c.tag}>
                      <td><span className="badge badge-blue">{c.tag}</span></td>
                      <td>{c.question_count.toLocaleString()}</td>
                      <td>{c.answer_count.toLocaleString()}</td>
                      <td>
                        {c.latest_question_at
                          ? <strong>{fmtDate(c.latest_question_at)}</strong>
                          : <span className="text-muted">—</span>}
                      </td>
                      <td className="text-muted">{fmtDate(c.last_fetch_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
            {incremental && coverage.some((c) => c.latest_question_at) && (
              <div className="hint" style={{ marginTop: 6 }}>
                Incremental is on — for each tag above, only questions newer than “data fetched till” will be pulled.
              </div>
            )}
          </div>
        </div>

        {error && <div className="alert alert-error">{error}</div>}

        <button
          className="btn btn-primary"
          onClick={() => { addTag(); void startFetch() }}
          disabled={running || !tags.length}
        >
          {running ? <Loader size={14} className="spin" /> : <Download size={14} />}
          {running ? 'Fetching…' : 'Fetch questions'}
        </button>
        {running && (
          <div className="hint" style={{ marginTop: 8 }}>
            This run keeps going if you switch tabs — come back any time to see progress.
          </div>
        )}
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
