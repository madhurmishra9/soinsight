import { createContext, useContext, useState } from 'react'
import type { ReactNode } from 'react'
import type {
  ConnectionTestResult,
  InsightsSummary,
  RemediationItem,
  ScheduleConfigPayload,
  ScheduleStatus,
  SettingsPayload,
  TrendItem,
  MetricsSummary,
  TagSuggestion,
} from '../types/api'

/**
 * PageStateContext lifts the Dashboard / Trends / Metrics / Tag-suggestions pages'
 * filter inputs and last-loaded results out of the page components, the same way
 * RunsContext lifts Fetch/Analysis/Remediation. Mounted above the router, so
 * switching tabs never resets a page you'd already loaded — only an explicit
 * action (changing a filter and reloading, or a full browser reload) changes it.
 *
 * Mirrored to sessionStorage so a reload restores it too. Kept deliberately
 * separate from RunsContext (which owns live SSE runs) since none of this needs
 * a persistent connection.
 */

export interface DashboardPageState {
  product: string
  windowDays: number
  fromDate: string
  toDate: string
  summary: InsightsSummary | null
  remediations: RemediationItem[]
}

export interface TrendsPageState {
  product: string
  opts: { recent_days: number; baseline_days: number; threshold: number; min_recent: number }
  items: TrendItem[]
  loaded: boolean
}

export interface MetricsPageState {
  selectedTags: string[]
  windowDays: number
  fromDate: string
  toDate: string
  data: MetricsSummary | null
}

export interface TagSuggestionsPageState {
  tracked: string
  minInstance: number
  limit: number
  items: TagSuggestion[]
  loaded: boolean
}

/**
 * The Settings page's in-progress draft — including whatever API key the user
 * has typed. Deliberately kept in React memory ONLY (never sessionStorage, never
 * disk): it survives switching tabs and coming back within the same session,
 * exactly like every other page here, but a full browser reload clears it, the
 * same security tradeoff the app already makes for the saved api_key.
 */
export interface SettingsDraftState {
  form: SettingsPayload | null
  saveMsg: { ok: boolean; text: string } | null
  testResult: ConnectionTestResult | null
  testError: string | null
}

/** No secrets involved, so unlike settingsDraft this is safe to mirror to sessionStorage. */
export interface SchedulePageState {
  form: ScheduleConfigPayload | null
  status: ScheduleStatus | null
  saveMsg: { ok: boolean; text: string } | null
  loaded: boolean
}

interface PageStateContextType {
  dashboard: DashboardPageState
  trends: TrendsPageState
  metrics: MetricsPageState
  tagSuggestions: TagSuggestionsPageState
  settingsDraft: SettingsDraftState
  schedule: SchedulePageState
  patchDashboard: (p: Partial<DashboardPageState>) => void
  patchTrends: (p: Partial<TrendsPageState>) => void
  patchMetrics: (p: Partial<MetricsPageState>) => void
  patchTagSuggestions: (p: Partial<TagSuggestionsPageState>) => void
  patchSettingsDraft: (p: Partial<SettingsDraftState>) => void
  patchSchedule: (p: Partial<SchedulePageState>) => void
}

const DASHBOARD_KEY = 'soinsight.page.dashboard'
const TRENDS_KEY = 'soinsight.page.trends'
const METRICS_KEY = 'soinsight.page.metrics'
const TAG_SUGGESTIONS_KEY = 'soinsight.page.tagSuggestions'
const SCHEDULE_KEY = 'soinsight.page.schedule'
// No sessionStorage key for settingsDraft — see SettingsDraftState doc comment.

const defaultDashboard: DashboardPageState = {
  product: '',
  windowDays: 30,
  fromDate: '',
  toDate: '',
  summary: null,
  remediations: [],
}

const defaultTrends: TrendsPageState = {
  product: '',
  opts: { recent_days: 7, baseline_days: 30, threshold: 2.0, min_recent: 2 },
  items: [],
  loaded: false,
}

