import { useState } from 'react'
import { categoryNames, duration } from '../../constants/credibility'
import type { CredibilityAssessment, CredibilityReport } from '../../types/credibility'
import { Button } from '../../ui/Button'
import { Icon, type IconName } from '../../ui/Icon'
import { outcomeNames, reasonName } from '../../ui/names'
import { OutcomePill } from '../../ui/Pill'
import { ScoreGauge } from '../../ui/ScoreGauge'
import { ScoreBead, ScoreKnob, ScoreTube } from '../../ui/SmallScore'
import { bandEdges, pagesOf, skills, type Mark, type ReportModel, type Skill } from './model'

export type Reported = CredibilityAssessment & { report: CredibilityReport }

const bandOf = (score: number) => {
  const edges = [0, ...bandEdges, 10]
  const i = Math.max(0, edges.findLastIndex(edge => score >= edge && edge < 10))
  return [edges[i], edges[i + 1]]
}

function SkillHead({ skill, model }: { skill: Skill; model: ReportModel }) {
  const dimension = model.dimensions[skill.dimension]
  const gap = model.fallback.has(skill.dimension)
  const Score = skill.id === 'claims' ? ScoreKnob : skill.id === 'refs' ? ScoreTube : ScoreBead
  return <div className="cs-stack" style={{ gap: 8 }}>
    <div className="cs-sk-t"><h4>{skill.name}</h4><span className={`cs-tag ${gap ? 'is-gap' : ''}`}>{gap ? 'Fallback score' : dimension.check_status === 'completed' ? 'Complete' : dimension.check_status}</span></div>
    <Score value={dimension.score} gap={gap} />
  </div>
}

export function VerdictBody({ report, model }: { report: CredibilityReport; model: ReportModel }) {
  const { score, category } = report.overall
  const gaps = skills.filter(skill => model.fallback.has(skill.dimension))
  const band = score == null ? null : bandOf(score)
  const counters: [string, number, string][] = [
    ['Concerns identified', model.concerns, 'var(--color-concern)'],
    ['Verification gaps', model.gaps, 'var(--color-slate-500)'],
    ['Fallback scores', model.fallback.size, 'var(--color-slate-300)'],
  ]
  return <div className="cs-stack" style={{ gap: 14 }}>
    <ScoreGauge mode="score" value={score} label={category ? categoryNames[category] : 'Not assessed'}
      sub={score == null ? report.overall.unassessed_reason ?? undefined : 'Trial score'} transitionName="score-gauge" />
    <ul className="grid grid-cols-3 gap-2.5">
      {counters.map(([label, count, colour]) => <li key={label} className="grid border-l-[3px] pl-2" style={{ borderColor: colour }}>
        <span className="text-caption text-muted">{label}</span><b className="cs-serif text-[22px] leading-tight">{count}</b>
      </li>)}
    </ul>
    <details className="cs-disclosure">
      <summary><Icon name="info" /><span>Score derivation</span><Icon name="chevron_down" className="is-chev" /></summary>
      <div className="cs-disclosure-body">
        <p className="cs-eq">({skills.map(skill => model.dimensions[skill.dimension].score?.toFixed(1) ?? '—').join(' + ')}) ÷ 3 = {score?.toFixed(1) ?? '—'}</p>
        <p>The overall score is the unweighted mean of the three checks.</p>
        {gaps.length > 0 && <p>{gaps.map(skill => skill.short).join(' and ')} received fallback scores because those checks did not complete: {report.reference_verification_summary.unverified_count} references could not be verified{report.checks.code.code_availability === 'available' ? '' : ', and no public implementation was located'}.</p>}
        {band && category && <p>Scores from {band[0]} to {band[1]} are classified as “{categoryNames[category]}” under rubric {report.rubric_version} (provisional). {model.concerns ? `${model.concerns} concern${model.concerns > 1 ? 's were' : ' was'} identified in the paper.` : 'No concern was identified in the paper itself.'}</p>}
      </div>
    </details>
  </div>
}

const outcomeIcon: Record<string, IconName> = { supported: 'checkmark_circle', partially_supported: 'circle_half_fill', contradicted: 'warning' }

