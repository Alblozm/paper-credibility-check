import { useMemo, useRef, useState, type ReactNode } from 'react'
import { enumLabel, labels } from '../../constants/labels'
import type { Assessment, AssessmentReport, DimensionAssessment, EvidenceLink, EvidenceReference, Finding } from '../../types/assessment'
import { Icon, type IconName } from '../../ui/Icon'
import { useAnimatedNumber, useViewCount } from '../../ui/motion'
import { useSectionSpy } from '../../ui/useSectionSpy'
import { ScoreGauge } from '../../ui/ScoreGauge'

type Evidence = Map<string, EvidenceReference>
export type Scored = Assessment & { report: AssessmentReport }

/** Five-dimension rating bands: weak below 5.5, adequate to 7, good to 8.5, strong above. */
const bands = [5.5, 7, 8.5]

const sections = [
  { id: 'q-summary', label: 'Paper summary' },
  { id: 'q-dimensions', label: 'Dimensions' },
  { id: 'q-findings', label: 'Strengths and concerns' },
  { id: 'q-claims', label: 'Claim checks' },
  { id: 'q-contributions', label: 'Contributions' },
  { id: 'q-reproducibility', label: 'Reproducibility' },
  { id: 'q-questions', label: 'Open questions' },
  { id: 'q-limitations', label: 'Limitations' },
]
const sectionIds = sections.map(section => section.id)

const supportPill: Record<string, string> = { supported: 'supported', partially_supported: 'partially_supported', unsupported: 'contradicted', uncertain: 'gap' }
const reproIcon: Record<string, IconName> = { available: 'checkmark_circle', not_available: 'dismiss', unable_to_verify: 'question_circle', not_applicable: 'circle_half_fill' }

/** The completion date; reports from some backends carry a placeholder instead of a timestamp. */
function assessedOn(iso: string | null) {
  const date = iso ? new Date(iso) : null
  return date && !Number.isNaN(date.getTime()) ? `Assessed ${date.toLocaleDateString('en-GB', { day: 'numeric', month: 'long', year: 'numeric' })}` : null
}

/**
 * "p. 7 · Table 2": where to look. For body text the locator label is an internal chunk ID, so the section is shown instead.
 */
function where(reference: EvidenceReference) {
  const parts: string[] = []
  if (reference.page_number != null) parts.push(`p. ${reference.page_number}`)
  if (reference.locator_type !== 'text' && reference.locator_label) parts.push(reference.locator_label)
  else if (reference.section_title) parts.push(reference.section_title)
  return parts.join(' · ') || 'Location not recorded'
}

/** Evidence chips; one opens at a time as a quotation card under the row (spec 5.6). */
function EvidenceRow({ links, evidence, arxivId }: { links: EvidenceLink[]; evidence: Evidence; arxivId: string | null }) {
  const [open, setOpen] = useState<number | null>(null)
  const [copied, setCopied] = useState(false)
  const resolved = links.filter(link => evidence.has(link.evidence_id))
  if (!resolved.length) return null
  const link = open == null ? null : resolved[open]
  const reference = link ? evidence.get(link.evidence_id)! : null
  const copy = async () => {
    if (!reference) return
    try { await navigator.clipboard.writeText(`“${reference.excerpt}” (${where(reference)})`); setCopied(true) } catch { setCopied(false) }
  }
  return <div className="cs-q-ev">
    <div className="cs-q-chips">
      {resolved.map((item, index) => <button key={`${item.evidence_id}-${index}`} type="button" aria-expanded={open === index}
        className={`cs-q-chip is-${item.stance} ${open === index ? 'is-on' : ''}`} onClick={() => { setOpen(open === index ? null : index); setCopied(false) }}>
        <i aria-hidden="true" />{enumLabel(labels.stance, item.stance)} · {where(evidence.get(item.evidence_id)!)}
      </button>)}
    </div>
    {link && reference && <figure key={open} className="cs-q-quote">
      <blockquote>{reference.excerpt}</blockquote>
      <figcaption>{reference.evidence_mode === 'quote' ? 'Quotation' : 'Paraphrase'} · {where(reference)}{link.reason ? ` — ${link.reason}` : ''}</figcaption>
      <div className="is-acts">
        <button type="button" onClick={() => void copy()}><Icon name="copy" />{copied ? 'Copied' : 'Copy quotation'}</button>
        {arxivId && reference.page_number != null && <a href={`https://arxiv.org/pdf/${arxivId}#page=${reference.page_number}`} target="_blank" rel="noreferrer">
          <Icon name="open" />Open page {reference.page_number} on arXiv</a>}
      </div>
    </figure>}
  </div>
}

