import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import {
  AlertTriangle,
  Check,
  CheckCircle2,
  Clock,
  Gauge,
  HelpCircle,
  Loader,
  MessageSquareOff,
  X,
} from 'lucide-react'
import { getMetricQuestions, getMetrics } from '../api'
import { errorMessage } from '../api/client'
import { useApp } from '../context/AppContext'
import type { MetricBucket, MetricQuestionRef, MetricsSummary } from '../types/api'

const WINDOWS = [7, 14, 30, 60, 90]

interface MetricDrill {
  bucket: MetricBucket
  label: string
  tags: string[]
}

function fmtHours(hours: number | null): string {
  if (hours === null) return '—'
  if (hours < 1) return `${Math.round(hours * 60)}m`
  if (hours < 48) return `${hours.toFixed(1)}h`
  return `${(hours / 24).toFixed(1)}d`
}

function fmtPct(ratio: number | null): string | undefined {
  return ratio === null ? undefined : `${Math.round(ratio * 100)}%`
}

function StatCard({
  value, label, sub, onClick,
}: { value: string | number; label: string; sub?: string; onClick?: () => void }) {
  return (
    <div className="card stat" onClick={onClick} style={onClick ? { cursor: 'pointer' } : undefined}>
      <div className="stat-value">{value}</div>
      <div className="stat-label">{label}</div>
      {sub && <div className="stat-note">{sub}</div>}
    </div>
  )
}

