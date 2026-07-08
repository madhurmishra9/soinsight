import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import { AppProvider } from './context/AppContext'
import { RunsProvider } from './context/RunsContext'
import { ErrorBoundary } from './components/ErrorBoundary'
import { Layout } from './components/Layout'
import { SettingsPage } from './pages/SettingsPage'
import { FetchPage } from './pages/FetchPage'
import { AnalysisPage } from './pages/AnalysisPage'
import { DashboardPage } from './pages/DashboardPage'
import { RunsPage } from './pages/RunsPage'
import { TrendsPage } from './pages/TrendsPage'
import { DismissedPatternsPage } from './pages/DismissedPatternsPage'
import { TagSuggestionsPage } from './pages/TagSuggestionsPage'
import { MetricsPage } from './pages/MetricsPage'
import { HelpPage } from './pages/HelpPage'

export default function App() {
  return (
    <AppProvider>
      <RunsProvider>
        <BrowserRouter>
          <ErrorBoundary>
            <Routes>
              <Route element={<Layout />}>
                <Route index element={<Navigate to="/settings" replace />} />
                <Route
                  path="/settings"
                  element={<ErrorBoundary><SettingsPage /></ErrorBoundary>}
                />
                <Route
                  path="/fetch"
                  element={<ErrorBoundary><FetchPage /></ErrorBoundary>}
                />
                <Route
                  path="/analysis"
                  element={<ErrorBoundary><AnalysisPage /></ErrorBoundary>}
                />
                <Route
                  path="/dashboard"
                  element={<ErrorBoundary><DashboardPage /></ErrorBoundary>}
                />
                <Route
                  path="/trends"
                  element={<ErrorBoundary><TrendsPage /></ErrorBoundary>}
                />
                <Route
                  path="/metrics"
                  element={<ErrorBoundary><MetricsPage /></ErrorBoundary>}
                />
                <Route
                  path="/tag-suggestions"
                  element={<ErrorBoundary><TagSuggestionsPage /></ErrorBoundary>}
                />
                <Route
                  path="/snoozed"
                  element={<ErrorBoundary><DismissedPatternsPage /></ErrorBoundary>}
                />
                <Route
                  path="/runs"
                  element={<ErrorBoundary><RunsPage /></ErrorBoundary>}
                />
                <Route
                  path="/help"
                  element={<ErrorBoundary><HelpPage /></ErrorBoundary>}
                />
              </Route>
            </Routes>
          </ErrorBoundary>
        </BrowserRouter>
      </RunsProvider>
    </AppProvider>
  )
}
