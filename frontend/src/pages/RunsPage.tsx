import { useEffect, useMemo, useState } from 'react'
import { Loader, RefreshCw } from 'lucide-react'
import { getRuns } from '../api'
import { errorMessage } from '../api/client'
import type { RunItem } from '../types/api'

const STATUSES: Array<{ value: string; label: string }> = [
  { value: '', label: 'All' },
  { value: 'done', label: 'Done' },
  { value: 'partial', label: 'Partial' },
  { value: 'failed', label: 'Failed' },
  { value: 'running', label: 'Running' },
]

function statusBadge(status: string): string {
  if (status === 'done') return 'badge badge-green'
  if (status === 'partial' || status === 'running') return 'badge badge-amber'
  if (status === 'failed') return 'badge badge-red'
  return 'badge badge-gray'
}

function fmtDuration(seconds: number | null): string {
  if (seconds == null) return '—'
  if (seconds < 60) return `${seconds.toFixed(0)}s`
  if (seconds < 3600) return `${(seconds / 60).toFixed(1)}m`
  return `${(seconds / 3600).toFixed(2)}h`
}

function fmtCounts(counts: Record<string, number>): string {
  const keys = Object.keys(counts)
  if (keys.length === 0) return '—'
  return keys.map((k) => `${k}=${counts[k]}`).join(' · ')
}

const PAGE_SIZE = 50

export function RunsPage() {
  const [runs, setRuns] = useState<RunItem[]>([])
  const [status, setStatus] = useState<string>('')
  const [offset, setOffset] = useState(0)
  const [loading, setLoading] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  const load = async (statusFilter: string, off: number) => {
    setLoading(true)
    setErr(null)
    try {
      const r = await getRuns(PAGE_SIZE, off, statusFilter || undefined)
      setRuns(r.data)
    } catch (e) {
      setErr(errorMessage(e))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void load(status, offset)
  }, [status, offset])

  const hasMore = runs.length === PAGE_SIZE
  const totalShownLabel = useMemo(() => {
    if (loading) return 'Loading…'
    const from = runs.length === 0 ? 0 : offset + 1
    const to = offset + runs.length
    return `Showing ${from}–${to}`
  }, [runs.length, offset, loading])

  return (
    <>
      <div className="page-header">
        <div className="flex items-center justify-between">
          <div>
            <div className="page-title">Run history</div>
            <div className="page-subtitle">Past ingest and aggregate runs — newest first</div>
          </div>
          <div className="btn-group">
            <button
              className="btn btn-secondary btn-sm"
              onClick={() => void load(status, offset)}
              disabled={loading}
            >
              {loading ? <Loader size={14} className="spin" /> : <RefreshCw size={14} />} Refresh
            </button>
          </div>
        </div>
      </div>

      <div className="card" style={{ marginBottom: 16 }}>
        <div className="flex items-center gap-12" style={{ flexWrap: 'wrap' }}>
          <div>
            <label style={{ display: 'block', marginBottom: 6 }}>Status</label>
            <select
              className="select"
              value={status}
              onChange={(e) => {
                setOffset(0)
                setStatus(e.target.value)
              }}
            >
              {STATUSES.map((s) => (
                <option key={s.value} value={s.value}>{s.label}</option>
              ))}
            </select>
          </div>
          <div style={{ flex: 1 }} />
          <div style={{ color: 'var(--text-muted)', fontSize: 12 }}>{totalShownLabel}</div>
        </div>
      </div>

      {err && (
        <div className="card" style={{ borderColor: 'var(--error)', color: 'var(--error)' }}>
          {err}
        </div>
      )}

      <div className="card">
        <table className="table">
          <thead>
            <tr>
              <th>Started</th>
              <th>Status</th>
              <th>Products</th>
              <th>Window</th>
              <th>Duration</th>
              <th>Counts</th>
            </tr>
          </thead>
          <tbody>
            {runs.length === 0 && !loading && (
              <tr>
                <td colSpan={6} style={{ color: 'var(--text-muted)', textAlign: 'center' }}>
                  No runs match the filter yet.
                </td>
              </tr>
            )}
            {runs.map((r) => (
              <tr key={r.id}>
                <td>{new Date(r.started_at).toLocaleString()}</td>
                <td><span className={statusBadge(r.status)}>{r.status}</span></td>
                <td>{r.products.length === 0 ? '—' : r.products.join(', ')}</td>
                <td>{r.window_days}d</td>
                <td>{fmtDuration(r.duration_seconds)}</td>
                <td style={{ color: 'var(--text-muted)', fontSize: 12 }}>{fmtCounts(r.counts)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="btn-group" style={{ marginTop: 16, justifyContent: 'flex-end' }}>
        <button
          className="btn btn-secondary btn-sm"
          disabled={offset === 0 || loading}
          onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
        >
          ← Previous
        </button>
        <button
          className="btn btn-secondary btn-sm"
          disabled={!hasMore || loading}
          onClick={() => setOffset(offset + PAGE_SIZE)}
        >
          Next →
        </button>
      </div>
    </>
  )
}
