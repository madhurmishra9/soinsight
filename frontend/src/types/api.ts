export interface CategoryBreakdownItem {
  main_category: string
  sub_category: string
  question_count: number
  distinct_users: number
}

export interface PatternItem {
  main_category: string
  sub_category: string
  question_count: number
  distinct_users: number
  suggested_action: string | null
  first_seen: string | null
  last_seen: string | null
  summary: string | null
}

export interface InsightsSummary {
  product: string
  window_days: number
  total_questions: number
  noise_count: number
  category_breakdown: CategoryBreakdownItem[]
  top_issues: CategoryBreakdownItem[]
  patterns: PatternItem[]
  recommended_actions: string[]
  technical_ratio: number | null
  non_technical_ratio: number | null
}

export interface RunResponse {
  run_id: string
  status: string
}

export interface ConnectionTestResult {
  reachable: boolean
  version: string | null
  scopes: string[]
  error?: string
}

export interface SettingsPayload {
  base_url: string
  api_key: string
  team: string
  ollama_url: string
}

export interface FetchRequest {
  products: string[]
  window_days: number
}

export interface AnalysisRequest {
  products: string[]
  window_days: number
}

export type SseEvent = {
  type: string
  [key: string]: unknown
}
