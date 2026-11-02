import { useMemo } from 'react'
import { isRunning } from '../../app/queries'
import { categoryNames, duration } from '../../constants/credibility'
import { enumLabel, labels } from '../../constants/labels'
import type { Assessment, EvidenceLink, EvidenceReference } from '../../types/assessment'
import { outcomeNames, reasonName, referenceNames } from '../../ui/names'
import { buildModel, skills } from './model'
import type { Reported } from './widgets'

/* The assessment as one readable document (spec 5.5): Part I the credibility assessment, Part II the
   five-dimension quality assessment run beside it. The Report view shows it on screen; the print page prints it. */

const humanise = (value: string) => value.replaceAll('_', ' ')
const sentence = (value: string) => humanise(value).replace(/^./, letter => letter.toUpperCase())
const date = (iso: string | null | undefined) => {
  const value = iso ? new Date(iso) : null
  return value && !Number.isNaN(value.getTime()) ? value.toLocaleDateString('en-GB', { day: 'numeric', month: 'long', year: 'numeric' }) : null
}
/** Parsed reference text can run to a paragraph when the bibliography was split badly. */
const clip = (text: string, length = 180) => (text.length > length ? `${text.slice(0, length).trimEnd()}…` : text)
const sectionOf = { claim_evidence: 1, reference_verification: 2, code_reproduction: 3 } as const

interface Props {
  assessment: Reported
  /** Part II, the quality assessment started with the check; the print version includes it, the Report view does not. */
  quality?: Assessment
  qualityRequested?: boolean
  includeQuality?: boolean
  modelName: string
}

