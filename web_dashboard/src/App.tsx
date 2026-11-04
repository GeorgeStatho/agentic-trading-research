import { useEffect, useState } from 'react'
import { getDashboardKpis } from './dashboardKpisApi.ts'
import {
  ROUTES,
  demoModeFromHash,
  demoToggleHash,
  locationFromHash,
  routeHash,
  tabHash,
} from './routes.tsx'
import type { Location } from './routes.tsx'
import { heartbeatMissing, loadScriptStatus, serverTimeAsUtc } from './shellStatus.ts'
import { StatusBar } from './statusBar.tsx'
import { Tag } from './ui/Tag.tsx'
import { usePolledResource } from './useRemoteResource.ts'

/** The shell: top bar, status line, the view strip a page asks for, the view. */
function App() {
  const [location, setLocation] = useState<Location>(() => locationFromHash(window.location.hash))
  const [demoMode, setDemoMode] = useState(() => demoModeFromHash(window.location.hash))
  const { route, tab } = location
  const kpis = usePolledResource(getDashboardKpis, { errorMessage: 'Failed to load system status' })
  const script = usePolledResource(loadScriptStatus, { errorMessage: 'Failed to load worker status', intervalMs: 15_000 })

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

  const marketLabel = kpis.payload?.market_status.label

  return (
    <div className="flex min-h-svh flex-col">
      <header className="border-b border-base-300 bg-head">
        {/* On phones the page links drop to their own row under the brand. */}
        <div className="mx-auto flex max-w-[1400px] flex-wrap items-center gap-x-6 px-5 sm:h-11 sm:flex-nowrap">
          <div className="flex shrink-0 items-center gap-2 text-[13px] font-semibold">
            <span aria-hidden="true" className="size-3.5 rounded-sm bg-primary" />
            Agentic Trading Research
          </div>
          <nav className="order-last flex h-9 basis-full items-stretch gap-4 sm:order-none sm:h-full sm:basis-auto" aria-label="Dashboard pages">
            {ROUTES.map((entry) => {
              const active = entry.slug === route.slug
              return (
                <a
                  key={entry.slug}
                  href={routeHash(entry, demoMode)}
                  className={`flex items-center border-b-2 ${active ? 'border-primary text-base-content' : 'border-transparent text-ink-2 hover:text-base-content'}`}
                  aria-current={active ? 'page' : undefined}
                >
                  {entry.label}
                </a>
              )
            })}
            <button type="button" className="btn btn-ghost btn-xs self-center" onClick={toggleDemoMode}>
              {demoMode ? 'Exit Demo' : 'Demo Mode'}
            </button>
          </nav>
          <div className="ml-auto flex h-11 shrink-0 items-center gap-3 text-[11px] text-ink-2 sm:h-auto">
            {marketLabel ? <span>Market {marketLabel.toLowerCase()}</span> : null}
            <Tag tone="accent">PAPER</Tag>
          </div>
        </div>
      </header>

      <StatusBar
        components={kpis.payload ? kpis.payload.bot_status.components ?? [] : null}
        lastRunAt={script.payload ? serverTimeAsUtc(script.payload.status.updated_at) : null}
        workerSilent={heartbeatMissing(script.payload?.status ?? null, Boolean(script.error), script.payload?.checkedAt ?? 0)}
      />

      {route.tabs.length > 1 ? (
        <div className="border-b border-base-300">
          <nav className="tabs tabs-border tabs-sm mx-auto max-w-[1400px] flex-nowrap overflow-x-auto px-3" aria-label={`${route.label} views`}>
            {route.tabs.map((entry) => {
              const active = entry.slug === tab.slug
              return (
                <a
                  key={entry.slug}
                  href={tabHash(route, entry, demoMode)}
                  className={`tab whitespace-nowrap ${active ? 'tab-active text-base-content before:text-primary' : 'text-ink-2'}`}
                  aria-current={active ? 'page' : undefined}
                >
                  {entry.label}
                </a>
              )
            })}
          </nav>
        </div>
      ) : null}

      <div className="mx-auto w-full max-w-[1400px] min-w-0 px-5 py-5">
        {tab.render(demoMode)}
      </div>
    </div>
  )
}

export default App
