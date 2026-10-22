import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { enumLabel, formatScore, labels, scoreBand, type ScoreBand } from '../constants/labels'
import type {
  Assessment,
  EvidenceLink,
  EvidenceReference,
  EvidenceSelection,
  Finding,
  SelectedEvidence,
} from '../types/assessment'
import { EvidenceDrawer } from './EvidenceDrawer'

interface Props {
  assessment: Assessment
  onNewAssessment: () => void
}

type OpenEvidence = (links: EvidenceLink[], context: string, startAt?: number) => void

const bandColor: Record<ScoreBand, string> = {
  strong: 'var(--score-strong)',
  good: 'var(--score-good)',
  adequate: 'var(--score-adequate)',
  weak: 'var(--score-weak)',
  none: 'var(--score-none)',
}

function bandStyle(score: number | null | undefined) {
  return { '--band': bandColor[scoreBand(score)] } as React.CSSProperties
}

const checkMarks: Record<string, string> = {
  available: '✓',
  not_available: '✕',
  unable_to_verify: '?',
  not_applicable: '–',
}

const sections = [
  { id: 'summary', label: 'Summary' },
  { id: 'dimensions', label: 'Dimensions' },
  { id: 'findings', label: 'Strengths & concerns' },
  { id: 'claims', label: 'Claim checks' },
  { id: 'contributions', label: 'Contributions' },
  { id: 'reproducibility', label: 'Reproducibility' },
  { id: 'questions', label: 'Open questions' },
  { id: 'limitations', label: 'Limitations' },
]

/**
 * "p. 7 · Table 2" beats "Evidence 04": the reader wants to know where to look.
 *
 * For body text the backend puts a chunk id in `locator_label` (the anchor the
 * model had to cite, e.g. arxiv_2307.08122v3_chunk_0000). That is internal
 * plumbing, so show the section instead; named elements keep their real label.
 */
function locationLabel(reference: EvidenceReference) {
  const parts: string[] = []
  if (reference.page_number != null) parts.push(`p. ${reference.page_number}`)
  if (reference.locator_type === 'text') {
    if (reference.section_title) parts.push(reference.section_title)
  } else if (reference.locator_label) {
    parts.push(reference.locator_label)
  }
  if (!parts.length && reference.section_title) parts.push(reference.section_title)
  if (!parts.length) parts.push(reference.evidence_id.replace(/^ev_/, 'Evidence '))
  return parts.join(' · ')
}

function EvidenceRow({ links, context, evidence, onOpen }: {
  links: EvidenceLink[]
  context: string
  evidence: Map<string, EvidenceReference>
  onOpen: OpenEvidence
}) {
  // Links whose reference the backend dropped are skipped here and in
  // `openEvidence`, so the index stays aligned with the drawer's list.
  const resolved = links.filter((link) => evidence.has(link.evidence_id))
  if (!resolved.length) return null
  return (
    <div className="evidence-row">
      {resolved.map((link, index) => {
        const reference = evidence.get(link.evidence_id)!
        const stance = enumLabel(labels.stance, link.stance)
        return (
          <button
            className={`ev-chip ${link.stance}`}
            type="button"
            key={`${link.evidence_id}-${index}`}
            aria-label={`${stance}: ${locationLabel(reference)} — open evidence`}
            onClick={() => onOpen(links, context, index)}
          >
            <i aria-hidden="true" />
            {stance} · {locationLabel(reference)}
          </button>
        )
      })}
    </div>
  )
}

function FindingColumn({ title, items, tone, evidence, onOpen }: {
  title: string
  items: Finding[]
  tone: 'strengths' | 'concerns'
  evidence: Map<string, EvidenceReference>
  onOpen: OpenEvidence
}) {
  return (
    <div className={`finding-col ${tone}`}>
      <h3>{title}<span>{items.length}</span></h3>
      {items.length ? items.map((item) => (
        <article className="finding" key={item.finding_id}>
          <div className="finding-head">
            <h4>{item.title}</h4>
            {item.importance === 'major' && <span className="chip">Major</span>}
          </div>
          <p>{item.detail}</p>
          <EvidenceRow links={item.evidence_links} context={item.title} evidence={evidence} onOpen={onOpen} />
        </article>
      )) : <p className="empty">Nothing reliable was returned for this section.</p>}
    </div>
  )
}

