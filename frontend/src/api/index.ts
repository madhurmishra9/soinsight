import { api } from './client'
import type {
  AnalysisRequest,
  ConnectionTestResult,
  FetchRequest,
  InsightsSummary,
  PatternItem,
  RunResponse,
  SettingsPayload,
} from '../types/api'

// ── Settings ──────────────────────────────────────────────────────────────────

export const saveSettings = (payload: SettingsPayload) =>
  api.post<{ status: string }>('/api/settings', payload)

export const testConnection = () =>
  api.get<ConnectionTestResult>('/api/settings/test')

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

export const getSummary = (product: string, window: number) =>
  api.get<InsightsSummary>('/api/insights/summary', { params: { product, window } })

export const getPatterns = (product?: string, window?: number) =>
  api.get<PatternItem[]>('/api/insights/patterns', { params: { product, window } })

export const reportUrl = (product: string, window: number, format: 'md' | 'json') =>
  `/api/insights/report?product=${encodeURIComponent(product)}&window=${window}&format=${format}`

export const downloadReport = (product: string, window: number, format: 'md' | 'json') => {
  const a = document.createElement('a')
  a.href = reportUrl(product, window, format)
  a.download = `report_${product}_${window}d.${format}`
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
}
