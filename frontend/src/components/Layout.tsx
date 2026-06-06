import { NavLink, Outlet } from 'react-router-dom'
import { BarChart2, Download, LayoutDashboard, Settings2 } from 'lucide-react'

export function Layout() {
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
      </nav>

      <main className="main">
        <div className="page">
          <Outlet />
        </div>
      </main>
    </div>
  )
}
