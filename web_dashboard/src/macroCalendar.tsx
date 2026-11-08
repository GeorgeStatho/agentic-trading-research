import { formatShortDateTime } from './format'
import { getMacroCalendar } from './reportsApi'
import type { MacroEvent } from './reportsApi'
import { Panel } from './ui/Panel'
import { usePolledResource } from './useRemoteResource'

const SHOWN = 5
const NOTABLE = new Set(['high', 'medium'])
const HOUR_MS = 60 * 60 * 1000

/** The market regime gate pauses entries around these; they move US options most. */
function isHighImpactUs(event: MacroEvent): boolean {
  return event.importance === 'high' && event.currency === 'USD'
}

/** The next few medium- and high-importance macro releases. */
export function MacroCalendarStrip() {
  const { payload, error } = usePolledResource(getMacroCalendar, { errorMessage: 'Failed to load the macro calendar.', intervalMs: HOUR_MS })
  const events = (payload?.events ?? []).filter((event) => NOTABLE.has(event.importance ?? '')).slice(0, SHOWN)

  return (
    <Panel title="Coming up" meta="macro releases, medium and high importance">
      {error ? <p className="text-error">{error}</p> : null}
      {payload && events.length === 0 ? (
        <p className="text-dim">Nothing scheduled through {formatShortDateTime(payload.calendar_through, { placeholder: 'the end of the stored calendar' })}.</p>
      ) : null}
      {events.length ? (
        <ul aria-label="Coming up" className="flex flex-wrap gap-x-6 gap-y-1">
          {events.map((event) => (
            <li key={`${event.event_name}-${event.scheduled_at_utc}`} className={isHighImpactUs(event) ? 'font-semibold text-warning' : 'text-ink-2'}>
              <span className="text-dim">{formatShortDateTime(event.scheduled_at_utc)}</span>{' '}
              <span>{event.event_name}</span>{' '}
              <span className="text-[11px] text-dim">{event.currency}</span>
            </li>
          ))}
        </ul>
      ) : null}
    </Panel>
  )
}
