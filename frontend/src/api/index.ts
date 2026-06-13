import { api } from './client'
import type {
  AnalysisRequest,
  ConnectionTestResult,
  FetchRequest,
  InsightsSummary,
  PatternItem,
  QuestionRef,
  RunResponse,
  SettingsPayload,
} from '../types/api'

// ── Settings ──────────────────────────────────────────────────────────────────

export const saveSettings = (payload: SettingsPayload) =>
  api.post<{ status: string }>('/api/settings', payload)

export const testConnection = () =>
  api.get<ConnectionTestResult>('/api/settings/test')

export const getSettings = () =>
  api.get<SettingsPayload>('/api/settings')

export const getOllamaModels = () =>
  api.get<{ models: string[] }>('/api/settings/ollama-models')

// ── Questions / Fetch ─────────────────────────────────────────────────────────

export const fetchQuestions = (payload: FetchRequest) =>
  api.post<RunResponse>('/api/questions/fetch', payload)

export const questionStreamUrl = (runId: string) =>
  `/api/questions/stream?run_id=${encodeURIComponent(runId)}`

// ── Analysis ──────────────────────────────────────────────────────────────────

export const startAnalysis = (payload: AnalysisRequest) =>
  api.post<RunResponse>('/api/analysis/start', payload)

export const analysisStreamUrl = (runId: string) =>
  `/api/analysis/stream?run_id=${encodeURIComponent(runId)}`

// ── Insights ──────────────────────────────────────────────────────────────────

export const getSummary = (product: string, window: number, fromDate?: string, toDate?: string) =>
  api.get<InsightsSummary>('/api/insights/summary', { params: { product, window, from_date: fromDate, to_date: toDate } })

export const getPatterns = (product?: string, window?: number) =>
  api.get<PatternItem[]>('/api/insights/patterns', { params: { product, window } })

export const getQuestions = (product: string, window: number, main: string, sub?: string, fromDate?: string, toDate?: string, noise?: boolean) =>
  api.get<QuestionRef[]>('/api/insights/questions', { params: { product, window, main, sub, from_date: fromDate, to_date: toDate, noise: noise || undefined } })

export const reportUrl = (product: string, window: number, format: 'md' | 'json', fromDate?: string, toDate?: string) => {
  const p = new URLSearchParams({ product, window: String(window), format })
  if (fromDate) p.set('from_date', fromDate)
  if (toDate) p.set('to_date', toDate)
  return `/api/insights/report?${p.toString()}`
}

export const downloadReport = (product: string, window: number, format: 'md' | 'json', fromDate?: string, toDate?: string) => {
  const a = document.createElement('a')
  a.href = reportUrl(product, window, format, fromDate, toDate)
  const rangeLabel = fromDate && toDate
    ? `${fromDate}_to_${toDate}`
    : fromDate
    ? `${fromDate}_to_now`
    : `${window}d`
  a.download = `report_${product}_${rangeLabel}.${format}`
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
}
