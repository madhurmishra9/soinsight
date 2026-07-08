import { useState } from 'react'
import { Link } from 'react-router-dom'
import { AlertTriangle, CheckCircle2, Gauge, HelpCircle, Loader, MessageSquareOff } from 'lucide-react'
import { getMetrics } from '../api'
import { errorMessage } from '../api/client'
import { useApp } from '../context/AppContext'
import type { MetricsSummary } from '../types/api'

const WINDOWS = [7, 14, 30, 60, 90]

function StatCard({ value, label, sub, tone }: { value: string | number; label: string; sub?: string; tone?: 'error' | 'warning' }) {
  return (
    <div className="card stat">
      <div className="stat-value" style={tone ? { color: `var(--${tone})` } : undefined}>{value}</div>
      <div className="stat-label">{label}</div>
      {sub && <div className="stat-note">{sub}</div>}
    </div>
  )
}

export function MetricsPage() {
  const { knownProducts } = useApp()
  const [selectedTags, setSelectedTags] = useState<string[]>([])
  const [windowDays, setWindowDays] = useState(30)
  const [fromDate, setFromDate] = useState('')
  const [toDate, setToDate] = useState('')
  const [data, setData] = useState<MetricsSummary | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const toggleTag = (tag: string) => {
    setSelectedTags((prev) => (prev.includes(tag) ? prev.filter((t) => t !== tag) : [...prev, tag]))
  }

  const load = async () => {
    setLoading(true)
    setError(null)
    try {
      const res = await getMetrics(selectedTags, windowDays, fromDate || undefined, toDate || undefined)
      setData(res.data)
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setLoading(false)
    }
  }

  const classifiedPct = data && data.total_questions > 0
    ? Math.round((data.classified / data.total_questions) * 100)
    : null
  const answeredPct = data && data.total_questions > 0
    ? Math.round((data.answered / data.total_questions) * 100)
    : null

  return (
    <>
      <div className="page-header">
        <div className="page-title">Metrics</div>
        <div className="page-subtitle">
          Pipeline health for a date range — how much was fetched, answered, and actually
          run through Analysis. See the <Link to="/help#metrics">user guide</Link> for how each number is derived.
        </div>
      </div>

      <div className="card" style={{ marginBottom: 20 }}>
        <div className="form-group">
          <label>Tags <span className="hint">(none selected = every tag present in range)</span></label>
          {knownProducts.length > 0 ? (
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
              {knownProducts.map((p) => (
                <label
                  key={p}
                  className="tag-chip"
                  style={{
                    cursor: 'pointer',
                    background: selectedTags.includes(p) ? 'var(--primary)' : undefined,
                    color: selectedTags.includes(p) ? '#fff' : undefined,
                  }}
                >
                  <input
                    type="checkbox"
                    checked={selectedTags.includes(p)}
                    onChange={() => toggleTag(p)}
                    style={{ display: 'none' }}
                  />
                  {p}
                </label>
              ))}
            </div>
          ) : (
            <div className="text-muted text-sm">
              No known tags yet — fetch some questions first, or leave empty to cover every tag in range.
            </div>
          )}
        </div>

        <div className="flex items-center gap-12" style={{ flexWrap: 'wrap' }}>
          <div>
            <label style={{ display: 'block', marginBottom: 6 }}>Window</label>
            <div className="window-tabs">
              {WINDOWS.map((w) => (
                <button
                  key={w}
                  className={`window-tab${windowDays === w && !fromDate && !toDate ? ' active' : ''}`}
                  onClick={() => { setWindowDays(w); setFromDate(''); setToDate('') }}
                >
                  {w}d
                </button>
              ))}
            </div>
          </div>
          <div>
            <label style={{ display: 'block', marginBottom: 6 }}>Custom range</label>
            <div className="flex items-center gap-12" style={{ flexWrap: 'wrap' }}>
              <input className="input" type="date" value={fromDate} onChange={(e) => setFromDate(e.target.value)} />
              <input className="input" type="date" value={toDate} onChange={(e) => setToDate(e.target.value)} />
            </div>
          </div>
          <div style={{ paddingTop: 22 }}>
            <button className="btn btn-primary" onClick={load} disabled={loading}>
              {loading ? <Loader size={14} className="spin" /> : <Gauge size={14} />}
              {loading ? 'Loading…' : 'Load metrics'}
            </button>
          </div>
        </div>
      </div>

      {error && <div className="alert alert-error">{error}</div>}

      {data && (
        <>
          <div className="grid-4" style={{ marginBottom: 16 }}>
            <StatCard value={data.total_questions} label="Total questions" sub={`in ${data.window_days}d window`} />
            <StatCard value={data.answered} label="Answered" sub={answeredPct !== null ? `${answeredPct}% of total` : undefined} />
            <StatCard value={data.unanswered} label="Unanswered" />
            <StatCard
              value={data.classified}
              label="Classified / analysed"
              sub={classifiedPct !== null ? `${classifiedPct}% of total` : undefined}
            />
          </div>

          {data.unclassified > 0 ? (
            <div className="card" style={{ marginBottom: 16 }}>
              <div className="card-title" style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <AlertTriangle size={15} style={{ color: 'var(--warning)' }} />
                Skipped / missing from analysis ({data.unclassified})
              </div>
              <div className="card-subtitle">
                Questions that were fetched into the database but do not yet have a classification
                record for this window. Run <strong>Analysis</strong> for these tags/window to close the gap.
              </div>
              <ul style={{ listStyle: 'none', paddingLeft: 0, marginTop: 8 }}>
                {data.unclassified_reasons.map((r, i) => (
                  <li key={i} className="flex items-center gap-8" style={{ marginBottom: 6 }}>
                    <span className="badge badge-amber">{r.count}</span>
                    <span style={{ fontSize: 14 }}>{r.reason}</span>
                  </li>
                ))}
              </ul>
            </div>
          ) : (
            <div className="alert alert-success" style={{ marginBottom: 16 }}>
              <CheckCircle2 size={14} /> Every fetched question in this range has been classified — analysis is fully caught up.
            </div>
          )}

          <div className="card">
            <div className="card-title">Tag-wise breakdown</div>
            <div className="card-subtitle">
              A question tagged with multiple tracked tags is counted once per matching tag, so
              column totals can exceed "Total questions" above.
            </div>
            {data.by_tag.length === 0 ? (
              <div className="text-muted text-sm">No questions in range for the selected tags.</div>
            ) : (
              <table className="table">
                <thead>
                  <tr>
                    <th>Tag</th>
                    <th>Total</th>
                    <th>Answered</th>
                    <th>Unanswered</th>
                    <th>Classified</th>
                    <th>Skipped</th>
                  </tr>
                </thead>
                <tbody>
                  {data.by_tag.map((t) => (
                    <tr key={t.tag}>
                      <td><span className="badge badge-blue">{t.tag}</span></td>
                      <td>{t.total_questions}</td>
                      <td>{t.answered}</td>
                      <td>
                        {t.unanswered > 0 ? (
                          <span className="flex items-center gap-8">
                            <MessageSquareOff size={12} className="text-muted" /> {t.unanswered}
                          </span>
                        ) : 0}
                      </td>
                      <td>{t.classified}</td>
                      <td>
                        {t.unclassified > 0 ? (
                          <span className="badge badge-amber">{t.unclassified}</span>
                        ) : (
                          <span className="badge badge-green">0</span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </>
      )}

      {!data && !loading && !error && (
        <div className="card">
          <div className="alert alert-info" style={{ marginBottom: 0 }}>
            <HelpCircle size={14} />
            Pick tags (optional) and a window, then click <strong>Load metrics</strong>.
          </div>
        </div>
      )}
    </>
  )
}
