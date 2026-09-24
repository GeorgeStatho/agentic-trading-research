import { useEffect, useState } from 'react'
import './App.css'
import { Icon } from './icon.tsx'
import {
  NO_SECTIONS,
  ROUTES,
  demoModeFromHash,
  demoToggleHash,
  locationFromHash,
  routeHash,
  tabHash,
} from './routes.tsx'
import type { Location } from './routes.tsx'
import { useActiveSection } from './useActiveSection.ts'

/** The shell: page nav, the view strip and sidebar a page asks for, the view. */
function App() {
  const [location, setLocation] = useState<Location>(() => locationFromHash(window.location.hash))
  const [demoMode, setDemoMode] = useState(() => demoModeFromHash(window.location.hash))
  const { route, tab } = location
  const activeSection = useActiveSection(tab.sections ?? NO_SECTIONS)

  useEffect(() => {
    const handleHashChange = () => {
      setLocation(locationFromHash(window.location.hash))
      setDemoMode(demoModeFromHash(window.location.hash))
    }

    window.addEventListener('hashchange', handleHashChange)
    return () => {
      window.removeEventListener('hashchange', handleHashChange)
    }
  }, [])

  const toggleDemoMode = () => {
    window.location.hash = demoToggleHash(location, demoMode)
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
                href={routeHash(entry, demoMode)}
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
        {tab.sections ? (
          <aside className="app-sidebar" aria-label="Page sections">
            <div className="app-sidebar__status">
              <span className="app-sidebar__status-dot" />
              <div><p className="app-sidebar__status-label">System Active</p><p className="app-sidebar__latency">Latency: 12ms</p></div>
            </div>
            <nav className="app-sidebar__nav">
              {tab.sections.map((section) => (
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

        <div className="app-page">
          {route.tabs.length > 1 ? (
            <nav className="page-tabs" aria-label={`${route.label} views`}>
              {route.tabs.map((entry) => (
                <a
                  key={entry.slug}
                  href={tabHash(route, entry, demoMode)}
                  className={`page-tabs__link${entry.slug === tab.slug ? ' page-tabs__link--active' : ''}`}
                  aria-current={entry.slug === tab.slug ? 'page' : undefined}
                >
                  {entry.label}
                </a>
              ))}
            </nav>
          ) : null}

          {tab.render(demoMode)}
        </div>
      </div>
    </div>
  )
}

export default App
