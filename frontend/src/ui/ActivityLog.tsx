import { Icon } from './Icon'

export type LogState = 'done' | 'running' | 'partial' | 'failed'
export interface LogEvent { id: string; text: string; state: LogState }

const stateNames: Record<LogState, string> = { done: 'Done', running: 'In progress', partial: 'Finished with gaps', failed: 'Failed' }

/**
 * Scrolling log (spec 3.9): a fixed window of the latest major events. New lines enter at the bottom,
 * older ones scroll up and fade out, so the page never grows.
 */
export function ActivityLog({ events, lines = 4 }: { events: LogEvent[]; lines?: number }) {
  const shown = events.slice(-lines)
  return <ol className="cs-log" style={{ '--lines': lines } as React.CSSProperties} aria-label="What the check is doing" aria-live="polite">
    {shown.map(event => <li key={event.id} className={`cs-log-row is-${event.state}`}>
      <span className={`cs-log-ic is-${event.state}`} role="img" aria-label={stateNames[event.state]}>
        {event.state === 'done' && <Icon name="checkmark" />}{event.state === 'failed' && <b className="text-[11px]">!</b>}
      </span>
      <span className="cs-log-t">{event.text}</span>
    </li>)}
  </ol>
}