/** One dimension: a groove bar with the rating bands marked; it counts up again whenever it comes back into view. */
function DimensionRow({ item, evidence, arxivId }: { item: DimensionAssessment; evidence: Evidence; arxivId: string | null }) {
  const meter = useRef<HTMLDivElement>(null)
  const entries = useViewCount(meter)
  const shown = useAnimatedNumber(entries > 0 && item.score != null ? item.score : 0, 1300, entries)
  const rating = enumLabel(labels.rating, item.rating)
  return <article className="cs-q-dim">
    <div ref={meter} className={`cs-q-meter ${item.score == null ? 'is-none' : ''}`} role="img"
      aria-label={item.score == null ? `${item.display_name}: not scored` : `${item.display_name}: ${item.score.toFixed(1)} out of 10, ${rating}`}>
      <b className="cs-num" aria-hidden="true">{item.score == null ? '—' : shown.toFixed(1)}<small>/10</small></b>
      <span className="cs-q-track cs-groove" aria-hidden="true">
        {item.score != null && <i className="cs-lit" style={{ width: `${shown * 10}%` }} />}
        {bands.map(edge => <s key={edge} style={{ left: `${edge * 10}%` }} />)}
      </span>
      <em aria-hidden="true">{item.score == null ? 'Not scored' : rating}</em>
    </div>
    <div className="grid gap-1.5">
      <header className="flex flex-wrap items-baseline gap-x-3 gap-y-0.5">
        <h3 className="cs-serif text-title-s font-semibold">{item.display_name}</h3>
        <span className="text-caption text-muted">{Math.round(item.weight * 100)}% of the overall score · {enumLabel(labels.confidence, item.confidence)}</span>
      </header>
      <p className="text-body">{item.summary}</p>
      {item.uncertainty_reason && <p className="cs-q-note"><Icon name="info" />Not scored: {item.uncertainty_reason}</p>}
      <EvidenceRow links={item.evidence_links} evidence={evidence} arxivId={arxivId} />
    </div>
  </article>
}

function FindingList({ title, items, tone, evidence, arxivId }: { title: string; items: Finding[]; tone: 'strength' | 'concern'; evidence: Evidence; arxivId: string | null }) {
  return <div className={`cs-q-col is-${tone}`}>
    <h3><span>{title}</span><span className="cs-badge">{items.length}</span></h3>
    {items.length ? items.map(item => <article key={item.finding_id} className="cs-q-item">
      <header className="flex items-start justify-between gap-3"><h4 className="font-bold">{item.title}</h4>
        {item.importance === 'major' && <span className={`cs-pill ${tone === 'concern' ? 'concern' : 'supported'}`}>Major</span>}</header>
      <p>{item.detail}</p>
      <EvidenceRow links={item.evidence_links} evidence={evidence} arxivId={arxivId} />
    </article>) : <p className="cs-lead">None recorded.</p>}
  </div>
}

const Section = ({ id, title, note, children }: { id: string; title: string; note?: string; children: ReactNode }) =>
  <section id={id} className="cs-q-sec" aria-labelledby={`${id}-h`}>
    <header><h2 id={`${id}-h`} className="cs-serif text-title font-semibold">{title}</h2>{note && <span className="text-small text-muted">{note}</span>}</header>
    {children}
  </section>

