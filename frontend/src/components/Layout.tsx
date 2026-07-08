import { NavLink, Outlet } from 'react-router-dom'
import {
  BarChart2,
  BellOff,
  BookOpen,
  Download,
  Gauge,
  History,
  LayoutDashboard,
  Lightbulb,
  Moon,
  Settings2,
  Sun,
  TrendingUp,
} from 'lucide-react'
import { useTheme } from '../hooks/useTheme'

export function Layout() {
  const { theme, toggle } = useTheme()

  return (
    <div className="layout">
      <nav className="sidebar">
        <div className="sidebar-logo">
          <BarChart2 size={20} />
          SO<span>Insight</span>
        </div>

        <div className="sidebar-section">Workflow</div>
        <NavLink to="/settings" className={({ isActive }) => `nav-link${isActive ? ' active' : ''}`}>
          <Settings2 size={16} /> Settings
        </NavLink>
        <NavLink to="/fetch" className={({ isActive }) => `nav-link${isActive ? ' active' : ''}`}>
          <Download size={16} /> Fetch Questions
        </NavLink>
        <NavLink to="/analysis" className={({ isActive }) => `nav-link${isActive ? ' active' : ''}`}>
          <BarChart2 size={16} /> Analysis
        </NavLink>

        <div className="sidebar-section" style={{ marginTop: 8 }}>Insights</div>
        <NavLink to="/dashboard" className={({ isActive }) => `nav-link${isActive ? ' active' : ''}`}>
          <LayoutDashboard size={16} /> Dashboard
        </NavLink>
        <NavLink to="/trends" className={({ isActive }) => `nav-link${isActive ? ' active' : ''}`}>
          <TrendingUp size={16} /> Rising trends
        </NavLink>
        <NavLink to="/metrics" className={({ isActive }) => `nav-link${isActive ? ' active' : ''}`}>
          <Gauge size={16} /> Metrics
        </NavLink>
        <NavLink to="/tag-suggestions" className={({ isActive }) => `nav-link${isActive ? ' active' : ''}`}>
          <Lightbulb size={16} /> Tag suggestions
        </NavLink>
        <NavLink to="/snoozed" className={({ isActive }) => `nav-link${isActive ? ' active' : ''}`}>
          <BellOff size={16} /> Snoozed
        </NavLink>

        <div className="sidebar-section" style={{ marginTop: 8 }}>Operations</div>
        <NavLink to="/runs" className={({ isActive }) => `nav-link${isActive ? ' active' : ''}`}>
          <History size={16} /> Run history
        </NavLink>
        <NavLink to="/help" className={({ isActive }) => `nav-link${isActive ? ' active' : ''}`}>
          <BookOpen size={16} /> User guide
        </NavLink>

        <button className="theme-toggle" onClick={toggle} title="Toggle theme">
          {theme === 'dark' ? <Sun size={16} /> : <Moon size={16} />}
          {theme === 'dark' ? 'Light mode' : 'Dark mode'}
        </button>
      </nav>

      <main className="main">
        <div className="page">
          <Outlet />
        </div>
      </main>
    </div>
  )
}