export function ReportDocument({ assessment, quality, qualityRequested = false, includeQuality = false, modelName }: Props) {
  const { report, paper } = assessment
  const model = useMemo(() => buildModel(report), [report])
  const { score, category } = report.overall
  const gaps = skills.filter(skill => model.fallback.has(skill.dimension))
  const summary = report.reference_verification_summary
  const code = report.checks.code
  const longest = Math.max(0, ...Object.values(report.execution.checks ?? {}).map(item => item?.elapsed_seconds ?? 0))
  const concerns = report.findings.filter(finding => finding.kind === 'concern')
  const gapGroups = Object.entries(report.findings.filter(finding => finding.kind === 'verification_gap')
    .reduce<Record<string, typeof report.findings>>((groups, finding) => ({ ...groups, [finding.title]: [...(groups[finding.title] ?? []), finding] }), {}))
  const quote = (ids: string[]) => ids.map(evidenceId => model.evidence.get(evidenceId)).filter(Boolean).map(item =>
    <blockquote key={item!.evidence_id}>{item!.excerpt}<cite>p. {item!.paper_locator?.page_start ?? '—'}{item!.paper_locator?.section_title ? `, ${item!.paper_locator.section_title}` : ''}</cite></blockquote>)
  const assessed = date(assessment.completed_at ?? report.generated_at)

  return <>
    <header>
      <p className="is-kicker">PaperTrace · Assessment report</p>
      <h1>{paper?.title ?? 'Untitled paper'}</h1>
      <p className="is-meta">
        {paper?.arxiv_id ? `arXiv:${paper.arxiv_id}` : 'Uploaded PDF'}{paper?.page_count ? ` · ${paper.page_count} pages` : ''}
        {assessed ? ` · Assessed ${assessed}` : ''} · {modelName}{longest ? ` · ${duration(longest)}` : ''} · Rubric {report.rubric_version} ({report.rubric_status})
      </p>
    </header>

    <p className="is-part">Part I · Credibility assessment</p>
    <section id="doc-verdict" className="is-verdict">
      <div><b>{score == null ? '—' : score.toFixed(1)}</b><span>/ 10</span></div>
      <div>
        <h2>{category ? categoryNames[category] : 'Not assessed'}</h2>
        <p>Trial score under a provisional rubric; not an approved assessment.</p>
        <ul>
          <li><b>{model.concerns}</b> concerns identified</li>
          <li><b>{model.gaps}</b> verification gaps</li>
          <li><b>{model.fallback.size}</b> fallback scores</li>
        </ul>
      </div>
    </section>
    <p>The overall score is the unweighted mean of the three checks: ({skills.map(skill => model.dimensions[skill.dimension].score?.toFixed(1) ?? '—').join(' + ')}) ÷ 3 = {score?.toFixed(1) ?? '—'}.
      {gaps.length > 0 && ` ${gaps.map(skill => skill.name).join(' and ')} received fallback scores because those checks did not complete.`}</p>

    <section id="doc-claims">
      <h2>1. Claims and evidence <small>{model.dimensions.claim_evidence.score?.toFixed(1) ?? '—'} / 10</small></h2>
      {model.claims.map(claim => <article key={claim.claim_id} className="is-item">
        <h3>{claim.label}. {claim.statement}</h3>
        <p className="is-meta">{outcomeNames[claim.outcome]} · {claim.importance} claim</p>
        <p>{claim.reason}</p>
        {claim.claim_evidence_ids.length > 0 && <><p className="is-label">Claim as stated</p>{quote(claim.claim_evidence_ids)}</>}
        {claim.comparison_evidence_ids.length > 0 && <><p className="is-label">Supporting passages</p>{quote(claim.comparison_evidence_ids)}</>}
      </article>)}
    </section>

    <section id="doc-references">
      <h2>2. References <small>{model.dimensions.reference_verification.score?.toFixed(1) ?? '—'} / 10</small></h2>
      <p>{summary.matched_count} of {summary.extracted_count} references were matched to a bibliographic record; {summary.unverified_count} could not be verified. An unverified reference does not indicate an error.</p>
      {summary.unresolved_items.length > 0 && <table className="is-refs">
        <thead><tr><th>Reference</th><th>Outcome</th><th>Reason</th></tr></thead>
        <tbody>{summary.unresolved_items.map(item => {
          const ref = model.references.get(item.reference_id)
          return <tr key={item.reference_id}>
            <td>{clip(ref?.parsed.title || ref?.raw_text || item.reference_id)}<br /><span className="is-meta">{(ref?.parsed.authors ?? []).slice(0, 3).join(', ')}{ref?.parsed.year ? ` (${ref.parsed.year})` : ''}</span></td>
            <td>{referenceNames[item.outcome]}</td><td>{reasonName(item.reason_code)}</td>
          </tr>
        })}</tbody>
      </table>}
    </section>

    <section id="doc-code">
      <h2>3. Code and reproduction <small>{model.dimensions.code_reproduction.score?.toFixed(1) ?? '—'} / 10</small></h2>
      <p>Code availability: {humanise(code.code_availability)}. Reproduction signal: {humanise(code.reproduction_signal)}. {report.code_discovery.queries.length} web searches, {report.code_discovery.candidates.length} candidate links examined.</p>
      <table>
        <thead><tr><th>Source examined</th><th>Outcome</th><th>Note</th></tr></thead>
        <tbody>{code.searches.map(search => <tr key={search.scope_id}><td>{sentence(search.scope_type)}</td><td>{sentence(search.outcome)}</td><td>{search.reason}</td></tr>)}</tbody>
      </table>
    </section>

    {/* Context findings restate the claim reasons above, and gaps are itemised in sections 2–3, so both are only counted here. */}
    <section id="doc-findings">
      <h2>4. Summary of findings</h2>
      <p className="is-label">Concerns ({concerns.length})</p>
      {concerns.length ? <ul>{concerns.map(finding => <li key={finding.finding_id}><b>{finding.title}.</b> {finding.detail} <span className="is-meta">({finding.severity}, {finding.certainty}; section {sectionOf[finding.dimension_id]})</span></li>)}</ul>
        : <p>No concerns were identified. This does not establish that the paper is free of problems; see the verification gaps below.</p>}
      <p className="is-label">Verification gaps ({model.gaps})</p>
      {gapGroups.length ? <ul>{gapGroups.map(([title, items]) => <li key={title}><b>{title}</b> — {items.length} {items.length === 1 ? 'item' : 'items'}, section {sectionOf[items[0].dimension_id]}.</li>)}</ul>
        : <p>Every item in scope was checked.</p>}
    </section>

    <section id="doc-limits">
      <h2>5. Limitations</h2>
      <ul>{report.limitations.map(line => <li key={line}>{line}</li>)}</ul>
    </section>

    {includeQuality && <>
      <p className="is-part">Part II · Quality assessment (five dimensions)</p>
      <QualityPart quality={quality} requested={qualityRequested} />
    </>}

    <RunDetails assessment={assessment} modelName={modelName} quality={includeQuality ? quality : undefined} />
    <footer>Report {report.report_id} · Assessment {assessment.assessment_id}{includeQuality && quality ? ` · Quality assessment ${quality.assessment_id}` : ''} · {assessment.schema_version}</footer>
  </>
}

