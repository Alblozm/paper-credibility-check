import { useEffect, useId, useRef } from 'react'
import { createPortal } from 'react-dom'
import type { CredibilityEvidence, CredibilityExternalSource } from '../types/credibility'
import './CredibilityEvidenceDrawer.css'

interface Props {
  evidence: CredibilityEvidence[]
  sources: CredibilityExternalSource[]
  selectedId: string | null
  paperPdfUrl: string | null
  onSelect: (id: string) => void
  onClose: () => void
}

const fetchLabels: Record<CredibilityExternalSource['fetch_status'], string> = {
  ok: 'Retrieved',
  not_found: 'Not found at this URL',
  access_denied: 'Access denied',
  timeout: 'Request timed out',
  rate_limited: 'Rate limited',
  error: 'Could not retrieve',
}

function sourceLink(value: string) {
  try {
    const url = new URL(value)
    if (!['https:', 'http:'].includes(url.protocol) || url.username || url.password) return null
    if (url.hostname === 'invalid' || url.hostname.endsWith('.invalid')) return null
    return url.href
  } catch {
    return null
  }
}

function isSyntheticSource(value: string) {
  try {
    const host = new URL(value).hostname
    return host === 'invalid' || host.endsWith('.invalid')
  } catch {
    return false
  }
}

function paperLink(value: string | null, page: number | undefined) {
  if (!value || !Number.isInteger(page) || page! < 1) return null
  const safe = sourceLink(value)
  if (!safe) return null
  const url = new URL(safe)
  if (url.protocol !== 'https:' || !['arxiv.org', 'www.arxiv.org'].includes(url.hostname)
    || !/^\/pdf\/[^/]+(?:\/[^/]+)?$/.test(url.pathname)) return null
  url.hash = `page=${page}`
  return url.href
}

function formatTimestamp(value: string) {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return `${date.toLocaleString('en-AU', {
    timeZone: 'UTC', day: 'numeric', month: 'short', year: 'numeric',
    hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false,
  })} UTC`
}