/** Side drawer listing the exact questions behind a clicked Metrics-tab number. */
function MetricsDrawer({
  drill, tags, windowDays, fromDate, toDate, onClose,
}: {
  drill: MetricDrill | null
  tags: string[]
  windowDays: number
  fromDate?: string
  toDate?: string
  onClose: () => void
}) {
  const [items, setItems] = useState<MetricQuestionRef[] | null>(null)
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    if (!drill) return
    setLoading(true)
    setItems(null)
    getMetricQuestions(drill.bucket, drill.tags.length ? drill.tags : tags, windowDays, fromDate, toDate)
      .then((r) => setItems(r.data))
      .catch(() => setItems([]))
      .finally(() => setLoading(false))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [drill, windowDays, fromDate, toDate])

  if (!drill) return null

  const showTime = drill.bucket === 'answered_with_time'

  return (
    <div className="drawer-overlay" onClick={onClose}>
      <div className="drawer" onClick={(e) => e.stopPropagation()}>
        <div className="drawer-header">
          <span>{drill.label}</span>
          <button className="btn btn-sm btn-secondary" onClick={onClose}>
            <X size={14} />
          </button>
        </div>
        {loading && <div className="text-muted text-sm">Loading…</div>}
        {items && items.length === 0 && <div className="text-muted text-sm">No questions.</div>}
        {items && items.length > 0 && (
          <ul className="drawer-list">
            {items.map((q) => (
              <li key={q.so_id}>
                {q.url ? (
                  <a href={q.url} target="_blank" rel="noreferrer">{q.title}</a>
                ) : (
                  q.title
                )}
                {' '}<span className="muted">[Q#{q.so_id}]</span>
                <div className="muted text-sm" style={{ marginTop: 2 }}>
                  score {q.score} · {q.view_count} views · {q.answer_count} answer{q.answer_count === 1 ? '' : 's'}
                  {q.has_accepted && (
                    <span className="badge badge-green" style={{ marginLeft: 6, display: 'inline-flex', alignItems: 'center', gap: 2 }}>
                      <Check size={10} /> accepted
                    </span>
                  )}
                  {showTime && q.time_to_first_answer_hours !== null && (
                    <span className="badge badge-blue" style={{ marginLeft: 6 }}>
                      <Clock size={10} /> answered in {fmtHours(q.time_to_first_answer_hours)}
                    </span>
                  )}
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>
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
  const [drill, setDrill] = useState<MetricDrill | null>(null)

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
          run through Analysis. Click any number to see the questions behind it. See the{' '}
          <Link to="/help#metrics">user guide</Link> for how each number is derived.
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
            <StatCard
              value={data.total_questions}
              label="Total questions"
              sub={`in ${data.window_days}d window`}
              onClick={() => setDrill({ bucket: 'total', label: 'Total questions', tags: selectedTags })}
            />
            <StatCard
              value={data.answered}
              label="Answered"
              sub={answeredPct !== null ? `${answeredPct}% of total` : undefined}
              onClick={() => setDrill({ bucket: 'answered', label: 'Answered questions', tags: selectedTags })}
            />
            <StatCard
              value={data.unanswered}
              label="Unanswered"
              onClick={() => setDrill({ bucket: 'unanswered', label: 'Unanswered questions', tags: selectedTags })}
            />
            <StatCard
              value={data.classified}
              label="Classified / analysed"
              sub={classifiedPct !== null ? `${classifiedPct}% of total` : undefined}
              onClick={() => setDrill({ bucket: 'classified', label: 'Classified / analysed questions', tags: selectedTags })}
            />
          </div>

          <div className="grid-3" style={{ marginBottom: 16 }}>
            <StatCard
              value={data.distinct_askers}
              label="People who asked"
              sub="distinct askers in window"
            />
            <StatCard
              value={data.accepted}
              label="Got an accepted answer"
              sub={fmtPct(data.acceptance_rate) ? `${fmtPct(data.acceptance_rate)} of answered` : undefined}
              onClick={() => setDrill({ bucket: 'accepted', label: 'Questions with an accepted answer', tags: selectedTags })}
            />
            <StatCard
              value={data.not_accepted}
              label="Answered, still unresolved"
              sub="no answer marked accepted"
              onClick={() => setDrill({ bucket: 'not_accepted', label: 'Answered but not accepted', tags: selectedTags })}
            />
          </div>

          <div className="grid-3" style={{ marginBottom: 16 }}>
            <StatCard
              value={data.avg_answers_per_question ?? '—'}
              label="Avg. answers / question"
              sub="engagement per question"
            />
            <StatCard
              value={data.avg_views_per_question ?? '—'}
              label="Avg. views / question"
              sub="interest per question"
            />
            <StatCard
              value={fmtHours(data.mean_time_to_answer_hours)}
              label="Time to first answer"
              sub={data.median_time_to_answer_hours !== null ? `median ${fmtHours(data.median_time_to_answer_hours)}` : undefined}
              onClick={() => setDrill({ bucket: 'answered_with_time', label: 'Time to first answer', tags: selectedTags })}
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
                    <span
                      className="badge badge-amber"
                      style={{ cursor: 'pointer' }}
                      onClick={() => setDrill({ bucket: 'unclassified', label: 'Skipped / missing from analysis', tags: selectedTags })}
                    >
                      {r.count}
                    </span>
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
              column totals can exceed "Total questions" above. Click any number to see its questions.
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
                    <th>Accepted</th>
                    <th>Classified</th>
                    <th>Skipped</th>
                    <th>Time to answer</th>
                  </tr>
                </thead>
                <tbody>
                  {data.by_tag.map((t) => (
                    <tr key={t.tag}>
                      <td><span className="badge badge-blue">{t.tag}</span></td>
                      <td>
                        <span style={{ cursor: 'pointer' }} onClick={() => setDrill({ bucket: 'total', label: `${t.tag} — total questions`, tags: [t.tag] })}>
                          {t.total_questions}
                        </span>
                      </td>
                      <td>
                        <span style={{ cursor: 'pointer' }} onClick={() => setDrill({ bucket: 'answered', label: `${t.tag} — answered questions`, tags: [t.tag] })}>
                          {t.answered}
                        </span>
                      </td>
                      <td>
                        {t.unanswered > 0 ? (
                          <span
                            className="flex items-center gap-8"
                            style={{ cursor: 'pointer', display: 'inline-flex' }}
                            onClick={() => setDrill({ bucket: 'unanswered', label: `${t.tag} — unanswered questions`, tags: [t.tag] })}
                          >
                            <MessageSquareOff size={12} className="text-muted" /> {t.unanswered}
                          </span>
                        ) : 0}
                      </td>
                      <td>
                        <span
                          style={{ cursor: 'pointer' }}
                          onClick={() => setDrill({ bucket: 'accepted', label: `${t.tag} — accepted answers`, tags: [t.tag] })}
                        >
                          {t.accepted}{fmtPct(t.acceptance_rate) ? ` (${fmtPct(t.acceptance_rate)})` : ''}
                        </span>
                      </td>
                      <td>
                        <span style={{ cursor: 'pointer' }} onClick={() => setDrill({ bucket: 'classified', label: `${t.tag} — classified questions`, tags: [t.tag] })}>
                          {t.classified}
                        </span>
                      </td>
                      <td>
                        {t.unclassified > 0 ? (
                          <span
                            className="badge badge-amber"
                            style={{ cursor: 'pointer' }}
                            onClick={() => setDrill({ bucket: 'unclassified', label: `${t.tag} — skipped questions`, tags: [t.tag] })}
                          >
                            {t.unclassified}
                          </span>
                        ) : (
                          <span className="badge badge-green">0</span>
                        )}
                      </td>
                      <td>
                        {t.mean_time_to_answer_hours !== null ? (
                          <span
                            style={{ cursor: 'pointer' }}
                            onClick={() => setDrill({ bucket: 'answered_with_time', label: `${t.tag} — time to first answer`, tags: [t.tag] })}
                          >
                            {fmtHours(t.mean_time_to_answer_hours)}
                          </span>
                        ) : '—'}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>

          <MetricsDrawer
            drill={drill}
            tags={selectedTags}
            windowDays={windowDays}
            fromDate={fromDate || undefined}
            toDate={toDate || undefined}
            onClose={() => setDrill(null)}
          />
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