export function AssessmentReport({ assessment, onNewAssessment }: Props) {
  const [selection, setSelection] = useState<EvidenceSelection | null>(null)
  const [activeSection, setActiveSection] = useState('summary')
  const [isCondensed, setIsCondensed] = useState(false)
  const sentinelRef = useRef<HTMLDivElement>(null)

  const report = assessment.report
  const paper = assessment.paper

  const closeEvidence = useCallback(() => setSelection(null), [])
  const navigateEvidence = useCallback((index: number) => {
    setSelection((current) => (current ? { ...current, index } : current))
  }, [])

  const openEvidence = useCallback<OpenEvidence>((links, context, startAt = 0) => {
    if (!report) return
    const items = links.reduce<SelectedEvidence[]>((accumulator, link) => {
      const reference = report.evidence.find((item) => item.evidence_id === link.evidence_id)
      if (reference) accumulator.push({ reference, link })
      return accumulator
    }, [])
    if (!items.length) return
    setSelection({ items, index: Math.min(startAt, items.length - 1), context })
  }, [report])

  // Condense the header once the score panel has scrolled past.
  useEffect(() => {
    const sentinel = sentinelRef.current
    if (!sentinel) return
    const observer = new IntersectionObserver(
      // `isIntersecting` is false both above and below the viewport, and on a
      // phone the sentinel starts far below it — so also require that it has
      // actually scrolled off the top.
      ([entry]) => setIsCondensed(!entry.isIntersecting && entry.boundingClientRect.top < 0),
      { rootMargin: '-72px 0px 0px 0px' },
    )
    observer.observe(sentinel)
    return () => observer.disconnect()
  }, [])

  // Scroll-spy for the section rail.
  useEffect(() => {
    const targets = sections
      .map((section) => document.getElementById(section.id))
      .filter((node): node is HTMLElement => Boolean(node))
    if (!targets.length) return
    const observer = new IntersectionObserver(
      (entries) => {
        const visible = entries.filter((entry) => entry.isIntersecting)
        if (visible.length) {
          setActiveSection(visible.sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top)[0].target.id)
        }
      },
      { rootMargin: '-96px 0px -65% 0px' },
    )
    targets.forEach((node) => observer.observe(node))
    return () => observer.disconnect()
  }, [report])

  const evidenceById = useMemo(
    () => new Map((report?.evidence ?? []).map((item) => [item.evidence_id, item])),
    [report],
  )

  const sourceChips = useMemo(() => {
    const chips: string[] = []
    if (assessment.source.arxiv_id) chips.push(`arXiv:${assessment.source.arxiv_id}`)
    if (assessment.source.original_filename) chips.push(assessment.source.original_filename)
    chips.push(`${assessment.venue.code} ${assessment.venue.year} · rubric v${assessment.venue.rubric_version}`)
    chips.push(assessment.model.display_name)
    if (paper?.page_count) chips.push(`${paper.page_count} pages`)
    return chips
  }, [assessment, paper])

  if (!report || !paper) return null
  const overall = report.overall_assessment
  const filledTicks = overall.overall_score == null ? 0 : Math.round(overall.overall_score)

  return (
    <>
      <section className="view" aria-labelledby="report-title">
        <div className={`sticky-bar ${isCondensed ? 'is-shown' : ''}`}>
          <span className="sticky-title">{paper.title ?? 'Untitled paper'}</span>
          <span className="sticky-score" style={bandStyle(overall.overall_score)}>
            <b>{formatScore(overall.overall_score)}</b>
            <span>/ 10 · {enumLabel(labels.rating, overall.rating)}</span>
          </span>
          <button className="button button-secondary button-sm" type="button" onClick={onNewAssessment}>
            New assessment
          </button>
        </div>

        <header className="report-head">
          <div className="head-row">
            <p className="eyebrow">Assessment report</p>
            <button className="button button-secondary button-sm" type="button" onClick={onNewAssessment}>
              New assessment
            </button>
          </div>
          <h1 id="report-title">{paper.title ?? 'Untitled paper'}</h1>
          <p className="report-authors">
            {paper.authors.length ? paper.authors.join(' · ') : 'Authors were not recovered from the document.'}
          </p>
          <div className="source-chips">
            {sourceChips.map((chip) => <span className="chip" key={chip}>{chip}</span>)}
          </div>

          <div className="report-notices">
            {overall.is_provisional && (
              <div className="notice notice-amber">
                <span>
                  <strong>Provisional.</strong> At least one dimension could not be judged reliably.
                  The overall score covers {overall.calculation.assessed_dimension_count} of {report.dimensions.length} dimensions
                  {overall.calculation.excluded_dimension_ids.length > 0
                    && ` (excluded: ${overall.calculation.excluded_dimension_ids.join(', ')})`}.
                </span>
              </div>
            )}
            {assessment.warnings.length > 0 && (
              <div className="notice">
                <span>
                  <strong>Parsing notes.</strong> {assessment.warnings.map((item) => item.message).join(' ')}
                </span>
              </div>
            )}
          </div>
        </header>

        <div className="score-panel" style={bandStyle(overall.overall_score)}>
          <div className="score-figure">
            <p className="eyebrow">Overall score</p>
            <div className="score-value">
              <b>{formatScore(overall.overall_score)}</b>
              <span>/ 10</span>
            </div>
            <span className="score-rating">{enumLabel(labels.rating, overall.rating)}</span>
            <div className="score-ticks" aria-hidden="true">
              {Array.from({ length: 10 }, (_, index) => (
                <i className={index < filledTicks ? 'is-on' : ''} key={index} />
              ))}
            </div>
            <div className="score-scale" aria-hidden="true"><span>1</span><span>10</span></div>
          </div>
          <div className="verdict">
            <p className="eyebrow">Reading recommendation</p>
            <h2>{enumLabel(labels.recommendation, overall.reading_recommendation)}</h2>
            <p>{overall.summary}</p>
            <p className="verdict-reason">{overall.recommendation_reason}</p>
            <div className="verdict-meta">
              <span className="chip">{enumLabel(labels.confidence, overall.confidence)}</span>
              <span className="chip chip-outline">
                {overall.calculation.method.replace(/_/g, ' ')} · {overall.calculation.assessed_dimension_count} dimensions
              </span>
            </div>
          </div>
        </div>

        <div className="scorecard">
          {report.dimensions.map((item) => (
            <a className="scorecard-item" href="#dimensions" key={item.dimension_id} style={bandStyle(item.score)}>
              <small>{item.display_name}</small>
              <span className={`scorecard-value ${item.score == null ? 'is-none' : ''}`}>
                <b>{item.score == null ? enumLabel(labels.rating, item.rating) : formatScore(item.score)}</b>
                {item.score != null && <span>/10</span>}
              </span>
              <span className="scorecard-bar">
                <i style={{ width: `${item.score == null ? 0 : item.score * 10}%` }} />
              </span>
            </a>
          ))}
        </div>

        <div ref={sentinelRef} aria-hidden="true" />

        <div className="report-grid">
          <nav className="toc" aria-label="Report sections">
            {sections.map((section) => (
              <a
                className={activeSection === section.id ? 'is-active' : ''}
                href={`#${section.id}`}
                key={section.id}
              >
                {section.label}
              </a>
            ))}
          </nav>

          <div>
            <section className="section" id="summary">
              <div className="section-head"><h2>Summary</h2></div>
              <p className="prose">{report.paper_summary}</p>
            </section>

            <section className="section" id="dimensions">
              <div className="section-head">
                <h2>Dimension scores</h2>
                <span className="section-note">Scored 1–10 · unscored when evidence is missing</span>
              </div>
              <div className="dim-list">
                {report.dimensions.map((item) => (
                  <article className="dim" key={item.dimension_id} style={bandStyle(item.score)}>
                    <div className={`dim-score ${item.score == null ? 'is-none' : ''}`}>
                      <b>{item.score == null ? '—' : formatScore(item.score)}</b>
                      <em>{enumLabel(labels.rating, item.rating)}</em>
                      <span className="dim-bar">
                        <i style={{ width: `${item.score == null ? 0 : item.score * 10}%` }} />
                      </span>
                    </div>
                    <div>
                      <div className="dim-head">
                        <h3>{item.display_name}</h3>
                        <span className="chip chip-outline">{Math.round(item.weight * 100)}% weight</span>
                        <span className="chip">{enumLabel(labels.confidenceShort, item.confidence)}</span>
                      </div>
                      <p>{item.summary}</p>
                      {item.uncertainty_reason && (
                        <p className="uncertain-note">Not scored: {item.uncertainty_reason}</p>
                      )}
                      <EvidenceRow links={item.evidence_links} context={item.display_name} evidence={evidenceById} onOpen={openEvidence} />
                    </div>
                  </article>
                ))}
              </div>
            </section>

            <section className="section" id="findings">
              <div className="section-head"><h2>Strengths & concerns</h2></div>
              <div className="finding-cols">
                <FindingColumn title="Strengths" items={report.strengths} tone="strengths" evidence={evidenceById} onOpen={openEvidence} />
                <FindingColumn title="Concerns" items={report.concerns} tone="concerns" evidence={evidenceById} onOpen={openEvidence} />
              </div>
            </section>

            <section className="section" id="claims">
              <div className="section-head">
                <h2>Claim checks</h2>
                <span className="section-note">Does the paper's own evidence back its claims?</span>
              </div>
              {report.claim_checks.length ? report.claim_checks.map((item) => (
                <article className="claim" key={item.claim_id}>
                  <div className="claim-head">
                    <span className={`chip ${item.support_level === 'supported' ? 'chip-accent'
                      : item.support_level === 'unsupported' ? 'chip-crimson' : 'chip-amber'}`}>
                      {enumLabel(labels.support, item.support_level)}
                    </span>
                    <span className="chip chip-outline">{enumLabel(labels.confidenceShort, item.confidence)}</span>
                  </div>
                  <p className="claim-text">{item.claim}</p>
                  <p className="claim-reason">{item.reasoning}</p>
                  <EvidenceRow links={item.evidence_links} context="Claim check" evidence={evidenceById} onOpen={openEvidence} />
                </article>
              )) : <p className="empty">No reliable claim check was returned.</p>}
            </section>

            <section className="section" id="contributions">
              <div className="section-head"><h2>Claimed contributions</h2></div>
              {report.claimed_contributions.length ? report.claimed_contributions.map((item, index) => (
                <article className="contrib" key={item.contribution_id}>
                  <span className="contrib-index">{String(index + 1).padStart(2, '0')}</span>
                  <div>
                    <p>{item.statement}</p>
                    <div className="evidence-row contrib-type">
                      <span className="chip chip-outline">{item.contribution_type.replace(/_/g, ' ')}</span>
                    </div>
                    <EvidenceRow links={item.evidence_links} context="Claimed contribution" evidence={evidenceById} onOpen={openEvidence} />
                  </div>
                </article>
              )) : <p className="empty">No contribution statement was extracted.</p>}
            </section>

            <section className="section" id="reproducibility">
              <div className="section-head">
                <h2>Reproducibility</h2>
                <span className="section-note">What the paper states, not what was rerun</span>
              </div>
              <div className="checklist">
                {report.reproducibility_checklist.map((item) => (
                  <div className="check" key={item.check_id}>
                    <span className={`check-mark ${item.status}`} aria-hidden="true">{checkMarks[item.status] ?? '–'}</span>
                    <div className="check-body">
                      <strong>{item.display_name}</strong>
                      <span className={`check-status ${item.status}`}>{enumLabel(labels.repro, item.status)}</span>
                      <p>{item.detail}</p>
                      <EvidenceRow links={item.evidence_links} context={item.display_name} evidence={evidenceById} onOpen={openEvidence} />
                    </div>
                  </div>
                ))}
              </div>
            </section>

            <section className="section" id="questions">
              <div className="section-head">
                <h2>Open questions</h2>
                <span className="section-note">Worth resolving before relying on the results</span>
              </div>
              {report.unresolved_questions.length ? report.unresolved_questions.map((item, index) => (
                <article className="question" key={item.question_id}>
                  <span className="question-index" aria-hidden="true">{index + 1}</span>
                  <div>
                    <strong>{item.question}</strong>
                    <small>Why it matters: {item.why_it_matters}</small>
                    <EvidenceRow links={item.evidence_links} context="Open question" evidence={evidenceById} onOpen={openEvidence} />
                  </div>
                </article>
              )) : <p className="empty">No open question was returned.</p>}
            </section>

            <section className="section" id="limitations">
              <div className="section-head"><h2>Limitations of this report</h2></div>
              <ul className="limits">
                {report.limitations.map((item) => <li key={item}>{item}</li>)}
              </ul>
            </section>

            <footer className="report-foot">
              ICLR-inspired rubric · not an official review. Generated{' '}
              {new Date(report.generated_at).toLocaleString('en-AU', { dateStyle: 'medium', timeStyle: 'short' })}
              {' · '}assessment <code>{assessment.assessment_id}</code>
            </footer>
          </div>
        </div>
      </section>
      <EvidenceDrawer selection={selection} onClose={closeEvidence} onNavigate={navigateEvidence} />
    </>
  )
}
