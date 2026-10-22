import { useCallback, useEffect, useRef } from 'react'
import { enumLabel, labels } from '../constants/labels'
import type { EvidenceSelection } from '../types/assessment'

interface Props {
  selection: EvidenceSelection | null
  onClose: () => void
  onNavigate: (index: number) => void
}

const stanceTone: Record<string, string> = {
  supports: 'chip-accent',
  contradicts: 'chip-crimson',
  context: 'chip-violet',
}

export function EvidenceDrawer({ selection, onClose, onNavigate }: Props) {
  const closeRef = useRef<HTMLButtonElement>(null)
  const current = selection ? selection.items[selection.index] : null
  const total = selection?.items.length ?? 0

  const step = useCallback((delta: number) => {
    if (!selection) return
    const next = selection.index + delta
    if (next >= 0 && next < selection.items.length) onNavigate(next)
  }, [onNavigate, selection])

  useEffect(() => {
    if (!selection) return
    const previous = document.activeElement as HTMLElement | null
    closeRef.current?.focus()
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === 'Escape') onClose()
      if (event.key === 'ArrowLeft') step(-1)
      if (event.key === 'ArrowRight') step(1)
    }
    document.addEventListener('keydown', onKeyDown)
    return () => {
      document.removeEventListener('keydown', onKeyDown)
      previous?.focus()
    }
  }, [onClose, selection, step])

  return (
    <>
      <aside
        className={`drawer ${selection ? 'is-open' : ''}`}
        aria-labelledby="drawer-title"
        aria-hidden={!selection}
        role="dialog"
        aria-modal="true"
      >
        <div className="drawer-head">
          <h2 id="drawer-title">Evidence</h2>
          {current && (
            <span className="chip">{current.reference.evidence_id.replace(/^ev_/, '#')}</span>
          )}
          {total > 1 && (
            <div className="drawer-nav">
              <button
                className="icon-button"
                type="button"
                aria-label="Previous evidence"
                disabled={!selection || selection.index === 0}
                onClick={() => step(-1)}
              >
                ‹
              </button>
              <span>{(selection?.index ?? 0) + 1} / {total}</span>
              <button
                className="icon-button"
                type="button"
                aria-label="Next evidence"
                disabled={!selection || selection.index >= total - 1}
                onClick={() => step(1)}
              >
                ›
              </button>
            </div>
          )}
          <button
            ref={closeRef}
            className="icon-button"
            type="button"
            aria-label="Close evidence panel"
            style={total > 1 ? undefined : { marginLeft: 'auto' }}
            onClick={onClose}
          >
            ×
          </button>
        </div>

        {current && selection && (
          <div className="drawer-body">
            <div className="drawer-locators">
              {current.reference.page_number != null && (
                <span className="chip">Page {current.reference.page_number}</span>
              )}
              {current.reference.section_title && (
                <span className="chip">{current.reference.section_title}</span>
              )}
              {/* For body text `locator_label` is the internal chunk anchor, not
                  something a reader can look up — keep it out of the chips. */}
              {current.reference.locator_type !== 'text' && (
                <span className="chip chip-outline">
                  {current.reference.locator_label ?? enumLabel(labels.locator, current.reference.locator_type)}
                </span>
              )}
            </div>

            <div className="drawer-block">
              <h3>{current.reference.evidence_mode === 'quote' ? 'Quoted from the paper' : 'Paraphrased from the paper'}</h3>
              <p className="quote">{current.reference.excerpt}</p>
            </div>

            <div className="drawer-block">
              <h3>Why it was cited</h3>
              <div className="stance-line">
                <span className={`chip ${stanceTone[current.link.stance] ?? ''}`}>
                  {enumLabel(labels.stance, current.link.stance)}
                </span>
                <span className="chip chip-outline">{selection.context}</span>
              </div>
              <p>{current.link.reason}</p>
            </div>

            <div className="drawer-block">
              <h3>Location confidence</h3>
              <p>
                {enumLabel(labels.confidence, current.reference.location_confidence)}
                {current.reference.location_confidence !== 'high'
                  && ' — check the page number against the original PDF before quoting it.'}
              </p>
            </div>
          </div>
        )}
      </aside>
      {selection && <button className="backdrop" aria-label="Close evidence panel" type="button" onClick={onClose} />}
    </>
  )
}
