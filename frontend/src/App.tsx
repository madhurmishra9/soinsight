import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import { AppProvider } from './context/AppContext'
import { ErrorBoundary } from './components/ErrorBoundary'
import { Layout } from './components/Layout'
import { SettingsPage } from './pages/SettingsPage'
import { FetchPage } from './pages/FetchPage'
import { AnalysisPage } from './pages/AnalysisPage'
import { DashboardPage } from './pages/DashboardPage'

export default function App() {
  return (
    <AppProvider>
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
            </Route>
          </Routes>
        </ErrorBoundary>
      </BrowserRouter>
    </AppProvider>
  )
}
