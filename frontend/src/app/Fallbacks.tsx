import { Link, type ErrorComponentProps } from '@tanstack/react-router'
import { Button } from '../ui/Button'
import { Icon } from '../ui/Icon'

/** An address that matches no page (spec 4.4). */
export function NotFound() {
  return <main className="mx-auto grid w-full max-w-[640px] gap-3 px-6 py-20 text-center">
    <p className="cs-eyebrow">Page not found</p>
    <h1 className="cs-serif text-title-l font-semibold">There is no page at this address</h1>
    <p className="cs-lead">The link may be mistyped, or it may point to a page that no longer exists. Assessments keep their own addresses under /check/ and /score/.</p>
    <div className="flex flex-wrap justify-center gap-2 pt-1">
      <Link to="/" className="cs-btn is-primary"><Icon name="add" />New assessment</Link>
      <Link to="/reports" className="cs-btn"><Icon name="history" />Assessment history</Link>
    </div>
  </main>
}

/** A page that failed while rendering. The report data on the server is unaffected (spec 4.4). */
export function PageError({ error, reset }: ErrorComponentProps) {
  return <main className="mx-auto grid w-full max-w-[640px] gap-3 px-6 py-20 text-center">
    <p className="cs-eyebrow text-concern">Display error</p>
    <h1 className="cs-serif text-title-l font-semibold">This page could not be displayed</h1>
    <p className="cs-lead">The assessment itself is unaffected; reloading usually resolves this. If it persists, the details below help diagnose it.</p>
    {error instanceof Error && error.message && <p className="mx-auto max-w-full rounded-sm bg-[#F2F5F8] px-3 py-2 font-mono text-caption text-muted">{error.message}</p>}
    <div className="flex flex-wrap justify-center gap-2 pt-1">
      <Button variant="primary" icon="arrow_clockwise" onClick={() => { reset(); window.location.reload() }}>Reload page</Button>
      <Link to="/" className="cs-btn"><Icon name="add" />New assessment</Link>
    </div>
  </main>
}
