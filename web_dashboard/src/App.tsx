import { useEffect, useState } from 'react'
import './App.css'
import { Icon } from './icon.tsx'
import { NO_SECTIONS, ROUTES, demoModeFromHash, demoToggleHash, hashFor, routeFromHash } from './routes.tsx'
import type { Route } from './routes.tsx'
import { useActiveSection } from './useActiveSection.ts'

/** The shell: top nav, the sidebar a page asks for, and the page itself. */
function App() {
  const [route, setRoute] = useState<Route>(() => routeFromHash(window.location.hash))
  const [demoMode, setDemoMode] = useState(() => demoModeFromHash(window.location.hash))
  const activeSection = useActiveSection(route.sections ?? NO_SECTIONS)

  useEffect(() => {
    const handleHashChange = () => {
      setRoute(routeFromHash(window.location.hash))
      setDemoMode(demoModeFromHash(window.location.hash))
    }

    window.addEventListener('hashchange', handleHashChange)
    return () => {
      window.removeEventListener('hashchange', handleHashChange)
    }
  }, [])

  const toggleDemoMode = () => {
    window.location.hash = demoToggleHash(route, demoMode)
  }

  const scrollToSection = (id: string) => {
    document.getElementById(id)?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }

  return (
    <div className="app-shell">
      <header className="app-topbar">
        <div className="app-topbar__inner">
          <div className="app-brand"><h1>Agentic Trading Research</h1></div>
          <nav className="app-nav" aria-label="Dashboard pages">
            {ROUTES.map((entry) => (
              <a
                key={entry.slug}
                href={hashFor(entry.slug, demoMode)}
                className={`app-nav__link${entry.slug === route.slug ? ' app-nav__link--active' : ''}`}
                aria-current={entry.slug === route.slug ? 'page' : undefined}
              >
                {entry.label}
              </a>
            ))}
            <button type="button" className={`app-nav__demo${demoMode ? ' app-nav__demo--active' : ''}`} onClick={toggleDemoMode}>{demoMode ? 'Exit Demo' : 'Demo Mode'}</button>
          </nav>
          <div className="app-topbar__actions" aria-label="Dashboard utilities">
            <button type="button" className="app-topbar__icon" aria-label="Notifications"><Icon name="bell" /></button>
            <button type="button" className="app-topbar__icon" aria-label="Settings"><Icon name="settings" /></button>
            <button type="button" className="app-topbar__icon" aria-label="Account"><Icon name="user" /></button>
          </div>
        </div>
      </header>

      <div className="app-layout">
        {route.sections ? (
          <aside className="app-sidebar" aria-label="Page sections">
            <div className="app-sidebar__status">
              <span className="app-sidebar__status-dot" />
              <div><p className="app-sidebar__status-label">System Active</p><p className="app-sidebar__latency">Latency: 12ms</p></div>
            </div>
            <nav className="app-sidebar__nav">
              {route.sections.map((section) => (
                <button
                  key={section.id}
                  type="button"
                  className={`app-sidebar__item${activeSection === section.id ? ' app-sidebar__item--active' : ''}`}
                  onClick={() => scrollToSection(section.id)}
                  aria-current={activeSection === section.id ? 'location' : undefined}
                >
                  <span className="app-sidebar__icon"><Icon name={section.icon} /></span>{section.label}
                </button>
              ))}
            </nav>
          </aside>
        ) : null}

        {route.render(demoMode)}
      </div>
    </div>
  )
}

export default App