export function ClaimsBody({ model, selectedClaims, onClaim, onPage }: {
  model: ReportModel; selectedClaims: Set<string>; onClaim: (claimId: string) => void; onPage: (claimId: string, page: number) => void
}) {
  const counts = model.claims.reduce<Record<string, number>>((all, claim) => ({ ...all, [claim.outcome]: (all[claim.outcome] ?? 0) + 1 }), {})
  return <>
    <SkillHead skill={skills[0]} model={model} />
    <p className="cs-lead">{model.claims.length} claims assessed: {Object.entries(counts).map(([key, count]) => `${count} ${outcomeNames[key as keyof typeof outcomeNames].toLowerCase()}`).join(', ')}.</p>
    <div>
      {model.claims.map(claim => <div key={claim.claim_id} className={`cs-clr ${selectedClaims.has(claim.claim_id) ? 'is-on' : ''}`} role="button" tabIndex={0}
        onClick={() => onClaim(claim.claim_id)} onKeyDown={event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); onClaim(claim.claim_id) } }}>
        <span className={`is-oc ${claim.outcome}`}><Icon name={outcomeIcon[claim.outcome] ?? 'question_circle'} /></span>
        <span className="is-ct"><b>{claim.label}</b>{claim.statement}</span>
        <span className="is-cm"><OutcomePill outcome={claim.outcome} />
          {pagesOf(model, [...claim.comparison_evidence_ids, ...claim.claim_evidence_ids]).map(page =>
            <button key={page} type="button" className="cs-pgchip" title={`Go to page ${page}`} onClick={event => { event.stopPropagation(); onPage(claim.claim_id, page) }}>p. {page}</button>)}
        </span>
      </div>)}
    </div>
  </>
}

export function ReferencesBody({ report, model, onReference }: { report: CredibilityReport; model: ReportModel; onReference: (referenceId: string) => void }) {
  const summary = report.reference_verification_summary
  const reasons = summary.unresolved_items.reduce<Record<string, string>>((all, item) => ({ ...all, [item.reason_code]: all[item.reason_code] ?? item.reference_id }), {})
  const counts = summary.unresolved_items.reduce<Record<string, number>>((all, item) => ({ ...all, [item.reason_code]: (all[item.reason_code] ?? 0) + 1 }), {})
  return <>
    <SkillHead skill={skills[1]} model={model} />
    <p className="cs-lead">{summary.matched_count} of {summary.extracted_count} references matched to a bibliographic record; {summary.unverified_count} could not be verified. An unverified reference does not indicate an error.</p>
    {Object.keys(counts).length > 0 && <div className="flex flex-wrap gap-1.5">
      {Object.entries(counts).sort((a, b) => b[1] - a[1]).map(([code, count]) =>
        <button key={code} type="button" className="cs-chip" onClick={() => onReference(reasons[code])}>{reasonName(code)} <b>{count}</b></button>)}
    </div>}
    {summary.unresolved_items.length > 0 && <details className="cs-disclosure cs-unv">
      <summary><Icon name="book_open" /><span>Unverified references ({summary.unresolved_items.length})</span><Icon name="chevron_down" className="is-chev" /></summary>
      <ul className="mt-1.5">{summary.unresolved_items.map(item => {
        const ref = model.references.get(item.reference_id)
        return <li key={item.reference_id} role="button" tabIndex={0} onClick={() => onReference(item.reference_id)}
          onKeyDown={event => { if (event.key === 'Enter') onReference(item.reference_id) }}>
          <b>{ref?.parsed.title || ref?.raw_text.slice(0, 100)}</b>
          <span>{(ref?.parsed.authors ?? []).slice(0, 2).join(', ')}{ref?.parsed.year ? ` · ${ref.parsed.year}` : ''} · {reasonName(item.reason_code)}</span>
        </li>
      })}</ul>
    </details>}
  </>
}

const host = (url: string) => { try { const parsed = new URL(url); return parsed.hostname.replace(/^www\./, '') + parsed.pathname.replace(/\/$/, '') } catch { return url } }

export function CodeBody({ report, model }: { report: CredibilityReport; model: ReportModel }) {
  const code = report.checks.code, discovery = report.code_discovery
  const unreached = code.searches.filter(search => search.outcome === 'not_checked' || search.outcome === 'failed').length
  const facts: [IconName, string | number, string][] = [
    ['search', discovery.queries.length, 'web searches'],
    ['link', discovery.candidates.length, `candidate links · ${discovery.candidates.filter(item => item.status === 'verified').length} verified`],
    ['branch', code.repositories.length, 'repositories'],
    ['people', code.reproduction_signal.replaceAll('_', ' '), 'public feedback'],
  ]
  return <>
    <SkillHead skill={skills[2]} model={model} />
    <p className="cs-lead">{code.code_availability === 'available' ? 'A public implementation was located.' : 'No public implementation was located.'} The paper body and appendix were examined{unreached ? `; ${unreached} further source${unreached > 1 ? 's' : ''} could not be accessed` : ''}.</p>
    <div className="cs-facts">{facts.map(([icon, value, label]) => <div key={label} className="cs-fact"><Icon name={icon} /><b>{value}</b><span>{label}</span></div>)}</div>
    {discovery.candidates.length > 0 && <div>
      <p className="cs-eyebrow mb-0.5">Candidate links</p>
      <ul className="cs-cands">{discovery.candidates.slice(0, 4).map(item => <li key={item.candidate_id}>
        <a href={item.url} target="_blank" rel="noreferrer"><Icon name="link" /><span>{host(item.url)}</span><em className={`cs-pill ${item.status === 'verified' ? 'matched' : 'gap'}`}>{item.status}</em></a>
      </li>)}</ul>
    </div>}
  </>
}