const stamp = (iso: string | null | undefined) => {
  const value = iso ? new Date(iso) : null
  return value && !Number.isNaN(value.getTime()) ? value.toLocaleString('en-GB', { day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' }) : '—'
}

/** Who produced the assessment and when (spec 5.5): model with its version, and the start and completion times. */
function RunDetails({ assessment, modelName, quality }: { assessment: Reported; modelName: string; quality?: Assessment }) {
  const versions = [...new Set(assessment.report.versions.model_calls.map(call => call.model_version))]
  return <section id="doc-run">
    <h2>Run details</h2>
    <table>
      <tbody>
        <tr><th>Credibility assessment</th><td>{modelName}{versions.length ? ` (${versions.join(', ')})` : ''} · {stamp(assessment.created_at)} – {stamp(assessment.completed_at)}</td></tr>
        {quality && <tr><th>Quality assessment</th><td>{quality.model.display_name} ({quality.model.model_version}) · {stamp(quality.created_at)} – {stamp(quality.completed_at)}</td></tr>}
      </tbody>
    </table>
  </section>
}

function QualityPart({ quality, requested }: { quality?: Assessment; requested: boolean }) {
  const evidence = useMemo(() => new Map((quality?.report?.evidence ?? []).map(item => [item.evidence_id, item])), [quality])
  if (!quality?.report) return <section id="doc-quality">
    <p>{!requested ? 'No quality assessment was run with this check. It can be started from the Quality assessment widget in the Paper view.'
      : quality && isRunning(quality) ? 'The quality assessment is still running and will appear here when it completes.'
      : quality?.status === 'failed' ? `The quality assessment did not complete: ${quality.error?.message ?? 'unknown error'}.`
      : 'Loading the quality assessment…'}</p>
  </section>
  const report = quality.report
  const overall = report.overall_assessment
  const pages = (links: EvidenceLink[]) => {
    const found = links.map(link => evidence.get(link.evidence_id)).filter((item): item is EvidenceReference => Boolean(item?.page_number))
    const unique = [...new Set(found.map(item => item.page_number))].sort((a, b) => a! - b!)
    return unique.length ? <span className="is-meta"> Evidence: {unique.map(page => `p. ${page}`).join(', ')}.</span> : null
  }
  const quoteOf = (links: EvidenceLink[]) => links.map(link => evidence.get(link.evidence_id)).filter((item): item is EvidenceReference => Boolean(item))
    .slice(0, 2).map(item => <blockquote key={item.evidence_id}>{item.excerpt}<cite>{item.page_number ? `p. ${item.page_number}` : 'Location not recorded'}{item.section_title ? `, ${item.section_title}` : ''}</cite></blockquote>)

  return <>
    <section id="doc-quality" className="is-verdict">
      <div><b>{overall.overall_score == null ? '—' : overall.overall_score.toFixed(1)}</b><span>/ 10</span></div>
      <div>
        <h2>{enumLabel(labels.rating, overall.rating)} · {enumLabel(labels.recommendation, overall.reading_recommendation)}</h2>
        <p>{overall.summary}</p>
        <p className="is-meta">{overall.recommendation_reason} {enumLabel(labels.confidence, overall.confidence)}; {humanise(overall.calculation.method)} of {overall.calculation.assessed_dimension_count} of {report.dimensions.length} dimensions{overall.is_provisional ? '; provisional' : ''}.</p>
      </div>
    </section>
    <p>{report.paper_summary}</p>

    <section id="doc-dimensions">
      <h2>6. Dimensions</h2>
      <table>
        <thead><tr><th>Dimension</th><th>Score</th><th>Rating</th><th>Weight</th></tr></thead>
        <tbody>{report.dimensions.map(item => <tr key={item.dimension_id}>
          <td>{item.display_name}</td><td>{item.score == null ? 'Not scored' : `${item.score.toFixed(1)} / 10`}</td>
          <td>{enumLabel(labels.rating, item.rating)}</td><td>{Math.round(item.weight * 100)}%</td>
        </tr>)}</tbody>
      </table>
      {report.dimensions.map(item => <article key={item.dimension_id} className="is-item">
        <h3>{item.display_name}</h3>
        <p>{item.summary}{pages(item.evidence_links)}</p>
        {item.uncertainty_reason && <p className="is-meta">Not scored: {item.uncertainty_reason}</p>}
      </article>)}
    </section>

    <section id="doc-strengths">
      <h2>7. Strengths and concerns</h2>
      <p className="is-label">Strengths ({report.strengths.length})</p>
      <ul>{report.strengths.map(item => <li key={item.finding_id}><b>{item.title}{item.importance === 'major' ? ' (major)' : ''}.</b> {item.detail}{pages(item.evidence_links)}</li>)}</ul>
      <p className="is-label">Concerns ({report.concerns.length})</p>
      <ul>{report.concerns.map(item => <li key={item.finding_id}><b>{item.title}{item.importance === 'major' ? ' (major)' : ''}.</b> {item.detail}{pages(item.evidence_links)}</li>)}</ul>
    </section>

    <section id="doc-claimchecks">
      <h2>8. Claim checks</h2>
      {report.claim_checks.length ? report.claim_checks.map(item => <article key={item.claim_id} className="is-item">
        <h3>{item.claim}</h3>
        <p className="is-meta">{enumLabel(labels.support, item.support_level)} · {enumLabel(labels.confidence, item.confidence)}</p>
        <p>{item.reasoning}</p>
        {quoteOf(item.evidence_links)}
      </article>) : <p>No claim check was returned.</p>}
    </section>

    <section id="doc-contributions">
      <h2>9. Claimed contributions</h2>
      {report.claimed_contributions.length ? <ol>{report.claimed_contributions.map(item =>
        <li key={item.contribution_id}>{item.statement} <span className="is-meta">({humanise(item.contribution_type)})</span>{pages(item.evidence_links)}</li>)}</ol>
        : <p>No contribution statement was extracted.</p>}
    </section>

    <section id="doc-repro">
      <h2>10. Reproducibility</h2>
      <table>
        <thead><tr><th>Item</th><th>Status</th><th>Detail</th></tr></thead>
        <tbody>{report.reproducibility_checklist.map(item => <tr key={item.check_id}>
          <td>{item.display_name}</td><td>{enumLabel(labels.repro, item.status)}</td><td>{item.detail}</td>
        </tr>)}</tbody>
      </table>
    </section>

    <section id="doc-questions">
      <h2>11. Open questions</h2>
      {report.unresolved_questions.length ? <ol>{report.unresolved_questions.map(item =>
        <li key={item.question_id}><b>{item.question}</b> <span className="is-meta">Why it matters: {item.why_it_matters}</span></li>)}</ol>
        : <p>No open question was returned.</p>}
    </section>

    <section id="doc-qlimits">
      <h2>12. Limitations</h2>
      <ul>{report.limitations.map(line => <li key={line}>{line}</li>)}</ul>
      <p className="is-meta">ICLR-inspired rubric; not an official review.</p>
    </section>
  </>
}
