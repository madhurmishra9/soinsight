import { useState } from 'react'
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
import { Download, FileJson, FileText, Loader, RefreshCw, Users } from 'lucide-react'
import { getSummary, downloadReport } from '../api'
import { errorMessage } from '../api/client'
import { useApp } from '../context/AppContext'
import { CardSkeleton, StatsSkeleton } from '../components/Skeleton'
import type { InsightsSummary, CategoryBreakdownItem, PatternItem } from '../types/api'

const WINDOWS = [7, 14, 30, 60, 90]

const CAT_COLORS: Record<string, string> = {
  'Product': '#6366f1',
  'Documentation': '#0ea5e9',
  'Operational': '#f59e0b',
  'Awareness': '#8b5cf6',
  'Technical': '#ef4444',
  'Security / Compliance': '#10b981',
  'Adoption / Migration': '#f97316',
  'Misuse / Noise': '#94a3b8',
}

const COLOR_PALETTE = ['#6366f1', '#0ea5e9', '#f59e0b', '#8b5cf6', '#ef4444', '#10b981', '#f97316', '#94a3b8']

function catColor(name: string, idx: number) {
  return CAT_COLORS[name] ?? COLOR_PALETTE[idx % COLOR_PALETTE.length]
}

// ── Sub-components ─────────────────────────────────────────────────────────────

function StatCard({ value, label, sub }: { value: string | number; label: string; sub?: string }) {
  return (
    <div className="card stat">
      <div className="stat-value">{value}</div>
      <div className="stat-label">{label}</div>
      {sub && <div className="stat-note">{sub}</div>}
    </div>
  )
}