const defaultMetrics: MetricsPageState = {
  selectedTags: [],
  windowDays: 30,
  fromDate: '',
  toDate: '',
  data: null,
}

const defaultTagSuggestions: TagSuggestionsPageState = {
  tracked: '',
  minInstance: 25,
  limit: 20,
  items: [],
  loaded: false,
}

const defaultSettingsDraft: SettingsDraftState = {
  form: null,
  saveMsg: null,
  testResult: null,
  testError: null,
}

const defaultSchedule: SchedulePageState = {
  form: null,
  status: null,
  saveMsg: null,
  loaded: false,
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

function persist<T>(key: string, value: T) {
  try {
    sessionStorage.setItem(key, JSON.stringify(value))
  } catch {
    // sessionStorage full/unavailable — state still survives in memory for this tab.
  }
}

const PageStateContext = createContext<PageStateContextType>({
  dashboard: defaultDashboard,
  trends: defaultTrends,
  metrics: defaultMetrics,
  tagSuggestions: defaultTagSuggestions,
  settingsDraft: defaultSettingsDraft,
  schedule: defaultSchedule,
  patchDashboard: () => undefined,
  patchTrends: () => undefined,
  patchMetrics: () => undefined,
  patchTagSuggestions: () => undefined,
  patchSettingsDraft: () => undefined,
  patchSchedule: () => undefined,
})

export function PageStateProvider({ children }: { children: ReactNode }) {
  const [dashboard, setDashboard] = useState<DashboardPageState>(() => restore(DASHBOARD_KEY, defaultDashboard))
  const [trends, setTrends] = useState<TrendsPageState>(() => restore(TRENDS_KEY, defaultTrends))
  const [metrics, setMetrics] = useState<MetricsPageState>(() => restore(METRICS_KEY, defaultMetrics))
  const [tagSuggestions, setTagSuggestions] = useState<TagSuggestionsPageState>(() => restore(TAG_SUGGESTIONS_KEY, defaultTagSuggestions))
  const [settingsDraft, setSettingsDraft] = useState<SettingsDraftState>(defaultSettingsDraft)
  const [schedule, setSchedule] = useState<SchedulePageState>(() => restore(SCHEDULE_KEY, defaultSchedule))

  const patchDashboard = (p: Partial<DashboardPageState>) =>
    setDashboard((prev) => { const next = { ...prev, ...p }; persist(DASHBOARD_KEY, next); return next })
  const patchTrends = (p: Partial<TrendsPageState>) =>
    setTrends((prev) => { const next = { ...prev, ...p }; persist(TRENDS_KEY, next); return next })
  const patchMetrics = (p: Partial<MetricsPageState>) =>
    setMetrics((prev) => { const next = { ...prev, ...p }; persist(METRICS_KEY, next); return next })
  const patchTagSuggestions = (p: Partial<TagSuggestionsPageState>) =>
    setTagSuggestions((prev) => { const next = { ...prev, ...p }; persist(TAG_SUGGESTIONS_KEY, next); return next })
  // Intentionally in-memory only — no sessionStorage persist() call (see SettingsDraftState doc comment).
  const patchSettingsDraft = (p: Partial<SettingsDraftState>) =>
    setSettingsDraft((prev) => ({ ...prev, ...p }))
  const patchSchedule = (p: Partial<SchedulePageState>) =>
    setSchedule((prev) => { const next = { ...prev, ...p }; persist(SCHEDULE_KEY, next); return next })

  return (
    <PageStateContext.Provider
      value={{
        dashboard, trends, metrics, tagSuggestions, settingsDraft, schedule,
        patchDashboard, patchTrends, patchMetrics, patchTagSuggestions, patchSettingsDraft, patchSchedule,
      }}
    >
      {children}
    </PageStateContext.Provider>
  )
}

export const usePageState = () => useContext(PageStateContext)
