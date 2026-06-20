import { useState } from 'react'
import { Loader, TrendingUp } from 'lucide-react'
import { getTrends } from '../api'
import { errorMessage } from '../api/client'
import { useApp } from '../context/AppContext'
import type { TrendItem } from '../types/api'

const DEFAULTS = {
  recent_days: 7,
  baseline_days: 30,
  threshold: 2.0,
  min_recent: 2,
}

export function TrendsPage() {
  const { settings } = useApp()
  const initialProduct =
    (settings.default_tags || '').split(',').map((t) => t.trim()).filter(Boolean)[0] || ''

  const [product, setProduct] = useState(initialProduct)
  const [opts, setOpts] = useState(DEFAULTS)
  const [items, setItems] = useState<TrendItem[]>([])
  const [loaded, setLoaded] = useState(false)
  const [loading, setLoading] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  const run = async () => {
    if (!product.trim()) return
    setLoading(true)
    setErr(null)
    try {
      const r = await getTrends(product.trim(), opts)
      setItems(r.data)
      setLoaded(true)
    } catch (e) {
      setErr(errorMessage(e))
    } finally {
      setLoading(false)
    }
  }

  const rising = items.filter((t) => t.is_rising)
  const quiet = items.filter((t) => !t.is_rising)

  return (
    <>
      <div className="page-header">
        <div className="page-title">Rising-volume detector</div>
        <div className="page-subtitle">
          Categories whose recent volume is at least <strong>{opts.threshold.toFixed(1)}×</strong> the
          trailing baseline. Tune the windows and threshold below.
        </div>
      </div>

      <div className="card" style={{ marginBottom: 16 }}>
        <div className="flex items-center gap-12" style={{ flexWrap: 'wrap' }}>
          <div style={{ flex: '1 1 200px' }}>
            <label style={{ display: 'block', marginBottom: 6 }}>Product / tag</label>
            <input
              className="input"
              placeholder="e.g. cloudsql"
              value={product}
              onChange={(e) => setProduct(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') void run()
              }}
            />
          </div>
          <div style={{ width: 120 }}>
            <label style={{ display: 'block', marginBottom: 6 }}>Recent (days)</label>
            <input
              className="input"
              type="number"
              min={1}
              max={90}
              value={opts.recent_days}
              onChange={(e) => setOpts({ ...opts, recent_days: Number(e.target.value) || 1 })}
            />
          </div>
          <div style={{ width: 120 }}>
            <label style={{ display: 'block', marginBottom: 6 }}>Baseline (days)</label>
            <input
              className="input"
              type="number"
              min={7}
              max={365}
              value={opts.baseline_days}
              onChange={(e) => setOpts({ ...opts, baseline_days: Number(e.target.value) || 30 })}
            />
          </div>
          <div style={{ width: 100 }}>
            <label style={{ display: 'block', marginBottom: 6 }}>Threshold ×</label>
            <input
              className="input"
              type="number"
              step={0.5}
              min={1}
              max={20}
              value={opts.threshold}
              onChange={(e) => setOpts({ ...opts, threshold: Number(e.target.value) || 1 })}
            />
          </div>
          <div style={{ width: 110 }}>
            <label style={{ display: 'block', marginBottom: 6 }}>Min recent</label>
            <input
              className="input"
              type="number"
              min={1}
              value={opts.min_recent}
              onChange={(e) => setOpts({ ...opts, min_recent: Number(e.target.value) || 1 })}
            />
          </div>
          <div style={{ alignSelf: 'flex-end' }}>
            <button className="btn btn-primary" onClick={run} disabled={loading || !product.trim()}>
              {loading ? <Loader size={14} className="spin" /> : <TrendingUp size={14} />} Detect
            </button>
          </div>
        </div>
        {opts.recent_days >= opts.baseline_days && (
          <div style={{ marginTop: 8, fontSize: 12, color: 'var(--warning)' }}>
            Baseline window must be larger than the recent window.
          </div>
        )}
      </div>

      {err && (
        <div className="card" style={{ borderColor: 'var(--error)', color: 'var(--error)' }}>
          {err}
        </div>
      )}

      {loaded && !err && items.length === 0 && (
        <div className="card" style={{ color: 'var(--text-muted)' }}>
          No classifications in the baseline window for <strong>{product}</strong>. Try a longer
          baseline, a different product, or fetch first.
        </div>
      )}

      {rising.length > 0 && (
        <div className="card">
          <div className="card-title">🚨 Rising ({rising.length})</div>
          <table className="table">
            <thead>
              <tr>
                <th>Category</th>
                <th>Sub-category</th>
                <th>Recent</th>
                <th>Trailing avg / window</th>
                <th>Multiplier</th>
              </tr>
            </thead>
            <tbody>
              {rising.map((t) => (
                <tr key={`${t.main_category}/${t.sub_category}`}>
                  <td>{t.main_category}</td>
                  <td>{t.sub_category}</td>
                  <td>{t.recent_count}</td>
                  <td>{t.trailing_avg_per_window.toFixed(2)}</td>
                  <td>
                    <span className="badge badge-red">{t.multiplier.toFixed(2)}×</span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {quiet.length > 0 && (
        <div className="card">
          <div className="card-title" style={{ color: 'var(--text-muted)' }}>
            Steady ({quiet.length})
          </div>
          <table className="table">
            <thead>
              <tr>
                <th>Category</th>
                <th>Sub-category</th>
                <th>Recent</th>
                <th>Trailing avg / window</th>
                <th>Multiplier</th>
              </tr>
            </thead>
            <tbody>
              {quiet.map((t) => (
                <tr key={`${t.main_category}/${t.sub_category}`}>
                  <td>{t.main_category}</td>
                  <td>{t.sub_category}</td>
                  <td>{t.recent_count}</td>
                  <td>{t.trailing_avg_per_window.toFixed(2)}</td>
                  <td>
                    <span className="badge badge-gray">{t.multiplier.toFixed(2)}×</span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  )
}