function CategoryChart({ breakdown }: { breakdown: CategoryBreakdownItem[] }) {
  // Aggregate by main category
  const byMain: Record<string, number> = {}
  for (const item of breakdown) {
    byMain[item.main_category] = (byMain[item.main_category] ?? 0) + item.question_count
  }
  const data = Object.entries(byMain)
    .sort(([, a], [, b]) => b - a)
    .map(([name, count], idx) => ({ name: name.length > 15 ? name.slice(0, 14) + '…' : name, count, color: catColor(name, idx), fullName: name }))

  if (!data.length) return <div className="text-muted text-sm">No data</div>

  return (
    <div className="chart-wrap">
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} margin={{ top: 4, right: 8, left: 0, bottom: 32 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#f1f5f9" />
          <XAxis
            dataKey="name"
            tick={{ fontSize: 11 }}
            angle={-25}
            textAnchor="end"
            interval={0}
          />
          <YAxis tick={{ fontSize: 11 }} allowDecimals={false} />
          <Tooltip
            formatter={(val, _name, entry) => [val ?? 0, (entry.payload as { fullName?: string } | undefined)?.fullName ?? '']}
          />
          <Bar dataKey="count" radius={[4, 4, 0, 0]}>
            {data.map((d, i) => <Cell key={i} fill={d.color} />)}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  )
}

function FrequencyChart({ breakdown }: { breakdown: CategoryBreakdownItem[] }) {
  const top = [...breakdown].slice(0, 8).map((item, idx) => ({
    name: item.sub_category.length > 28 ? item.sub_category.slice(0, 27) + '…' : item.sub_category,
    fullName: item.sub_category,
    count: item.question_count,
    color: catColor(item.main_category, idx),
  }))

  if (!top.length) return <div className="text-muted text-sm">No data</div>

  return (
    <div className="chart-wrap">
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={top} layout="vertical" margin={{ top: 4, right: 16, left: 8, bottom: 4 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#f1f5f9" horizontal={false} />
          <XAxis type="number" tick={{ fontSize: 11 }} allowDecimals={false} />
          <YAxis type="category" dataKey="name" tick={{ fontSize: 11 }} width={180} />
          <Tooltip
            formatter={(val, _name, entry) => [val ?? 0, (entry.payload as { fullName?: string } | undefined)?.fullName ?? '']}
          />
          <Bar dataKey="count" radius={[0, 4, 4, 0]}>
            {top.map((d, i) => <Cell key={i} fill={d.color} />)}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  )
}

function TopIssues({ issues }: { issues: CategoryBreakdownItem[] }) {
  if (!issues.length) return <div className="text-muted text-sm">No issues to display</div>
  return (
    <ol style={{ paddingLeft: 18 }}>
      {issues.map((item, i) => (
        <li key={i} style={{ marginBottom: 10 }}>
          <div className="flex items-center gap-8">
            <span style={{ fontWeight: 600 }}>{item.main_category} / {item.sub_category}</span>
            <span className="badge badge-blue">{item.question_count}q</span>
            <span className="badge badge-gray"><Users size={10} /> {item.distinct_users}</span>
          </div>
        </li>
      ))}
    </ol>
  )
}

function PatternsSection({ patterns }: { patterns: PatternItem[] }) {
  if (!patterns.length) {
    return (
      <div className="alert alert-info" style={{ marginBottom: 0 }}>
        No patterns yet — run analysis first (patterns require ≥3 questions from ≥2 distinct users).
      </div>
    )
  }
  return (
    <>
      {patterns.map((p, i) => (
        <div key={i} className="pattern-card">
          <div className="pattern-header">
            <span className="pattern-title">{p.main_category}: {p.sub_category}</span>
            <div className="flex gap-8">
              <span className="badge badge-blue">{p.question_count} questions</span>
              <span className="badge badge-gray"><Users size={10} /> {p.distinct_users} users</span>
            </div>
          </div>
          {p.summary && <div className="pattern-meta">{p.summary}</div>}
          {p.suggested_action && (
            <div className="pattern-action">
              <Download size={12} /> {p.suggested_action}
            </div>
          )}
        </div>
      ))}
    </>
  )
}

function TechnicalSplit({ techRatio, nonTechRatio }: { techRatio: number | null; nonTechRatio: number | null }) {
  if (techRatio === null) return <div className="text-muted text-sm">No data</div>

  const tech = Math.round(techRatio * 100)
  const nonTech = Math.round((nonTechRatio ?? 0) * 100)

  const data = [
    { name: `Technical (${tech}%)`, value: tech, fill: '#2563eb' },
    { name: `Non-technical (${nonTech}%)`, value: nonTech, fill: '#94a3b8' },
  ]

  return (
    <div>
      <div className="chart-wrap-sm">
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={data} margin={{ top: 4, right: 8, left: 0, bottom: 4 }}>
            <XAxis dataKey="name" tick={{ fontSize: 11 }} />
            <YAxis tick={{ fontSize: 11 }} unit="%" domain={[0, 100]} />
            <Tooltip formatter={(v) => [`${v ?? 0}%`]} />
            <Legend />
            <Bar dataKey="value" radius={[4, 4, 0, 0]}>
              {data.map((d, i) => <Cell key={i} fill={d.fill} />)}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>
      <div className="text-sm text-muted mt-8" style={{ fontStyle: 'italic' }}>
        ⚠ APPROXIMATE — based on question tags, not verified user profiles.
      </div>
    </div>
  )
}

// ── Main page ─────────────────────────────────────────────────────────────────

export function DashboardPage() {
  const { knownProducts } = useApp()
  const [product, setProduct] = useState(knownProducts[0] ?? '')
  const [windowDays, setWindowDays] = useState(30)
  const [summary, setSummary] = useState<InsightsSummary | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const load = async () => {
    if (!product.trim()) return
    setLoading(true)
    setError(null)
    try {
      const res = await getSummary(product.trim(), windowDays)
      setSummary(res.data)
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setLoading(false)
    }
  }

  const handleExport = (fmt: 'json' | 'md') => {
    if (!product.trim()) return
    downloadReport(product.trim(), windowDays, fmt)
  }

  return (
    <>
      <div className="page-header">
        <div className="flex items-center justify-between">
          <div>
            <div className="page-title">Dashboard</div>
            <div className="page-subtitle">Per-product category analysis and pattern insights</div>
          </div>
          {summary && (
            <div className="btn-group">
              <button className="btn btn-secondary btn-sm" onClick={() => handleExport('json')}>
                <FileJson size={14} /> JSON
              </button>
              <button className="btn btn-secondary btn-sm" onClick={() => handleExport('md')}>
                <FileText size={14} /> Markdown
              </button>
            </div>
          )}
        </div>
      </div>

      {/* Filter bar */}
      <div className="card" style={{ marginBottom: 20 }}>
        <div className="flex items-center gap-12" style={{ flexWrap: 'wrap' }}>
          <div style={{ flex: '1 1 200px' }}>
            <label style={{ display: 'block', marginBottom: 6 }}>Product / tag</label>
            {knownProducts.length > 0 ? (
              <select
                className="select"
                value={product}
                onChange={(e) => setProduct(e.target.value)}
              >
                <option value="">Select a product…</option>
                {knownProducts.map((p) => <option key={p} value={p}>{p}</option>)}
              </select>
            ) : (
              <input
                className="input"
                placeholder="e.g. python"
                value={product}
                onChange={(e) => setProduct(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && void load()}
              />
            )}
          </div>
          <div>
            <label style={{ display: 'block', marginBottom: 6 }}>Window</label>
            <div className="window-tabs">
              {WINDOWS.map((w) => (
                <button
                  key={w}
                  className={`window-tab${windowDays === w ? ' active' : ''}`}
                  onClick={() => setWindowDays(w)}
                >
                  {w}d
                </button>
              ))}
            </div>
          </div>
          <div style={{ paddingTop: 22 }}>
            <button
              className="btn btn-primary"
              onClick={load}
              disabled={loading || !product.trim()}
            >
              {loading ? <Loader size={14} className="spin" /> : <RefreshCw size={14} />}
              {loading ? 'Loading…' : 'Load'}
            </button>
          </div>
        </div>
      </div>

      {error && <div className="alert alert-error">{error}</div>}

      {loading && (
        <>
          <StatsSkeleton />
          <div style={{ marginTop: 16 }}>
            <CardSkeleton />
          </div>
        </>
      )}

      {!loading && summary && (
        <>
          {/* Stats row */}
          <div className="grid-4" style={{ marginBottom: 16 }}>
            <StatCard value={summary.total_questions} label="Signal questions" />
            <StatCard value={summary.noise_count} label="Noise volume" />
            <StatCard value={summary.patterns.length} label="Patterns detected" />
            <StatCard
              value={summary.technical_ratio !== null ? `${Math.round(summary.technical_ratio * 100)}%` : '—'}
              label="Technical questions"
              sub="APPROXIMATE"
            />
          </div>

          {/* Charts row */}
          <div className="grid-2" style={{ marginBottom: 16 }}>
            <div className="card">
              <div className="card-title">Category distribution</div>
              <CategoryChart breakdown={summary.category_breakdown} />
            </div>
            <div className="card">
              <div className="card-title">Sub-category frequency</div>
              <div className="card-subtitle">Top 8 sub-categories by volume</div>
              <FrequencyChart breakdown={summary.category_breakdown} />
            </div>
          </div>

          <div className="grid-2" style={{ marginBottom: 16 }}>
            {/* Top issues */}
            <div className="card">
              <div className="card-title">Top issues</div>
              <TopIssues issues={summary.top_issues} />
            </div>

            {/* Technical split */}
            <div className="card">
              <div className="card-title">Technical / Non-technical split</div>
              <TechnicalSplit techRatio={summary.technical_ratio} nonTechRatio={summary.non_technical_ratio} />
            </div>
          </div>

          {/* Patterns */}
          <div className="card">
            <div className="card-title">Key patterns</div>
            <div className="card-subtitle">
              Clusters with ≥3 questions from ≥2 distinct users · recommended action per pattern
            </div>
            <PatternsSection patterns={summary.patterns} />
          </div>

          {/* Recommended actions */}
          {summary.recommended_actions.length > 0 && (
            <div className="card" style={{ marginTop: 16 }}>
              <div className="card-title">Recommended actions</div>
              <ol style={{ paddingLeft: 20, lineHeight: 1.8 }}>
                {summary.recommended_actions.map((action, i) => (
                  <li key={i} style={{ color: 'var(--text)', fontSize: 14 }}>{action}</li>
                ))}
              </ol>
            </div>
          )}
        </>
      )}

      {!loading && !summary && !error && (
        <div className="card">
          <div className="alert alert-info" style={{ marginBottom: 0 }}>
            Select a product and click <strong>Load</strong> to view insights.
            Run <strong>Fetch</strong> + <strong>Analysis</strong> first if you haven't already.
          </div>
        </div>
      )}
    </>
  )
}
