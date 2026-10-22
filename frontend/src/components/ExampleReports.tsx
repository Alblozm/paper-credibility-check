import { useEffect, useRef, useState } from 'react'
import { categoryNames } from '../constants/credibility'
import type { CredibilityApi, CredibilityAssessment, CredibilityHistoryItem } from '../types/credibility'

interface Props {
  api: CredibilityApi
  onOpen: (assessment: CredibilityAssessment) => void
  /** Hide the check that is currently running or open. */
  excludeId?: string
  limit?: number
  title?: string
  note?: string
}

/** Finished checks already stored on this backend, offered as reading material. */
export function ExampleReports({ api, onOpen, excludeId, limit = 6, title = 'Example reports', note }: Props) {
  const [items, setItems] = useState<CredibilityHistoryItem[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [opening, setOpening] = useState<string | null>(null)
  const opener = useRef<AbortController | null>(null)
  useEffect(() => () => opener.current?.abort(), [])

  useEffect(() => {
    if (!api.listAssessments) return
    let active = true
    api.listAssessments('', 0).then(page => {
      if (active) setItems(page.items.filter(item => item.status === 'completed'))
    }).catch(cause => { if (active) setError(cause instanceof Error ? cause.message : 'Example reports could not be loaded.') })
    return () => { active = false }
  }, [api])

  if (!api.listAssessments) return null
  const shown = (items ?? []).filter(item => item.assessment_id !== excludeId).slice(0, limit)

  async function open(id: string) {
    opener.current?.abort()
    const controller = new AbortController()
    opener.current = controller
    setOpening(id); setError(null)
    try {
      const assessment = await api.getAssessment(id, controller.signal)
      if (!controller.signal.aborted) onOpen(assessment)
    } catch (cause) {
      if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : 'This report could not be opened.')
    } finally {
      if (opener.current === controller) { opener.current = null; setOpening(null) }
    }
  }

  return <section className="cred-examples" aria-labelledby="cred-examples-title">
    <div className="cred-examples-head">
      <h2 id="cred-examples-title">{title}</h2>
      <p>{note ?? 'Checks already completed on this server. Opening one does not run anything.'}</p>
    </div>
    {error && <p className="field-error" role="alert">{error}</p>}
    {items === null && !error && <p className="cred-muted" role="status">Loading example reports…</p>}
    {items !== null && shown.length === 0 && <p className="cred-empty">No finished checks on this server yet. Completed checks appear here.</p>}
    {shown.length > 0 && <ul className="cred-example-list">
      {shown.map(item => <li key={item.assessment_id}>
        <button type="button" className="cred-example" disabled={opening === item.assessment_id} onClick={() => void open(item.assessment_id)}>
          <span className="cred-example-score">{item.overall_score === null ? '—' : item.overall_score.toFixed(1)}</span>
          <span className="cred-example-copy">
            <strong>{item.title || item.arxiv_id || 'Untitled paper'}</strong>
            <small>{[item.arxiv_id, item.category ? categoryNames[item.category] ?? item.category : 'No overall category'].filter(Boolean).join(' · ')}</small>
          </span>
          <span className="cred-example-open" aria-hidden="true">{opening === item.assessment_id ? '…' : '→'}</span>
        </button>
      </li>)}
    </ul>}
  </section>
}
