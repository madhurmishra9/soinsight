import { useEffect, useState } from 'react'
import { X } from 'lucide-react'
import { getQuestions } from '../api'
import type { QuestionRef } from '../types/api'

export type Drill = { main: string; sub?: string; label: string }

interface QuestionDrawerProps {
  target: Drill | null
  product: string
  windowDays: number
  onClose: () => void
}

export function QuestionDrawer({ target, product, windowDays, onClose }: QuestionDrawerProps) {
  const [items, setItems] = useState<QuestionRef[] | null>(null)
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    if (!target) return
    setLoading(true)
    setItems(null)
    getQuestions(product, windowDays, target.main, target.sub)
      .then((r) => setItems(r.data))
      .catch(() => setItems([]))
      .finally(() => setLoading(false))
  }, [target, product, windowDays])

  if (!target) return null

  return (
    <div className="drawer-overlay" onClick={onClose}>
      <div className="drawer" onClick={(e) => e.stopPropagation()}>
        <div className="drawer-header">
          <span>{target.main}{target.sub ? ` / ${target.sub}` : ''}</span>
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
                <span className="muted"> · score {q.score} · {q.view_count} views</span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  )
}
