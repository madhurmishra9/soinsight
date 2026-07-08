import { useEffect, useState } from 'react'
import { Check, ChevronDown, ChevronRight, X } from 'lucide-react'
import { getQuestions, getTechnicalQuestions } from '../api'
import type { QuestionRef } from '../types/api'

export type Drill = {
  main?: string
  sub?: string
  label: string
  noise?: boolean
  /** When set, the drawer lists the Technical/Non-technical split instead of a category. */
  technical?: boolean
}

interface QuestionDrawerProps {
  target: Drill | null
  product: string
  windowDays: number
  onClose: () => void
  fromDate?: string
  toDate?: string
  noise?: boolean
}

/** Strip HTML tags so SO answer bodies render as readable plain text. */
function stripHtml(html: string): string {
  const tmp = document.createElement('div')
  tmp.innerHTML = html
  return (tmp.textContent || tmp.innerText || '').trim()
}

function AnswerList({ q }: { q: QuestionRef }) {
  const [open, setOpen] = useState(false)
  const answers = q.answers ?? []
  const count = q.answer_count ?? answers.length

  if (!count) return <span className="muted"> · no answers</span>
  if (!answers.length) return <span className="muted"> · {count} answers</span>

  return (
    <div style={{ marginTop: 4 }}>
      <button
        className="btn btn-ghost btn-sm"
        onClick={() => setOpen((o) => !o)}
        style={{ padding: '2px 6px', gap: 4 }}
      >
        {open ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
        {count} answer{count === 1 ? '' : 's'}
      </button>
      {open && (
        <ul style={{ listStyle: 'none', margin: '6px 0 0', padding: 0, display: 'flex', flexDirection: 'column', gap: 8 }}>
          {answers.map((a) => (
            <li
              key={a.so_id}
              style={{
                borderLeft: '2px solid var(--border, #ccc)',
                paddingLeft: 10,
                fontSize: 13,
              }}
            >
              <div className="muted" style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 2 }}>
                {a.is_accepted && <span className="badge badge-green" style={{ display: 'inline-flex', alignItems: 'center', gap: 2 }}><Check size={10} /> accepted</span>}
                <span>[A#{a.so_id}] · score {a.score}</span>
              </div>
              <div style={{ whiteSpace: 'pre-wrap', maxHeight: 220, overflow: 'auto' }}>
                {stripHtml(a.body)}
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

export function QuestionDrawer({ target, product, windowDays, onClose, fromDate, toDate, noise }: QuestionDrawerProps) {
  const [items, setItems] = useState<QuestionRef[] | null>(null)
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    if (!target) return
    setLoading(true)
    setItems(null)
    const req = target.technical !== undefined
      ? getTechnicalQuestions(product, windowDays, target.technical, fromDate, toDate)
      : getQuestions(product, windowDays, target.main ?? '', target.sub, fromDate, toDate, noise || target.noise)
    req
      .then((r) => setItems(r.data))
      .catch(() => setItems([]))
      .finally(() => setLoading(false))
  }, [target, product, windowDays, fromDate, toDate, noise])

  if (!target) return null

  return (
    <div className="drawer-overlay" onClick={onClose}>
      <div className="drawer" onClick={(e) => e.stopPropagation()}>
        <div className="drawer-header">
          <span>{target.label || (target.noise ? 'Noise / Excluded Questions' : `${target.main}${target.sub ? ` / ${target.sub}` : ''}`)}</span>
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
                <span className="muted"> · score {q.score} · {q.view_count} views</span>
                <AnswerList q={q} />
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  )
}
