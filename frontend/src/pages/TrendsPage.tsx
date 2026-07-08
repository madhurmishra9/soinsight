import { useEffect, useState } from 'react'
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { Loader, TrendingUp } from 'lucide-react'
import { getTrends } from '../api'
import { errorMessage } from '../api/client'
import { useApp } from '../context/AppContext'
import { usePageState } from '../context/PageStateContext'
import { cssVar, useThemeTick } from '../hooks/useTheme'
import type { TrendItem } from '../types/api'

const MAX_CHART_ITEMS = 15

/** Grouped bar chart: recent volume vs. trailing-baseline average, per category.
 *  Bars are colored by rising/steady so the multiplier table above reads visually. */
function TrendChart({ items }: { items: TrendItem[] }) {
  useThemeTick()
  const data = [...items]
    .sort((a, b) => (b.is_rising === a.is_rising ? b.multiplier - a.multiplier : b.is_rising ? 1 : -1))
    .slice(0, MAX_CHART_ITEMS)
    .map((t) => {
      const label = `${t.main_category} / ${t.sub_category}`
      return {
        name: label.length > 30 ? label.slice(0, 29) + '…' : label,
        fullName: label,
        recent: t.recent_count,
        trailing: t.trailing_avg_per_window,
        multiplier: t.multiplier,
        rising: t.is_rising,
      }
    })

  if (!data.length) return null

  return (
    <div className="card" style={{ marginBottom: 16 }}>
      <div className="card-title">Recent vs. trailing baseline (visual)</div>
      <div className="card-subtitle">
        Each pair of bars is one category. The taller the <strong>Recent</strong> bar is compared
        to <strong>Trailing avg</strong>, the sharper the spike — categories flagged 🚨 rising are
        outlined in red. Showing top {Math.min(MAX_CHART_ITEMS, items.length)} of {items.length} by multiplier.
      </div>
      <div className="chart-wrap" style={{ height: 370 }}>
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={data} margin={{ top: 4, right: 8, left: 0, bottom: 80 }}>
            <CartesianGrid strokeDasharray="3 3" stroke={cssVar('--grid')} />
            <XAxis
              dataKey="name"
              tick={{ fontSize: 11, fill: cssVar('--axis') }}
              angle={-35}
              textAnchor="end"
              interval={0}
              height={90}
            />
            <YAxis tick={{ fontSize: 11, fill: cssVar('--axis') }} allowDecimals={false} />
            <Tooltip
              contentStyle={{ background: cssVar('--tooltip-bg'), border: `1px solid ${cssVar('--border')}`, color: cssVar('--text') }}
              formatter={(val, name, entry) => {
                const p = entry.payload as { fullName?: string; multiplier?: number }
                const label = name === 'recent' ? 'Recent' : 'Trailing avg / window'
                return [val ?? 0, `${label} — ${p.fullName ?? ''} (${(p.multiplier ?? 0).toFixed(2)}×)`]
              }}
            />
            <Legend
              verticalAlign="top"
              height={28}
              wrapperStyle={{ color: cssVar('--text') }}
              formatter={(value) => (value === 'recent' ? 'Recent count' : 'Trailing avg / window')}
            />
            <Bar dataKey="recent" name="recent" radius={[4, 4, 0, 0]}>
              {data.map((d, i) => (
                <Cell
                  key={i}
                  fill={d.rising ? '#ef4444' : '#6366f1'}
                  stroke={d.rising ? '#991b1b' : 'transparent'}
                  strokeWidth={d.rising ? 2 : 0}
                />
              ))}
            </Bar>
            <Bar dataKey="trailing" name="trailing" fill={cssVar('--text-muted')} radius={[4, 4, 0, 0]} />
          </BarChart>
        </ResponsiveContainer>
      </div>
    </div>
  )
}

export function TrendsPage() {
  const { settings } = useApp()
  const { trends, patchTrends } = usePageState()
  const { product, opts, items, loaded } = trends
  const [loading, setLoading] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  // Seed the product field from default_tags the first time only, if empty.
  useEffect(() => {
    if (product || loaded) return
    const initialProduct =
      (settings.default_tags || '').split(',').map((t) => t.trim()).filter(Boolean)[0] || ''
    if (initialProduct) patchTrends({ product: initialProduct })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [settings.default_tags])

  const setProduct = (p: string) => patchTrends({ product: p })
  const setOpts = (o: typeof opts) => patchTrends({ opts: o })

  const run = async () => {
    if (!product.trim()) return
    setLoading(true)
    setErr(null)
    try {
      const r = await getTrends(product.trim(), opts)
      patchTrends({ items: r.data, loaded: true })
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

      {items.length > 0 && <TrendChart items={items} />}

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
