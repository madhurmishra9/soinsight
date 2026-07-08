import { api } from './client'
import type {
  AnalysisRequest,
  AvailableTagsResponse,
  ConnectionTestResult,
  DismissRequest,
  DismissedItem,
  FetchRequest,
  InsightsSummary,
  MetricBucket,
  MetricQuestionRef,
  MetricsSummary,
  PatternItem,
  QuestionRef,
  RemediationItem,
  RunItem,
  RunResponse,
  SettingsPayload,
  TagCoverage,
  TagSuggestion,
  TagValidation,
  TrendItem,
} from '../types/api'

// ── Settings ──────────────────────────────────────────────────────────────────

export const saveSettings = (payload: SettingsPayload) =>
  api.post<SettingsPayload>('/api/settings', payload)

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

export const getCoverage = (products: string[]) =>
  api.get<TagCoverage[]>('/api/questions/coverage', { params: { products: products.join(',') } })

export const validateTags = (tags: string[]) =>
  api.get<TagValidation[]>('/api/questions/validate-tags', { params: { tags: tags.join(',') } })

export const getAvailableTags = (search = '', limit = 1000) =>
  api.get<AvailableTagsResponse>('/api/questions/available-tags', { params: { search, limit } })

// ── Analysis ──────────────────────────────────────────────────────────────────

export const startAnalysis = (payload: AnalysisRequest) =>
  api.post<RunResponse>('/api/analysis/start', payload)

export const analysisStreamUrl = (runId: string) =>
  `/api/analysis/stream?run_id=${encodeURIComponent(runId)}`

// ── Remediation (grounded fix guides) ─────────────────────────────────────────

export const getRemediations = (product: string, window: number) =>
  api.get<RemediationItem[]>('/api/remediation', { params: { product, window } })

export const generateRemediations = (payload: {
  products: string[]
  window_days: number
  from_date?: string
  to_date?: string
  regenerate?: boolean
}) => api.post<RunResponse>('/api/remediation/generate', payload)

export const remediationStreamUrl = (runId: string) =>
  `/api/remediation/stream?run_id=${encodeURIComponent(runId)}`

// ── Insights ──────────────────────────────────────────────────────────────────

export const getSummary = (product: string, window: number, fromDate?: string, toDate?: string) =>
  api.get<InsightsSummary>('/api/insights/summary', { params: { product, window, from_date: fromDate, to_date: toDate } })

export const getPatterns = (product?: string, window?: number) =>
  api.get<PatternItem[]>('/api/insights/patterns', { params: { product, window } })

export const getQuestions = (product: string, window: number, main: string, sub?: string, fromDate?: string, toDate?: string, noise?: boolean) =>
  api.get<QuestionRef[]>('/api/insights/questions', { params: { product, window, main, sub, from_date: fromDate, to_date: toDate, noise: noise || undefined } })

export const getTechnicalQuestions = (product: string, window: number, technical: boolean, fromDate?: string, toDate?: string) =>
  api.get<QuestionRef[]>('/api/insights/technical-questions', {
    params: { product, window, technical, from_date: fromDate, to_date: toDate },
  })

export const reportUrl = (
  product: string,
  window: number,
  format: 'md' | 'json' | 'pdf',
  fromDate?: string,
  toDate?: string,
) => {
  const p = new URLSearchParams({ product, window: String(window), format })
  if (fromDate) p.set('from_date', fromDate)
  if (toDate) p.set('to_date', toDate)
  return `/api/insights/report?${p.toString()}`
}

export const downloadReport = (
  product: string,
  window: number,
  format: 'md' | 'json' | 'pdf',
  fromDate?: string,
  toDate?: string,
) => {
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

// ── F4 Run history ────────────────────────────────────────────────────────────

export const getRuns = (limit = 50, offset = 0, status?: string) =>
  api.get<RunItem[]>('/api/runs', { params: { limit, offset, status } })

// ── F1 Pattern snooze/dismiss ─────────────────────────────────────────────────

export const dismissPattern = (payload: DismissRequest) =>
  api.post<DismissedItem>('/api/patterns/dismiss', payload)

export const restorePattern = (product: string, main: string, sub: string) =>
  api.delete<void>('/api/patterns/dismiss', { params: { product, main, sub } })

export const listDismissed = (product?: string, includeExpired = false) =>
  api.get<DismissedItem[]>('/api/patterns/dismiss', {
    params: { product, include_expired: includeExpired || undefined },
  })

// ── F2 Rising-volume trends ───────────────────────────────────────────────────

export const getTrends = (
  product: string,
  opts: { recent_days?: number; baseline_days?: number; threshold?: number; min_recent?: number } = {},
) => api.get<TrendItem[]>('/api/insights/trends', { params: { product, ...opts } })

// ── Metrics (pipeline health) ─────────────────────────────────────────────────

export const getMetrics = (
  tags: string[],
  window: number,
  fromDate?: string,
  toDate?: string,
) =>
  api.get<MetricsSummary>('/api/insights/metrics', {
    params: { tags: tags.join(','), window, from_date: fromDate, to_date: toDate },
  })

export const getMetricQuestions = (
  bucket: MetricBucket,
  tags: string[],
  window: number,
  fromDate?: string,
  toDate?: string,
) =>
  api.get<MetricQuestionRef[]>('/api/insights/metrics/questions', {
    params: { bucket, tags: tags.join(','), window, from_date: fromDate, to_date: toDate },
  })

// ── F3 Tag auto-discovery ─────────────────────────────────────────────────────

export const getTagSuggestions = (
  tracked: string[],
  opts: { min_instance_count?: number; limit?: number } = {},
) =>
  api.get<TagSuggestion[]>('/api/insights/tag-suggestions', {
    params: { tracked: tracked.join(','), ...opts },
  })

// Updated summary signature: support include_dismissed for showing snoozed items.
export const getSummaryWithDismissed = (
  product: string,
  window: number,
  fromDate?: string,
  toDate?: string,
  includeDismissed?: boolean,
) =>
  api.get<InsightsSummary>('/api/insights/summary', {
    params: {
      product,
      window,
      from_date: fromDate,
      to_date: toDate,
      include_dismissed: includeDismissed || undefined,
    },
  })
