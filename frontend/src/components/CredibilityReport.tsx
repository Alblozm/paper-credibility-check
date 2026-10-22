import { useEffect, useRef, useState } from 'react'
import type { CredibilityAssessment, CredibilityDimensionId, CredibilityReport as Report, CredibilityScenario } from '../types/credibility'
import { categoryNames, dimensionNames, outcomeNames } from '../constants/credibility'
import { CredibilityEvidenceDrawer } from './CredibilityEvidenceDrawer'
import { CredibilityProcess } from './CredibilityProcess'
import './CredibilityWorkspace.css'

interface Props {
  assessment: CredibilityAssessment & { report: Report }
  live: boolean
  /** Mock mode only: the scenario that produced this sample. */
  scenario?: CredibilityScenario
  newLabel?: string
  onNew: () => void
}

/** One finished credibility report: the user's own check, an example, or a saved check. */
export function CredibilityReport({ assessment, live, scenario, newLabel = 'New check', onNew }: Props) {
  const [selectedEvidence, setSelectedEvidence] = useState<string | null>(null)
  const [activeDimension, setActiveDimension] = useState<CredibilityDimensionId>('claim_evidence')
  const heading = useRef<HTMLHeadingElement | null>(null)
  useEffect(() => { heading.current?.focus() }, [assessment.assessment_id])

  const report = assessment.report
  const paperPdfUrl = live
    ? assessment.paper?.arxiv_id ? `https://arxiv.org/pdf/${assessment.paper.arxiv_id}` : null
    : scenario === 'first_case' ? 'https://arxiv.org/pdf/2310.01798v1' : null
  const claimCount = report.checks.claims.items.length
  const trial = report.overall.calculation.method === 'equal_weight_trial_dimensions'
  const fallbackIds = report.overall.calculation.fallback_dimension_ids ?? []
  function evidenceLinks(ids: string[], title = 'Evidence') {
    if (!ids.length) return <span className="cred-muted">No evidence recorded for this item.</span>
    return <div className="cred-evidence-links" aria-label={title}>{ids.map(id => {
      const evidence = report?.evidence.find(item => item.evidence_id === id)
      const locator = evidence?.paper_locator
      const label = locator ? `${locator.element_label || locator.section_title || 'Passage'} · p. ${locator.page_start}` : 'External source'
      return <button className="cred-evidence-button" type="button" key={id} onClick={() => setSelectedEvidence(id)} disabled={!evidence}>{label}<span aria-hidden="true"> ↗</span></button>
    })}</div>
  }

  function sourceRecords(ids: string[]) {
    return ids.map(id => {
      const source = report?.sources.find(item => item.source_id === id)
      if (!source) return null
      return <details className="cred-source" key={id}>
        <summary>Source record · {source.kind.replaceAll('_', ' ')} <span>{source.fetch_status.replaceAll('_', ' ')}</span></summary>
        <p>{source.association_reason}</p>
        <dl className="cred-facts"><div><dt>Observed</dt><dd>{source.observed_at}</dd></div><div><dt>Retrieved</dt><dd>{source.retrieved_at}</dd></div><div><dt>Cache</dt><dd>{source.is_cached ? 'Cached source' : 'No cache'}</dd></div></dl>
        <span className="cred-muted">{live ? 'URL' : 'Synthetic source URL'} · {source.url}</span>
      </details>
    })
  }

  function detailsFor(active: CredibilityDimensionId, value: Report) {
    const dimension = value.dimensions.find(item => item.dimension_id === active)!
    return <section className="cred-detail" role="tabpanel" id={`panel-${active}`} aria-labelledby={`tab-${active}`}>
      <div className="cred-detail-intro"><h2>{dimensionNames[active]}</h2><p>{dimension.summary}</p></div>
      <details className="cred-scope"><summary>Applicability & scoring reason</summary><p>{dimension.applicability_reason}</p>{dimension.unassessed_reason && <p>{dimension.unassessed_reason}</p>}{evidenceLinks(dimension.evidence_ids, 'Dimension evidence')}</details>
      {value.findings.filter(item => item.dimension_id === active).map(item => <article className={`cred-finding is-${item.kind}`} key={item.finding_id}>
        <div className="cred-item-label">{item.severity} · {item.certainty.replaceAll('_', ' ')}</div><h3>{item.title}</h3><p>{item.detail}</p>{evidenceLinks(item.evidence_ids)}{sourceRecords(item.source_ids)}{item.limitations.map(line => <p className="cred-muted" key={line}>{line}</p>)}
      </article>)}
      {active === 'claim_evidence' && <>
        <div className="cred-section-note"><span>{value.checks.claims.scope_complete ? 'Selected scope checked' : 'Scope incomplete'}</span><p>{value.checks.claims.selection_scope}</p></div>
        {value.checks.claims.items.map((item, index) => <article className="cred-claim" key={item.claim_id}>
          <div className="cred-item-top"><span className="cred-item-label">Claim {String(index + 1).padStart(2, '0')} · {item.importance}</span><span className={`cred-outcome is-${item.outcome}`}>{outcomeNames[item.outcome]}</span></div>
          <h3>{item.statement}</h3><p>{item.reason}</p>
          <div className="cred-comparison"><div><h4>The claim</h4>{evidenceLinks(item.claim_evidence_ids, 'Claim evidence')}</div><div><h4>The comparison</h4>{evidenceLinks(item.comparison_evidence_ids, 'Comparison evidence')}</div></div>
          <details className="cred-scope"><summary>Conditions & search scope</summary><p>{item.conditions}</p><p>{item.search_scope}</p></details>
        </article>)}
        {value.checks.claims.scope_limitations.map(line => <p className="cred-limitation" key={line}>{line}</p>)}
      </>}
      {active === 'reference_verification' && <>
        <div className="cred-counts"><div><b>{value.checks.references.extracted_count}</b><span>Extracted</span></div><div><b>{value.checks.references.checked_count}</b><span>Checked</span></div><div><b>{value.checks.references.matched_count}</b><span>Matched</span></div><div><b>{value.checks.references.conflict_count}</b><span>Metadata conflict</span></div><div><b>{value.checks.references.unverified_count}</b><span>Unverified</span></div><div><b>{value.checks.references.not_checked_count}</b><span>Not checked</span></div></div>
        <dl className="cred-facts"><div><dt>Query completion</dt><dd>{ratio(value.checks.references.query_completion_ratio)}</dd></div><div><dt>Extraction coverage</dt><dd>{ratio(value.checks.references.extraction_coverage_ratio)}</dd></div><div><dt>References in original</dt><dd>{value.checks.references.original_total ?? 'Unknown'}</dd></div></dl>
        {value.checks.references.items.length === 0 && <p className="cred-empty">{live ? 'No reference results yet.' : 'Reference verification has not run in this sample.'} Empty results do not mean no concerns.</p>}
        {value.checks.references.items.map(item => <article className="cred-reference" key={item.reference_id}><div className="cred-item-top"><span className="cred-item-label">Reference</span><span className={`cred-outcome is-${item.outcome}`}>{outcomeNames[item.outcome]}</span></div><h3>{item.parsed.title || item.raw_text}</h3><p>{item.reason}</p>{evidenceLinks(item.paper_evidence_ids)}{item.candidates.map(candidate => <details className="cred-scope" key={candidate.candidate_id}><summary>{candidate.candidate_id === item.selected_candidate_id ? 'Selected match' : 'Candidate match'}</summary><p>{candidate.metadata.title}</p><p>{candidate.match_basis}</p>{evidenceLinks(candidate.evidence_ids)}</details>)}{sourceRecords(item.source_ids)}</article>)}
        {value.checks.references.gaps.map(line => <p className="cred-limitation" key={line}>{line}</p>)}
      </>}
      {active === 'code_reproduction' && <>
        <dl className="cred-facts"><div><dt>Code availability</dt><dd>{outcomeNames[value.checks.code.code_availability]}</dd></div><div><dt>Reproduction signal</dt><dd>{outcomeNames[value.checks.code.reproduction_signal]}</dd></div></dl>
        <p className="cred-section-note">No research code is executed. Community reports describe third-party experience.</p>
        {value.checks.code.searches.map(item => <details className="cred-scope" key={item.scope_id}><summary>{item.scope_type.replaceAll('_', ' ')} <span>{item.outcome.replaceAll('_', ' ')}</span></summary><p>{item.reason}</p>{evidenceLinks(item.evidence_ids)}{sourceRecords(item.source_ids)}</details>)}
        {value.checks.code.repositories.map(item => {
          const repo = repositoryLink(value.sources.find(source => source.source_id === item.source_id)?.url)
          return <article className="cred-reference" key={item.repository_id}><div className="cred-item-label">Repository · {item.association} · {item.availability}</div>{repo && <h3><a href={repo.href} target="_blank" rel="noreferrer">{repo.name}</a></h3>}<p>{item.association_reason}</p>{item.inspected_revision && <dl className="cred-facts"><div><dt>Inspected revision</dt><dd>{item.inspected_revision.slice(0, 12)}</dd></div></dl>}{evidenceLinks([...item.association_evidence_ids, ...item.implementation_evidence_ids])}{sourceRecords([item.source_id])}</article>
        })}
        {value.checks.code.discussions.map(item => <article className="cred-reference" key={item.discussion_id}><div className="cred-item-label">{outcomeNames[item.signal]} · relevance {item.relevance}</div><p>{item.conditions}</p><dl className="cred-facts"><div><dt>Resolution</dt><dd>{item.resolution}</dd></div><div><dt>Independence</dt><dd>{item.independence}</dd></div><div><dt>Counts toward score</dt><dd>{item.scoring_eligible ? 'Yes' : 'No'}</dd></div></dl><p>{item.reason}</p>{evidenceLinks(item.evidence_ids)}{sourceRecords([...item.source_ids, ...item.author_response_source_ids])}</article>)}
        {value.checks.code.scope_limitations.map(line => <p className="cred-limitation" key={line}>{line}</p>)}
      </>}
    </section>
  }

  return <>
        <div className="cred-report-heading"><div><p className="cred-kicker">{live ? 'Live check · provisional' : scenario === 'first_case' ? 'Prepared candidate · AI-assisted confirmation · Not Gold' : 'Synthetic scenario · sample report'}</p><h1 ref={heading} tabIndex={-1}>{assessment?.paper?.title || 'Paper credibility report'}</h1><p>{live
          ? assessment?.paper ? `${assessment.paper.arxiv_id || assessment.paper.paper_id} · ${claimCount} claim${claimCount === 1 ? '' : 's'} checked` : 'This input was not matched to a parsed paper'
          : scenario === 'first_case' ? 'Source example: 2310.01798v1 · first claim only' : 'Illustrative paper and evidence · not a real evaluation'}</p>{live && assessment?.warnings.map(item => <p className="cred-limitation" key={item.code}>{item.message}</p>)}</div><button className="button button-secondary button-sm" type="button" onClick={onNew}>{newLabel}</button></div>
        <section className="cred-overall" aria-label="Overall assessment"><div className="cred-overall-score"><span className="cred-kicker">Overall</span><div><strong>{report.overall.score === null ? '—' : report.overall.score.toFixed(1)}</strong>{report.overall.score !== null && <span>/ 10</span>}</div></div><div className="cred-overall-description"><h2>{report.overall.category ? categoryNames[report.overall.category] || report.overall.category : 'Unable to assess overall'}</h2><p>{report.overall.summary}</p>{report.overall.unassessed_reason && <p className="cred-unassessed">{report.overall.unassessed_reason}</p>}<span className="cred-muted">{report.overall.calculation.assessed_dimension_count} of 3 checks scored · {trial ? 'trial score' : 'provisional'}</span>{fallbackIds.length > 0 && <p className="cred-unassessed">Fallback scores for incomplete checks: {fallbackIds.map(id => dimensionNames[id]).join(', ')}.</p>}</div></section>
        {report.alerts.length > 0 && <div className="cred-alerts" aria-label="Report alerts">{report.alerts.map((item,index) => <button key={`${item.type}-${index}`} type="button" className={`cred-alert is-${item.type}`} onClick={() => setActiveDimension(item.dimension_id)}><span>{item.type === 'assessment_gap' ? 'Coverage gap' : item.type === 'low_dimension_score' ? 'Low check score' : 'Important finding'}</span><p>{item.message}</p><b aria-hidden="true">↗</b></button>)}</div>}
        <div className="cred-dimensions" role="tablist" aria-label="Credibility checks">{report.dimensions.map((item,index) => <button type="button" role="tab" key={item.dimension_id} id={`tab-${item.dimension_id}`} aria-selected={activeDimension === item.dimension_id} aria-controls={`panel-${item.dimension_id}`} tabIndex={activeDimension === item.dimension_id ? 0 : -1} onClick={() => setActiveDimension(item.dimension_id)} onKeyDown={event => {
          if (event.key !== 'ArrowRight' && event.key !== 'ArrowLeft') return
          event.preventDefault(); const next=(index+(event.key === 'ArrowRight' ? 1 : 2))%3
          setActiveDimension(report.dimensions[next].dimension_id)
          event.currentTarget.parentElement?.querySelectorAll<HTMLButtonElement>('[role="tab"]')[next]?.focus()
        }}><span className="cred-dimension-index">0{index+1}</span><span className="cred-dimension-name">{dimensionNames[item.dimension_id]}<small>{item.score_status === 'not_applicable' ? 'Not applicable' : `${item.check_status.replaceAll('_',' ')} · ${item.score_status === 'scored' ? 'Scored' : 'Unscored'}`}</small></span><strong>{item.score === null ? '—' : item.score}<small>{item.score !== null && '/10'}</small></strong></button>)}</div>
        {detailsFor(activeDimension,report)}
        <CredibilityProcess report={report} />
        <details className="cred-report-scope"><summary>Coverage, limitations & report provenance</summary><ul>{report.limitations.map(line => <li key={line}>{line}</li>)}</ul><dl className="cred-facts"><div><dt>Blocking checks</dt><dd>{report.overall.calculation.blocking_dimension_ids.map(id => dimensionNames[id]).join(', ') || 'None'}</dd></div><div><dt>Excluded as not applicable</dt><dd>{report.overall.calculation.excluded_dimension_ids.map(id => dimensionNames[id]).join(', ') || 'None'}</dd></div><div><dt>Contract</dt><dd>{assessment?.schema_version}</dd></div><div><dt>Model calls</dt><dd>{report.versions.model_calls.length}</dd></div></dl><button className="cred-text-button" type="button" onClick={() => { const blob = new Blob([JSON.stringify(assessment,null,2)],{type:'application/json'}); const url=URL.createObjectURL(blob); const anchor=document.createElement('a'); anchor.href=url; anchor.download=`${assessment?.assessment_id}.json`; anchor.click(); setTimeout(() => URL.revokeObjectURL(url),0) }}>{live ? 'Download JSON' : 'Download sample JSON'} ↓</button></details>
        <CredibilityEvidenceDrawer evidence={report.evidence} sources={report.sources} selectedId={selectedEvidence} paperPdfUrl={paperPdfUrl} onSelect={setSelectedEvidence} onClose={() => setSelectedEvidence(null)} />
  </>
}

function ratio(value: number | null) { return value === null ? 'Unknown' : `${Math.round(value*100)}%` }

/** The backend records the GitHub API URL it queried; readers need the repository page. Other hosts stay unlinked. */
function repositoryLink(url: string | undefined) {
  const github = url && /^https:\/\/(?:api\.github\.com\/repos|github\.com)\/([^/?#]+)\/([^/?#]+)/.exec(url)
  return github ? { name: `${github[1]}/${github[2]}`, href: `https://github.com/${github[1]}/${github[2]}` } : null
}
