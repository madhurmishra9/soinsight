import { useState } from 'react'
import { Lightbulb, Loader } from 'lucide-react'
import { getTagSuggestions } from '../api'
import { errorMessage } from '../api/client'
import { useApp } from '../context/AppContext'
import type { TagSuggestion } from '../types/api'

function coverageBadge(ratio: number): string {
  if (ratio >= 0.5) return 'badge badge-green'
  if (ratio >= 0.1) return 'badge badge-amber'
  return 'badge badge-gray'
}

export function TagSuggestionsPage() {
  const { settings, knownProducts } = useApp()
  const seed = [
    ...new Set(
      [...(settings.default_tags || '').split(','), ...knownProducts]
        .map((t) => t.trim())
        .filter(Boolean),
    ),
  ].join(', ')

  const [tracked, setTracked] = useState(seed)
  const [minInstance, setMinInstance] = useState(25)
  const [limit, setLimit] = useState(20)
  const [items, setItems] = useState<TagSuggestion[]>([])
  const [loaded, setLoaded] = useState(false)
  const [loading, setLoading] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  const run = async () => {
    setLoading(true)
    setErr(null)
    try {
      const trackedList = tracked.split(',').map((t) => t.trim()).filter(Boolean)
      const r = await getTagSuggestions(trackedList, {
        min_instance_count: minInstance,
        limit,
      })
      setItems(r.data)
      setLoaded(true)
    } catch (e) {
      setErr(errorMessage(e))
    } finally {
      setLoading(false)
    }
  }

  return (
    <>
      <div className="page-header">
        <div className="page-title">Tag auto-discovery</div>
        <div className="page-subtitle">
          Tags on your SO instance that you aren’t tracking yet, ranked by instance-wide
          volume. Reads the cached tag index — if it’s empty, validate a tag on the Fetch
          page first to prime the cache.
        </div>
      </div>

      <div className="card" style={{ marginBottom: 16 }}>
        <div className="flex items-center gap-12" style={{ flexWrap: 'wrap' }}>
          <div style={{ flex: '2 1 300px' }}>
            <label style={{ display: 'block', marginBottom: 6 }}>
              Tracked tags (comma-separated — these will be excluded)
            </label>
            <input
              className="input"
              placeholder="cloudsql, cloudspanner, cloudstorage"
              value={tracked}
              onChange={(e) => setTracked(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') void run()
              }}
            />
          </div>
          <div style={{ width: 150 }}>
            <label style={{ display: 'block', marginBottom: 6 }}>Min instance count</label>
            <input
              className="input"
              type="number"
              min={1}
              value={minInstance}
              onChange={(e) => setMinInstance(Number(e.target.value) || 1)}
            />
          </div>
          <div style={{ width: 100 }}>
            <label style={{ display: 'block', marginBottom: 6 }}>Limit</label>
            <input
              className="input"
              type="number"
              min={1}
              max={200}
              value={limit}
              onChange={(e) => setLimit(Number(e.target.value) || 20)}
            />
          </div>
          <div style={{ alignSelf: 'flex-end' }}>
            <button className="btn btn-primary" onClick={run} disabled={loading}>
              {loading ? <Loader size={14} className="spin" /> : <Lightbulb size={14} />} Suggest
            </button>
          </div>
        </div>
      </div>

      {err && (
        <div className="card" style={{ borderColor: 'var(--error)', color: 'var(--error)' }}>
          {err}
        </div>
      )}

      {loaded && !err && items.length === 0 && (
        <div className="card" style={{ color: 'var(--text-muted)' }}>
          No suggestions. Either the tag-index cache is empty (validate a tag on the Fetch page
          first), every instance tag meeting the threshold is already in your tracked list, or
          the threshold is too high.
        </div>
      )}

      {items.length > 0 && (
        <div className="card">
          <div className="card-title">Suggestions ({items.length})</div>
          <table className="table">
            <thead>
              <tr>
                <th>Tag</th>
                <th>Instance volume</th>
                <th>Local count</th>
                <th>Coverage</th>
              </tr>
            </thead>
            <tbody>
              {items.map((s) => (
                <tr key={s.tag}>
                  <td><code>{s.tag}</code></td>
                  <td>{s.instance_count.toLocaleString()}</td>
                  <td>{s.local_count.toLocaleString()}</td>
                  <td>
                    <span className={coverageBadge(s.coverage_ratio)}>
                      {(s.coverage_ratio * 100).toFixed(1)}%
                    </span>
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