export function EvidenceBody({ list, selected, reviewed, onSelect, onCopy, onReview }: {
  list: Mark[]; selected: string | null; reviewed: ReadonlySet<string>
  onSelect: (markId: string) => void; onCopy: (mark: Mark) => void; onReview: (markId: string) => void
}) {
  if (!list.length) return <p className="cs-lead">Enable a highlight layer to review its evidence.</p>
  const index = Math.max(0, list.findIndex(mark => mark.id === selected))
  const mark = list[index]
  const done = list.filter(item => reviewed.has(item.id)).length
  const isClaim = mark.skill === 'claims'
  const step = (delta: number) => onSelect(list[(index + delta + list.length) % list.length].id)
  return <>
    <div className="flex items-center justify-between gap-2 text-[11.5px] text-muted">
      <span className={`cs-ev-kind ${mark.kind === 'support' ? '' : 'is-claim'}`}>
        <Icon name={isClaim ? (mark.kind === 'support' ? 'checkmark_circle' : 'text_quote') : 'book_open'} />
        {isClaim ? `${mark.claims.map(claim => claim.label).join(', ')} · ${mark.kind === 'support' ? 'Supporting passage' : 'Claim as stated'}` : 'Reference · not verified'}
      </span>
      <span>p. {mark.page}</span>
    </div>
    <blockquote className={`cs-evq ${mark.kind === 'support' ? '' : 'is-claim'}`}>{mark.evidence.excerpt}</blockquote>
    {mark.evidence.paper_locator?.section_title && <p className="cs-lead -mt-1">{mark.evidence.paper_locator.section_title}</p>}
    <div className="flex flex-wrap gap-1.5">
      <Button size="sm" icon="copy" onClick={() => onCopy(mark)}>Copy quotation</Button>
      <Button size="sm" icon="checkmark" on={reviewed.has(mark.id)} onClick={() => onReview(mark.id)}>{reviewed.has(mark.id) ? 'Reviewed' : 'Mark reviewed'}</Button>
    </div>
    <div className="flex items-center justify-between gap-2 text-small text-muted">
      <Button size="sm" icon="arrow_left" onClick={() => step(-1)}>Previous</Button>
      <span className="tabular-nums">{index + 1} / {list.length}</span>
      <Button size="sm" iconEnd="arrow_right" onClick={() => step(1)}>Next</Button>
    </div>
    <div className="flex items-center gap-2.5 text-[11.5px] text-muted"><span className="cs-minibar"><i style={{ width: `${done / list.length * 100}%` }} /></span><span>{done} of {list.length} reviewed</span></div>
  </>
}

/** The five-dimension reading summary that ran beside the check (secondary to credibility). */
const laneMeta: Record<string, [string, IconName]> = { claim_check: ['Claims', 'text_quote'], reference_check: ['References', 'book_open'], code_check: ['Code', 'code'] }

export function ProcessBody({ report }: { report: CredibilityReport }) {
  const checks = Object.entries(report.execution.checks ?? {})
  const longest = Math.max(1, ...checks.map(([, check]) => check?.elapsed_seconds ?? 0))
  return <>
    <div className="grid gap-2.5">
      {checks.map(([name, check]) => check && <div key={name} className="cs-lane">
        <Icon name={laneMeta[name]?.[1] ?? 'timeline'} /><span>{laneMeta[name]?.[0] ?? name}</span>
        <span className={`is-track cs-groove ${check.status === 'completed' ? '' : 'is-gap'}`}><i className="cs-lit" style={{ width: `${Math.max(4, check.elapsed_seconds / longest * 100)}%` }} /></span>
        <small>{check.stopped_by_user ? 'Stopped by the user' : check.status === 'completed' ? 'Complete' : check.status === 'partial' ? 'Partial coverage' : 'Not completed'}{check.reused ? ' · reused' : ''} · {duration(check.elapsed_seconds)}</small>
      </div>)}
    </div>
    <p className="cs-lead">{report.execution.workers ?? 3} checks run in parallel · {report.versions.model_calls.length} model calls.</p>
  </>
}

export function NotesBody({ id }: { id: string }) {
  const key = `papertrace-notes-${id}`
  const [text, setText] = useState(() => { try { return localStorage.getItem(key) ?? '' } catch { return '' } })
  return <textarea className="cs-input min-h-[96px] py-2" placeholder="Notes (stored in this browser only)" value={text}
    onChange={event => { setText(event.target.value); try { localStorage.setItem(key, event.target.value) } catch { /* storage blocked */ } }} />
}
