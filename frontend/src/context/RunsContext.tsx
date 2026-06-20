import { createContext, useContext, useEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import {
  fetchQuestions,
  questionStreamUrl,
  startAnalysis as apiStartAnalysis,
  analysisStreamUrl,
  generateRemediations as apiGenerateRemediations,
  remediationStreamUrl,
} from '../api'
import { connectSSE } from '../api/sse'
import { errorMessage } from '../api/client'
import { useApp } from './AppContext'
import type { SseEvent } from '../types/api'

/**
 * RunsContext lifts the Fetch and Analysis *operations* (their form inputs,
 * progress logs, run state, and — crucially — the live SSE connection) out of
 * the page components and into a provider mounted ABOVE the router.
 *
 * Because this provider never unmounts when you switch tabs, an in-flight fetch
 * or analysis keeps streaming and its log keeps filling even while you are on a
 * different page. State is also mirrored to sessionStorage, so a full page
 * reload restores the log/form and reconnects any run that was still in flight.
 */

export interface LogEntry {
  ts: string
  msg: string
  kind: 'info' | 'done' | 'error'
}

export interface TagSummary {
  tag: string
  patterns: number
  total: number
  noise: number
}

export interface FetchState {
  tags: string[]
  windowDays: number
  fromDate: string
  toDate: string
  incremental: boolean
  running: boolean
  log: LogEntry[]
  error: string | null
  runId: string | null
}

export interface AnalysisState {
  tags: string[]
  windowDays: number
  fromDate: string
  toDate: string
  running: boolean
  log: LogEntry[]
  error: string | null
  runId: string | null
  tagSummaries: TagSummary[]
}

export interface RemediationState {
  running: boolean
  log: LogEntry[]
  error: string | null
  runId: string | null
  product: string
  windowDays: number
  // Bumped each time a run completes, so the dashboard knows to refetch.
  completedToken: number
}

interface RunsContextType {
  fetch: FetchState
  analysis: AnalysisState
  remediation: RemediationState
  patchFetch: (p: Partial<FetchState>) => void
  patchAnalysis: (p: Partial<AnalysisState>) => void
  startFetch: () => Promise<void>
  startAnalysis: () => Promise<void>
  startRemediation: (opts: {
    product: string
    windowDays: number
    fromDate?: string
    toDate?: string
    regenerate?: boolean
  }) => Promise<void>
}

const LOG_CAP = 500
const FETCH_KEY = 'soinsight.runs.fetch'
const ANALYSIS_KEY = 'soinsight.runs.analysis'
const REMEDIATION_KEY = 'soinsight.runs.remediation'

function now() {
  return new Date().toLocaleTimeString('en-GB', { hour12: false })
}

const defaultFetch: FetchState = {
  tags: [],
  windowDays: 30,
  fromDate: '',
  toDate: '',
  incremental: true,
  running: false,
  log: [],
  error: null,
  runId: null,
}

const defaultAnalysis: AnalysisState = {
  tags: [],
  windowDays: 30,
  fromDate: '',
  toDate: '',
  running: false,
  log: [],
  error: null,
  runId: null,
  tagSummaries: [],
}

const defaultRemediation: RemediationState = {
  running: false,
  log: [],
  error: null,
  runId: null,
  product: '',
  windowDays: 30,
  completedToken: 0,
}

function restore<T>(key: string, fallback: T): T {
  try {
    const raw = sessionStorage.getItem(key)
    if (!raw) return fallback
    return { ...fallback, ...(JSON.parse(raw) as Partial<T>) }
  } catch {
    return fallback
  }
}

const RunsContext = createContext<RunsContextType>({
  fetch: defaultFetch,
  analysis: defaultAnalysis,
  remediation: defaultRemediation,
  patchFetch: () => undefined,
  patchAnalysis: () => undefined,
  startFetch: async () => undefined,
  startAnalysis: async () => undefined,
  startRemediation: async () => undefined,
})

export function RunsProvider({ children }: { children: ReactNode }) {
  const { addProducts } = useApp()

  const [fetch, setFetch] = useState<FetchState>(() => restore(FETCH_KEY, defaultFetch))
  const [analysis, setAnalysis] = useState<AnalysisState>(() => restore(ANALYSIS_KEY, defaultAnalysis))
  const [remediation, setRemediation] = useState<RemediationState>(() => restore(REMEDIATION_KEY, defaultRemediation))

  // Latest-state refs so start* never reads stale form values from a closure.
  const fetchRef = useRef(fetch)
  const analysisRef = useRef(analysis)
  const remediationRef = useRef(remediation)
  useEffect(() => { fetchRef.current = fetch }, [fetch])
  useEffect(() => { analysisRef.current = analysis }, [analysis])
  useEffect(() => { remediationRef.current = remediation }, [remediation])

  // Live SSE handles — owned by the provider, closed only when a new run
  // starts or the whole app unmounts (never on tab navigation).
  const fetchSSE = useRef<(() => void) | null>(null)
  const analysisSSE = useRef<(() => void) | null>(null)
  const remediationSSE = useRef<(() => void) | null>(null)
  useEffect(() => () => { fetchSSE.current?.(); analysisSSE.current?.(); remediationSSE.current?.() }, [])

  // Persist snapshots so a reload can restore the log/form.
  useEffect(() => {
    try { sessionStorage.setItem(FETCH_KEY, JSON.stringify(fetch)) } catch { /* ignore */ }
  }, [fetch])
  useEffect(() => {
    try { sessionStorage.setItem(ANALYSIS_KEY, JSON.stringify(analysis)) } catch { /* ignore */ }
  }, [analysis])
  useEffect(() => {
    try { sessionStorage.setItem(REMEDIATION_KEY, JSON.stringify(remediation)) } catch { /* ignore */ }
  }, [remediation])

  const patchFetch = (p: Partial<FetchState>) => setFetch((prev) => ({ ...prev, ...p }))
  const patchAnalysis = (p: Partial<AnalysisState>) => setAnalysis((prev) => ({ ...prev, ...p }))

  const appendFetchLog = (entry: LogEntry) =>
    setFetch((prev) => ({ ...prev, log: [...prev.log, entry].slice(-LOG_CAP) }))
  const appendAnalysisLog = (entry: LogEntry) =>
    setAnalysis((prev) => ({ ...prev, log: [...prev.log, entry].slice(-LOG_CAP) }))
  const appendRemediationLog = (entry: LogEntry) =>
    setRemediation((prev) => ({ ...prev, log: [...prev.log, entry].slice(-LOG_CAP) }))

  // ── Fetch ──────────────────────────────────────────────────────────────────

  const wireFetchStream = (runId: string) => {
    fetchSSE.current?.()
    fetchSSE.current = connectSSE(
      questionStreamUrl(runId),
      (ev) => appendFetchLog(fetchEventToEntry(ev)),
      () => {
        appendFetchLog({ ts: now(), msg: '✓ Fetch complete.', kind: 'done' })
        setFetch((prev) => ({ ...prev, running: false }))
      },
      (msg) => {
        appendFetchLog({ ts: now(), msg: `Error: ${msg}`, kind: 'error' })
        setFetch((prev) => ({ ...prev, running: false, error: msg }))
      },
    )
  }

  const startFetch = async () => {
    const s = fetchRef.current
    if (!s.tags.length || s.running) return
    const rangeLabel = s.fromDate && s.toDate
      ? `${s.fromDate} → ${s.toDate}`
      : s.fromDate
      ? `${s.fromDate} → now`
      : `last ${s.windowDays}d`
    setFetch((prev) => ({
      ...prev,
      running: true,
      error: null,
      runId: null,
      log: [{ ts: now(), msg: `Starting fetch for [${s.tags.join(', ')}] over ${rangeLabel}…`, kind: 'info' }],
    }))

    try {
      const res = await fetchQuestions({
        products: s.tags,
        window_days: s.windowDays,
        from_date: s.fromDate || undefined,
        to_date: s.toDate || undefined,
        incremental: s.incremental,
      })
      const { run_id } = res.data
      addProducts(s.tags)
      setFetch((prev) => ({ ...prev, runId: run_id }))
      appendFetchLog({ ts: now(), msg: `Run ${run_id} started, streaming…`, kind: 'info' })
      wireFetchStream(run_id)
    } catch (err) {
      const msg = errorMessage(err)
      appendFetchLog({ ts: now(), msg: `Error: ${msg}`, kind: 'error' })
      setFetch((prev) => ({ ...prev, running: false, error: msg }))
    }
  }

  // ── Analysis ─────────────────────────────────────────────────────────────────

  const handleAnalysisEvent = (ev: SseEvent) => {
    if (ev.type === 'tag_start') {
      appendAnalysisLog({ ts: now(), msg: `▶ Processing tag: ${String(ev.tag)}`, kind: 'info' })
    } else if (ev.type === 'tag_done') {
      const tag = String(ev.tag)
      const patterns = Number(ev.patterns ?? 0)
      const total = Number(ev.total ?? 0)
      const noise = Number(ev.noise ?? 0)
      setAnalysis((prev) => ({
        ...prev,
        tagSummaries: [...prev.tagSummaries, { tag, patterns, total, noise }],
        log: [...prev.log, { ts: now(), msg: `✓ ${tag}: ${total} signal, ${noise} noise, ${patterns} patterns`, kind: 'info' as const }].slice(-LOG_CAP),
      }))
    } else {
      const detail = Object.entries(ev).filter(([k]) => k !== 'type').map(([k, v]) => `${k}=${String(v)}`).join(' ')
      appendAnalysisLog({ ts: now(), msg: `[${ev.type}] ${detail}`, kind: 'info' })
    }
  }

  const wireAnalysisStream = (runId: string) => {
    analysisSSE.current?.()
    analysisSSE.current = connectSSE(
      analysisStreamUrl(runId),
      handleAnalysisEvent,
      () => {
        appendAnalysisLog({ ts: now(), msg: '✓ Analysis complete.', kind: 'done' })
        setAnalysis((prev) => ({ ...prev, running: false }))
      },
      (msg) => {
        appendAnalysisLog({ ts: now(), msg: `Error: ${msg}`, kind: 'error' })
        setAnalysis((prev) => ({ ...prev, running: false, error: msg }))
      },
    )
  }

  const startAnalysis = async () => {
    const s = analysisRef.current
    if (!s.tags.length || s.running) return
    const rangeLabel = s.fromDate && s.toDate
      ? `${s.fromDate} → ${s.toDate}`
      : s.fromDate
      ? `${s.fromDate} → now`
      : `last ${s.windowDays}d`
    setAnalysis((prev) => ({
      ...prev,
      running: true,
      error: null,
      runId: null,
      tagSummaries: [],
      log: [{ ts: now(), msg: `Starting analysis for [${s.tags.join(', ')}] over ${rangeLabel}…`, kind: 'info' }],
    }))

    try {
      const res = await apiStartAnalysis({
        products: s.tags,
        window_days: s.windowDays,
        from_date: s.fromDate || undefined,
        to_date: s.toDate || undefined,
      })
      addProducts(s.tags)
      setAnalysis((prev) => ({ ...prev, runId: res.data.run_id }))
      appendAnalysisLog({ ts: now(), msg: `Run ${res.data.run_id} started…`, kind: 'info' })
      wireAnalysisStream(res.data.run_id)
    } catch (err) {
      const msg = errorMessage(err)
      appendAnalysisLog({ ts: now(), msg: `Error: ${msg}`, kind: 'error' })
      setAnalysis((prev) => ({ ...prev, running: false, error: msg }))
    }
  }

  // ── Remediation ──────────────────────────────────────────────────────────────

  const handleRemediationEvent = (ev: SseEvent) => {
    if (ev.type === 'cluster_start') {
      appendRemediationLog({ ts: now(), msg: `▶ ${String(ev.cluster)}`, kind: 'info' })
    } else if (ev.type === 'cluster_done') {
      const cluster = String(ev.cluster)
      const grounded = ev.grounded ? 'grounded' : 'ungrounded'
      const cached = ev.cached ? ' (cached)' : ''
      appendRemediationLog({ ts: now(), msg: `✓ ${cluster} — ${grounded}${cached}`, kind: 'info' })
    } else if (ev.type === 'warning') {
      appendRemediationLog({ ts: now(), msg: `⚠ ${String(ev.message)}`, kind: 'error' })
    } else if (ev.type === 'info') {
      appendRemediationLog({ ts: now(), msg: String(ev.message ?? ''), kind: 'info' })
    } else {
      const detail = Object.entries(ev).filter(([k]) => k !== 'type').map(([k, v]) => `${k}=${String(v)}`).join(' ')
      appendRemediationLog({ ts: now(), msg: `[${ev.type}] ${detail}`, kind: 'info' })
    }
  }

  const wireRemediationStream = (runId: string) => {
    remediationSSE.current?.()
    remediationSSE.current = connectSSE(
      remediationStreamUrl(runId),
      handleRemediationEvent,
      () => {
        appendRemediationLog({ ts: now(), msg: '✓ Remediation guide ready.', kind: 'done' })
        setRemediation((prev) => ({ ...prev, running: false, completedToken: prev.completedToken + 1 }))
      },
      (msg) => {
        appendRemediationLog({ ts: now(), msg: `Error: ${msg}`, kind: 'error' })
        setRemediation((prev) => ({ ...prev, running: false, error: msg }))
      },
    )
  }

  const startRemediation: RunsContextType['startRemediation'] = async (opts) => {
    if (remediationRef.current.running || !opts.product) return
    setRemediation((prev) => ({
      ...prev,
      running: true,
      error: null,
      runId: null,
      product: opts.product,
      windowDays: opts.windowDays,
      log: [{
        ts: now(),
        msg: `Analysing questions & answers for "${opts.product}" (${opts.windowDays}d) to build grounded fixes…`,
        kind: 'info',
      }],
    }))

    try {
      const res = await apiGenerateRemediations({
        products: [opts.product],
        window_days: opts.windowDays,
        from_date: opts.fromDate || undefined,
        to_date: opts.toDate || undefined,
        regenerate: opts.regenerate ?? false,
      })
      setRemediation((prev) => ({ ...prev, runId: res.data.run_id }))
      appendRemediationLog({ ts: now(), msg: `Run ${res.data.run_id} started…`, kind: 'info' })
      wireRemediationStream(res.data.run_id)
    } catch (err) {
      const msg = errorMessage(err)
      appendRemediationLog({ ts: now(), msg: `Error: ${msg}`, kind: 'error' })
      setRemediation((prev) => ({ ...prev, running: false, error: msg }))
    }
  }

  // On first mount, reconnect any run that was still in flight when the page
  // was reloaded. If the run already finished (stream gone), the SSE helper's
  // error/done path clears the running flag cleanly.
  useEffect(() => {
    if (fetchRef.current.running && fetchRef.current.runId) {
      appendFetchLog({ ts: now(), msg: '↻ Reconnecting to in-progress fetch…', kind: 'info' })
      wireFetchStream(fetchRef.current.runId)
    }
    if (analysisRef.current.running && analysisRef.current.runId) {
      appendAnalysisLog({ ts: now(), msg: '↻ Reconnecting to in-progress analysis…', kind: 'info' })
      wireAnalysisStream(analysisRef.current.runId)
    }
    if (remediationRef.current.running && remediationRef.current.runId) {
      appendRemediationLog({ ts: now(), msg: '↻ Reconnecting to in-progress remediation…', kind: 'info' })
      wireRemediationStream(remediationRef.current.runId)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  return (
    <RunsContext.Provider
      value={{
        fetch, analysis, remediation,
        patchFetch, patchAnalysis,
        startFetch, startAnalysis, startRemediation,
      }}
    >
      {children}
    </RunsContext.Provider>
  )
}

function fetchEventToEntry(ev: SseEvent): LogEntry {
  if (ev.type === 'progress') {
    return { ts: now(), msg: String(ev.message ?? JSON.stringify(ev)), kind: 'info' }
  }
  const detail = Object.entries(ev)
    .filter(([k]) => k !== 'type')
    .map(([k, v]) => `${k}=${String(v)}`)
    .join(' ')
  if (ev.type === 'tag_done' || ev.type === 'page_done') {
    return { ts: now(), msg: detail, kind: 'info' }
  }
  return { ts: now(), msg: `[${ev.type}] ${detail}`, kind: 'info' }
}

export const useRuns = () => useContext(RunsContext)