/** Five-dimension quality assessment (spec 5.6): a document page in the same visual language as the credibility report. */
export function QualityReport({ assessment, embedded = false }: { assessment: Scored; embedded?: boolean }) {
  const { report, paper, source, venue, model } = assessment
  const overall = report.overall_assessment
  const evidence = useMemo<Evidence>(() => new Map(report.evidence.map(item => [item.evidence_id, item])), [report])
  const arxivId = paper?.arxiv_id ?? source.arxiv_id
  const { on, go } = useSectionSpy(sectionIds)
  const excluded = overall.calculation.excluded_dimension_ids
    .map(id => report.dimensions.find(item => item.dimension_id === id)?.display_name ?? id)
  const meta = [
    arxivId ? `arXiv:${arxivId}` : source.original_filename ?? 'Uploaded PDF',
    paper?.page_count ? `${paper.page_count} pages` : null,
    `${venue.code} ${venue.year} rubric ${venue.rubric_version}`,
    model.display_name,
    assessedOn(assessment.completed_at ?? report.generated_at),
  ].filter(Boolean)

  // Inside a run page the part bar already names the run, so the heading is smaller.
  return <main className={`mx-auto grid w-full max-w-[1120px] gap-6 px-4 pb-20 sm:px-6 ${embedded ? 'pt-6' : 'pt-8'}`}>
    <header className="grid gap-2">
      <p className="cs-eyebrow">Quality assessment · five dimensions</p>
      <h1 className={`cs-serif font-semibold ${embedded ? 'text-title-l' : 'text-display max-sm:text-title-l'}`}>{paper?.title ?? 'Untitled paper'}</h1>
      {paper?.authors.length ? <p className="text-body-l text-muted">{paper.authors.join(', ')}</p> : null}
      <p className="text-small text-muted">{meta.join(' · ')}</p>
    </header>

    <section className="cs-card cs-q-verdict" aria-label="Overall assessment">
      {/* The small gauge keeps the quality score visually below the credibility score (spec 3.1). */}
      <ScoreGauge mode="score" small size={168} value={overall.overall_score} label={enumLabel(labels.rating, overall.rating)} bands={bands} />
      <div className="grid content-start gap-2.5">
        <p className="cs-eyebrow">Reading recommendation</p>
        <h2 className="cs-serif text-title-l font-semibold">{enumLabel(labels.recommendation, overall.reading_recommendation)}</h2>
        <p className="text-body-l">{overall.summary}</p>
        <p className="text-body text-muted">{overall.recommendation_reason}</p>
        <p className="text-small text-muted">{enumLabel(labels.confidence, overall.confidence)} · {overall.calculation.method.replaceAll('_', ' ')} of {overall.calculation.assessed_dimension_count} of {report.dimensions.length} dimensions</p>
        {overall.is_provisional && <p className="cs-q-note"><Icon name="info" />At least one dimension could not be judged reliably{excluded.length ? `; excluded from the score: ${excluded.join(', ')}` : ''}.</p>}
        {assessment.warnings.length > 0 && <p className="cs-q-note"><Icon name="document_search" />Parsing notes: {assessment.warnings.map(item => item.message).join(' ')}</p>}
      </div>
    </section>

    <div className="grid items-start gap-8 lg:grid-cols-[180px_minmax(0,1fr)]">
      <nav className="cs-q-toc max-lg:hidden" aria-label="Report sections">
        <ul className="cs-outline">{sections.map(section => <li key={section.id}>
          <button type="button" className={on === section.id ? 'is-on' : ''} aria-current={on === section.id ? 'true' : undefined} onClick={() => go(section.id)}>
            <span>{section.label}</span></button>
        </li>)}</ul>
      </nav>

      <div className="grid gap-10">
        <Section id="q-summary" title="Paper summary">
          <p className="cs-q-prose">{report.paper_summary}</p>
        </Section>

        <Section id="q-dimensions" title="Dimensions" note="Scored 1–10; a dimension without sufficient evidence is not scored">
          <div className="cs-card divide-y divide-hairline">{report.dimensions.map(item =>
            <DimensionRow key={item.dimension_id} item={item} evidence={evidence} arxivId={arxivId} />)}</div>
        </Section>

        <Section id="q-findings" title="Strengths and concerns">
          <div className="grid gap-4 md:grid-cols-2">
            <FindingList title="Strengths" items={report.strengths} tone="strength" evidence={evidence} arxivId={arxivId} />
            <FindingList title="Concerns" items={report.concerns} tone="concern" evidence={evidence} arxivId={arxivId} />
          </div>
        </Section>

        <Section id="q-claims" title="Claim checks" note="Whether the paper’s own evidence supports each claim">
          {report.claim_checks.length ? <div className="cs-card divide-y divide-hairline">{report.claim_checks.map(item =>
            <article key={item.claim_id} className="cs-q-item">
              <header className="flex flex-wrap items-center gap-2">
                <span className={`cs-pill ${supportPill[item.support_level] ?? 'gap'}`}>{enumLabel(labels.support, item.support_level)}</span>
                <span className="text-caption text-muted">{enumLabel(labels.confidence, item.confidence)}</span>
              </header>
              <p className="cs-serif text-body-l">{item.claim}</p>
              <p className="text-body text-muted">{item.reasoning}</p>
              <EvidenceRow links={item.evidence_links} evidence={evidence} arxivId={arxivId} />
            </article>)}</div> : <p className="cs-lead">No claim check was returned.</p>}
        </Section>

        <Section id="q-contributions" title="Claimed contributions">
          {report.claimed_contributions.length ? <ol className="cs-card divide-y divide-hairline">{report.claimed_contributions.map((item, index) =>
            <li key={item.contribution_id} className="cs-q-item cs-q-num">
              <span className="cs-num" aria-hidden="true">{String(index + 1).padStart(2, '0')}</span>
              <div className="grid gap-1.5">
                <p>{item.statement}</p>
                <span className="cs-tag">{item.contribution_type.replaceAll('_', ' ')}</span>
                <EvidenceRow links={item.evidence_links} evidence={evidence} arxivId={arxivId} />
              </div>
            </li>)}</ol> : <p className="cs-lead">No contribution statement was extracted.</p>}
        </Section>

        <Section id="q-reproducibility" title="Reproducibility" note="What the paper states; nothing was re-run">
          <ul className="cs-card divide-y divide-hairline">{report.reproducibility_checklist.map(item =>
            <li key={item.check_id} className={`cs-q-item cs-q-repro is-${item.status}`}>
              <Icon name={reproIcon[item.status] ?? 'circle_half_fill'} />
              <div className="grid gap-1">
                <p className="flex flex-wrap items-baseline gap-x-2"><b>{item.display_name}</b><span className="text-small">{enumLabel(labels.repro, item.status)}</span></p>
                <p className="text-body text-muted">{item.detail}</p>
                <EvidenceRow links={item.evidence_links} evidence={evidence} arxivId={arxivId} />
              </div>
            </li>)}</ul>
        </Section>

        <Section id="q-questions" title="Open questions" note="Worth resolving before relying on the results">
          {report.unresolved_questions.length ? <ol className="cs-card divide-y divide-hairline">{report.unresolved_questions.map((item, index) =>
            <li key={item.question_id} className="cs-q-item cs-q-num">
              <span className="cs-num" aria-hidden="true">{index + 1}</span>
              <div className="grid gap-1">
                <p className="font-bold">{item.question}</p>
                <p className="text-body text-muted">Why it matters: {item.why_it_matters}</p>
                <EvidenceRow links={item.evidence_links} evidence={evidence} arxivId={arxivId} />
              </div>
            </li>)}</ol> : <p className="cs-lead">No open question was returned.</p>}
        </Section>

        <Section id="q-limitations" title="Limitations of this assessment">
          <ul className="cs-q-limits">{report.limitations.map(line => <li key={line}>{line}</li>)}</ul>
          <p className="text-caption text-muted">ICLR-inspired rubric; not an official review. Assessment {assessment.assessment_id}.</p>
        </Section>
      </div>
    </div>
  </main>
}