export function CredibilityEvidenceDrawer({
  evidence, sources, selectedId, paperPdfUrl, onSelect, onClose,
}: Props) {
  const index = evidence.findIndex((item) => item.evidence_id === selectedId)
  const current = index >= 0 ? evidence[index] : null
  const isOpen = current !== null
  const titleId = useId()
  const panelRef = useRef<HTMLElement>(null)
  const closeRef = useRef<HTMLButtonElement>(null)
  const onCloseRef = useRef(onClose)

  useEffect(() => {
    onCloseRef.current = onClose
  }, [onClose])

  useEffect(() => {
    if (!isOpen) return
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null
    const previousOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    closeRef.current?.focus()

    function keepFocusInside(event: FocusEvent) {
      if (event.target instanceof Node && !panelRef.current?.contains(event.target)) {
        closeRef.current?.focus()
      }
    }

    function onKeyDown(event: KeyboardEvent) {
      if (event.key === 'Escape') {
        event.preventDefault()
        event.stopPropagation()
        onCloseRef.current()
        return
      }
      if (event.key !== 'Tab') return
      const controls = Array.from(panelRef.current?.querySelectorAll<HTMLElement>(
        'a[href], button:not([disabled]), [tabindex]:not([tabindex="-1"])',
      ) ?? [])
      const first = controls[0]
      const last = controls[controls.length - 1]
      if (!first) {
        event.preventDefault()
        panelRef.current?.focus()
      } else if (event.shiftKey && (document.activeElement === first || document.activeElement === panelRef.current)) {
        event.preventDefault()
        last.focus()
      } else if (!event.shiftKey && (document.activeElement === last || document.activeElement === panelRef.current)) {
        event.preventDefault()
        first.focus()
      }
    }

    document.addEventListener('keydown', onKeyDown, true)
    document.addEventListener('focusin', keepFocusInside)
    return () => {
      document.removeEventListener('keydown', onKeyDown, true)
      document.removeEventListener('focusin', keepFocusInside)
      document.body.style.overflow = previousOverflow
      if (previous?.isConnected) previous.focus()
    }
  }, [isOpen])

  if (!current) return null
  const locator = current.paper_locator
  const source = sources.find((item) => item.source_id === current.source_id)
  const originalPdf = current.origin === 'paper' ? paperLink(paperPdfUrl, locator?.page_start) : null
  const externalUrl = source ? sourceLink(source.url) : null
  const synthetic = source ? isSyntheticSource(source.url) : false
  const pageLabel = locator
    ? locator.page_start === locator.page_end ? `Page ${locator.page_start}` : `Pages ${locator.page_start}–${locator.page_end}`
    : null

  return createPortal(
    <div className="cred-evidence-layer">
      <button className="cred-evidence-backdrop" type="button" tabIndex={-1}
        aria-label="Dismiss evidence panel" onClick={onClose} />
      <aside ref={panelRef} className="cred-evidence-panel" role="dialog" aria-modal="true"
        aria-labelledby={titleId} tabIndex={-1}>
        <header className="cred-evidence-header">
          <div>
            <p className="cred-evidence-eyebrow">Evidence · {index + 1} of {evidence.length}</p>
            <h2 id={titleId}>{current.origin === 'paper' ? 'Paper passage' : 'External source'}</h2>
          </div>
          <button ref={closeRef} className="cred-evidence-close" type="button"
            aria-label="Close evidence panel" onClick={onClose}><span aria-hidden="true">×</span></button>
        </header>

        <div className="cred-evidence-body">
          {current.origin === 'paper' && (
            <section className="cred-evidence-location" aria-label="Paper location">
              <p className="cred-evidence-page">{pageLabel ?? 'Page location unavailable'}</p>
              {locator?.section_title && <p>{locator.section_title}</p>}
              {locator?.element_label && <p className="cred-evidence-element">{locator.element_label}</p>}
            </section>
          )}

          <section className="cred-evidence-excerpt">
            <h3>{current.mode === 'quote' ? 'Source excerpt' : 'Source summary'}</h3>
            <blockquote>{current.excerpt}</blockquote>
          </section>

          <section className="cred-evidence-section">
            <h3>Location confidence</h3>
            <p className="cred-evidence-confidence">{current.location_confidence}</p>
            {current.location_confidence !== 'high' && (
              <p className="cred-evidence-note">Verify this location against the source before quoting.</p>
            )}
          </section>

          {current.origin === 'paper' && (
            <section className="cred-evidence-section">
              <h3>Original paper</h3>
              <p className="cred-evidence-note">Locations use the PDF’s physical page order, starting at page 1.</p>
              {originalPdf ? (
                <a className="cred-evidence-source-link" href={originalPdf} target="_blank" rel="noopener noreferrer">
                  Open PDF at page {locator?.page_start} <span aria-hidden="true">↗</span>
                </a>
              ) : <p className="cred-evidence-note">An original PDF link is not available for this evidence.</p>}
            </section>
          )}

          {current.origin === 'external' && (
            <section className="cred-evidence-section">
              <h3>Source record</h3>
              {source ? <>
                <p className={`cred-evidence-fetch ${source.fetch_status === 'ok' ? '' : 'is-incomplete'}`}>
                  {fetchLabels[source.fetch_status]}{synthetic && ' · Synthetic source'}
                </p>
                <dl className="cred-evidence-source-meta">
                  <div><dt>Source type</dt><dd>{source.kind.replaceAll('_', ' ')}</dd></div>
                  <div><dt>Observed</dt><dd><time dateTime={source.observed_at}>{formatTimestamp(source.observed_at)}</time></dd></div>
                  <div><dt>Read for this report</dt><dd><time dateTime={source.retrieved_at}>{formatTimestamp(source.retrieved_at)}</time></dd></div>
                  <div><dt>Cache used</dt><dd>{source.is_cached ? 'Yes — observation time remains unchanged' : 'No'}</dd></div>
                  {source.http_status !== null && <div><dt>HTTP status</dt><dd>{source.http_status}</dd></div>}
                </dl>
                <h3 className="cred-evidence-association-heading">Why this source was checked</h3>
                <p>{source.association_reason}</p>
                {source.fetch_status !== 'ok' && (
                  <p className="cred-evidence-note">This source was not retrieved successfully. The excerpt needs review.</p>
                )}
                {externalUrl ? (
                  <a className="cred-evidence-source-link" href={externalUrl} target="_blank" rel="noopener noreferrer">
                    Open source <span aria-hidden="true">↗</span>
                  </a>
                ) : <p className="cred-evidence-note">
                  {synthetic ? 'This fixture uses an example address. No live page is available.' : 'A safe source link is not available.'}
                </p>}
              </> : <p className="cred-evidence-note">The source record for this evidence is unavailable.</p>}
            </section>
          )}
        </div>

        <footer className="cred-evidence-navigation" aria-label="Evidence navigation">
          <button type="button" disabled={index === 0} onClick={() => onSelect(evidence[index - 1].evidence_id)}>
            <span aria-hidden="true">←</span> Previous evidence
          </button>
          <button type="button" disabled={index === evidence.length - 1} onClick={() => onSelect(evidence[index + 1].evidence_id)}>
            Next evidence <span aria-hidden="true">→</span>
          </button>
        </footer>
      </aside>
    </div>, document.body,
  )
}
