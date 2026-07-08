export interface EvidenceQuestion {
  so_id: number
  title: string
  url: string | null
}

export interface EvidenceAnswer {
  so_id: number
  question_so_id: number
  snippet: string
  is_accepted: boolean
  score: number
}

export interface RemediationItem {
  main_category: string
  sub_category: string
  question_count: number
  distinct_users: number
  root_cause: string
  solution: string
  prevention: string
  confidence: number
  grounded: boolean
  model: string
  generated_at: string
  evidence_questions: EvidenceQuestion[]
  evidence_answers: EvidenceAnswer[]
}

export interface TagValidation {
  tag: string
  status: 'available' | 'unavailable' | 'unknown'
  question_count: number | null
}

export interface AvailableTag {
  tag: string
  question_count: number
}

export interface AvailableTagsResponse {
  ok: boolean
  tags: AvailableTag[]
  total: number
}

export interface TagCoverage {
  tag: string
  question_count: number
  answer_count: number
  earliest_question_at: string | null
  latest_question_at: string | null
  last_fetch_at: string | null
}

export interface AnswerRef {
  so_id: number
  body: string
  score: number
  is_accepted: boolean
  created_at: string
}

export interface QuestionRef {
  so_id: number
  title: string
  score: number
  view_count: number
  created_at: string
  url: string | null
  answer_count?: number
  answers?: AnswerRef[]
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

// ── F4 Run history ────────────────────────────────────────────────────────────

export interface RunItem {
  id: number
  started_at: string
  finished_at: string | null
  status: string
  products: string[]
  window_days: number
  duration_seconds: number | null
  counts: Record<string, number>
}

// ── F1 Pattern dismissals ─────────────────────────────────────────────────────

export interface DismissedItem {
  id: number
  product: string
  main: string
  sub: string
  dismissed_until: string | null
  reason: string | null
  created_at: string
}

export interface DismissRequest {
  product: string
  main: string
  sub: string
  days?: number
  until?: string
  reason?: string
}

// ── F2 Rising-volume trends ───────────────────────────────────────────────────

export interface TrendItem {
  main_category: string
  sub_category: string
  recent_count: number
  trailing_avg_per_window: number
  multiplier: number
  is_rising: boolean
}

// ── F3 Tag suggestions ────────────────────────────────────────────────────────

export interface TagSuggestion {
  tag: string
  instance_count: number
  local_count: number
  coverage_ratio: number
}

// ── Metrics (pipeline health) ─────────────────────────────────────────────────

export interface UnclassifiedReason {
  reason: string
  count: number
}

export interface TagMetrics {
  tag: string
  total_questions: number
  answered: number
  unanswered: number
  classified: number
  unclassified: number
  accepted: number
  acceptance_rate: number | null
  mean_time_to_answer_hours: number | null
}

export interface MetricsSummary {
  window_days: number
  from_date: string | null
  to_date: string | null
  tags: string[]
  total_questions: number
  answered: number
  unanswered: number
  classified: number
  unclassified: number
  unclassified_reasons: UnclassifiedReason[]
  accepted: number
  not_accepted: number
  acceptance_rate: number | null
  avg_answers_per_question: number | null
  avg_views_per_question: number | null
  distinct_askers: number
  mean_time_to_answer_hours: number | null
  median_time_to_answer_hours: number | null
  by_tag: TagMetrics[]
}

export type MetricBucket =
  | 'total'
  | 'answered'
  | 'unanswered'
  | 'classified'
  | 'unclassified'
  | 'accepted'
  | 'not_accepted'
  | 'answered_with_time'

export interface MetricQuestionRef {
  so_id: number
  title: string
  url: string | null
  score: number
  view_count: number
  answer_count: number
  has_accepted: boolean
  created_at: string
  time_to_first_answer_hours: number | null
}
