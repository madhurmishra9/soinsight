export interface QuestionRef {
  so_id: number
  title: string
  score: number
  view_count: number
  created_at: string
  url: string | null
}

export interface CategoryBreakdownItem {
  main_category: string
  sub_category: string
  question_count: number
  distinct_users: number
  questions?: QuestionRef[]
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
  questions?: QuestionRef[]
}

export interface InsightsSummary {
  product: string
  window_days: number
  total_questions: number
  noise_count: number
  noise_questions?: QuestionRef[]
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
  ollama_model?: string
  default_tags?: string
}

export interface FetchRequest {
  products: string[]
  window_days: number
  from_date?: string
  to_date?: string
  incremental?: boolean
}

export interface AnalysisRequest {
  products: string[]
  window_days: number
  from_date?: string
  to_date?: string
}

export type SseEvent = {
  type: string
  [key: string]: unknown
}
