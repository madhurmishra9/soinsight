import { useEffect, useState } from 'react'
import { BellOff, BellRing, Loader, Plus, RotateCcw } from 'lucide-react'
import { dismissPattern, listDismissed, restorePattern } from '../api'
import { errorMessage } from '../api/client'
import { useApp } from '../context/AppContext'
import type { DismissedItem } from '../types/api'

interface NewSnoozeForm {
  product: string
  main: string
  sub: string
  days: number | ''
  reason: string
}

const EMPTY_FORM: NewSnoozeForm = { product: '', main: '', sub: '', days: 30, reason: '' }

function fmtUntil(value: string | null): string {
  if (!value) return 'indefinite'
  return new Date(value).toLocaleString()
}

export function DismissedPatternsPage() {
  const { knownProducts } = useApp()

  const [productFilter, setProductFilter] = useState<string>('')
  const [includeExpired, setIncludeExpired] = useState(false)
  const [rows, setRows] = useState<DismissedItem[]>([])
  const [loading, setLoading] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  const [showForm, setShowForm] = useState(false)
  const [form, setForm] = useState<NewSnoozeForm>(EMPTY_FORM)
  const [submitting, setSubmitting] = useState(false)
  const [formErr, setFormErr] = useState<string | null>(null)

  const load = async () => {
    setLoading(true)
    setErr(null)
    try {
      const r = await listDismissed(productFilter || undefined, includeExpired)
      setRows(r.data)
    } catch (e) {
      setErr(errorMessage(e))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [productFilter, includeExpired])

  const handleRestore = async (item: DismissedItem) => {
    if (!confirm(`Restore "${item.main} / ${item.sub}" for ${item.product}?`)) return
    try {
      await restorePattern(item.product, item.main, item.sub)
      setRows((prev) => prev.filter((r) => r.id !== item.id))
    } catch (e) {
      setErr(errorMessage(e))
    }
  }

  const handleSnooze = async () => {
    if (!form.product.trim() || !form.main.trim() || !form.sub.trim()) {
      setFormErr('Product, main, and sub are required.')
      return
    }
    setSubmitting(true)
    setFormErr(null)
    try {
      await dismissPattern({
        product: form.product.trim(),
        main: form.main.trim(),
        sub: form.sub.trim(),
        days: form.days === '' ? undefined : Number(form.days),
        reason: form.reason.trim() || undefined,
      })
      setForm(EMPTY_FORM)
      setShowForm(false)
      await load()
    } catch (e) {
      setFormErr(errorMessage(e))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <>
      <div className="page-header">
        <div className="flex items-center justify-between">
          <div>
            <div className="page-title">Snoozed patterns</div>
            <div className="page-subtitle">
              Patterns you have acknowledged. Snoozed clusters are hidden from the dashboard
              until the snooze expires.
            </div>
          </div>
          <div className="btn-group">
            <button
              className="btn btn-primary btn-sm"
              onClick={() => setShowForm((v) => !v)}
            >
              <Plus size={14} /> {showForm ? 'Cancel' : 'New snooze'}
            </button>
          </div>
        </div>
      </div>

      {showForm && (
        <div className="card" style={{ marginBottom: 16 }}>
          <div className="card-title">Snooze a pattern</div>
          <div className="flex items-center gap-12" style={{ flexWrap: 'wrap' }}>
            <div style={{ flex: '1 1 180px' }}>
              <label style={{ display: 'block', marginBottom: 6 }}>Product / tag</label>
              {knownProducts.length > 0 ? (
                <select
                  className="select"
                  value={form.product}
                  onChange={(e) => setForm({ ...form, product: e.target.value })}
                >
                  <option value="">Select…</option>
                  {knownProducts.map((p) => (
                    <option key={p} value={p}>{p}</option>
                  ))}
                </select>
              ) : (
                <input
                  className="input"
                  placeholder="e.g. cloudsql"
                  value={form.product}
                  onChange={(e) => setForm({ ...form, product: e.target.value })}
                />
              )}
            </div>
            <div style={{ flex: '1 1 180px' }}>
              <label style={{ display: 'block', marginBottom: 6 }}>Main category</label>
              <input
                className="input"
                placeholder="Technical"
                value={form.main}
                onChange={(e) => setForm({ ...form, main: e.target.value })}
              />
            </div>
            <div style={{ flex: '1 1 220px' }}>
              <label style={{ display: 'block', marginBottom: 6 }}>Sub-category</label>
              <input
                className="input"
                placeholder="Reliability issues or instability"
                value={form.sub}
                onChange={(e) => setForm({ ...form, sub: e.target.value })}
              />
            </div>
            <div style={{ width: 120 }}>
              <label style={{ display: 'block', marginBottom: 6 }}>Days</label>
              <input
                className="input"
                type="number"
                min={1}
                max={3650}
                value={form.days}
                onChange={(e) => {
                  const v = e.target.value
                  setForm({ ...form, days: v === '' ? '' : Number(v) })
                }}
              />
            </div>
            <div style={{ flex: '1 1 240px' }}>
              <label style={{ display: 'block', marginBottom: 6 }}>Reason (optional)</label>
              <input
                className="input"
                placeholder="fix shipping in v1.4"
                value={form.reason}
                onChange={(e) => setForm({ ...form, reason: e.target.value })}
              />
            </div>
            <div style={{ alignSelf: 'flex-end' }}>
              <button className="btn btn-primary" onClick={handleSnooze} disabled={submitting}>
                {submitting ? <Loader size={14} className="spin" /> : <BellOff size={14} />} Snooze
              </button>
            </div>
          </div>
          {formErr && (
            <div style={{ marginTop: 8, color: 'var(--error)', fontSize: 12 }}>{formErr}</div>
          )}
          <div style={{ marginTop: 8, color: 'var(--text-muted)', fontSize: 12 }}>
            Leave Days blank for an indefinite snooze.
          </div>
        </div>
      )}

      <div className="card" style={{ marginBottom: 16 }}>
        <div className="flex items-center gap-12" style={{ flexWrap: 'wrap' }}>
          <div style={{ flex: '1 1 200px' }}>
            <label style={{ display: 'block', marginBottom: 6 }}>Filter by product</label>
            <input
              className="input"
              placeholder="(all)"
              value={productFilter}
              onChange={(e) => setProductFilter(e.target.value)}
            />
          </div>
          <label
            className="flex items-center"
            style={{ gap: 6, alignSelf: 'flex-end', marginBottom: 6, fontSize: 13 }}
          >
            <input
              type="checkbox"
              checked={includeExpired}
              onChange={(e) => setIncludeExpired(e.target.checked)}
            />
            Include expired
          </label>
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
              <th>Product</th>
              <th>Main</th>
              <th>Sub-category</th>
              <th>Until</th>
              <th>Reason</th>
              <th>Created</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 && !loading && (
              <tr>
                <td colSpan={7} style={{ color: 'var(--text-muted)', textAlign: 'center' }}>
                  <BellRing size={14} style={{ verticalAlign: 'middle', marginRight: 6 }} />
                  No active snoozes. Use “New snooze” above, or snooze a pattern from the dashboard.
                </td>
              </tr>
            )}
            {rows.map((r) => (
              <tr key={r.id}>
                <td>{r.product}</td>
                <td>{r.main}</td>
                <td>{r.sub}</td>
                <td>{fmtUntil(r.dismissed_until)}</td>
                <td style={{ color: 'var(--text-muted)' }}>{r.reason || '—'}</td>
                <td style={{ color: 'var(--text-muted)', fontSize: 12 }}>
                  {new Date(r.created_at).toLocaleString()}
                </td>
                <td>
                  <button
                    className="btn btn-ghost btn-sm"
                    onClick={() => void handleRestore(r)}
                    title="Restore this pattern"
                  >
                    <RotateCcw size={14} /> Restore
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  )
}
