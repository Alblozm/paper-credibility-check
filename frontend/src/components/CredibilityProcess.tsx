import { duration } from '../constants/credibility'
import type { CredibilityCheckName, CredibilityReport } from '../types/credibility'

const lanes: { id: CredibilityCheckName; name: string }[] = [
  { id: 'claim_check', name: 'Claims & evidence' },
  { id: 'reference_check', name: 'References' },
  { id: 'code_check', name: 'Code & reproduction' },
]

/** Model-call purposes the backend records, in the words a reader would use. */
const purposeNames: Record<string, string> = {
  claim_extraction: 'Extract the paper’s claims',
  claim_extraction_repair: 'Repair the claim list',
  claim_judgment: 'Compare claims with the paper’s evidence',
  claim_judgment_repair: 'Repair the claim judgments',
  reference_field_parsing: 'Read reference fields',
  reference_source_agent: 'Reference agent: search for sources',
  paper_link_classification: 'Classify links in the paper',
  artifact_release_classification: 'Classify code-release statements',
  external_code_discovery_native_tools: 'Code search agent',
  external_code_prefetch_review: 'Review fetched code sources',
  external_code_native_tools_output_retry: 'Retry the code agent’s answer',
}

const searchProviders: Record<string, string> = {
  openai_web_search: 'GPT web search',
  deepseek_search: 'DeepSeek search',
}

function used(value: number, max: number) {
  return max ? `${value} / ${max}` : String(value)
}

/** What the backend did, read from the finished report: nothing here streams while a check runs. */
export function CredibilityProcess({ report }: { report: CredibilityReport }) {
  const checks = report.execution?.checks
  const longest = Math.max(1, ...lanes.map(lane => checks?.[lane.id]?.elapsed_seconds ?? 0))
  const calls = report.versions.model_calls.reduce<Record<string, number>>((counts, call) => {
    counts[call.purpose] = (counts[call.purpose] ?? 0) + 1
    return counts
  }, {})
  const agentVerified = report.checks.references.items.filter(item => item.reason.startsWith('[agent_verified_source]')).length
  const unresolved = report.reference_verification_summary.unresolved_items.reduce<Record<string, number>>((counts, item) => {
    counts[item.reason_code] = (counts[item.reason_code] ?? 0) + 1
    return counts
  }, {})
  const discovery = report.code_discovery
  const ran = Boolean(checks) || report.versions.model_calls.length > 0 || discovery.status !== 'not_run'

  return <section className="cred-process" aria-labelledby="cred-process-title">
    <div className="cred-process-head">
      <h2 id="cred-process-title">How this check ran</h2>
      <p>Recorded by the backend when the check finished. The three checks run side by side.</p>
    </div>
    {!ran && <p className="cred-empty">This report carries no execution record.</p>}

    {checks && <ol className="cred-lanes" aria-label="Check timeline">
      {lanes.map(lane => {
        const run = checks[lane.id]
        if (!run) return null
        return <li key={lane.id} className={`cred-lane is-${run.status}`}>
          <span className="cred-lane-name">{lane.name}</span>
          <span className="cred-lane-track" aria-hidden="true"><i style={{ width: `${Math.max(2, run.elapsed_seconds / longest * 100)}%` }} /></span>
          <span className="cred-lane-meta">
            {run.status === 'completed' ? 'Completed' : run.status === 'partial' ? 'Partial' : 'Failed'} · {duration(run.elapsed_seconds)}
            {run.reused && ' · reused'}
            {run.error && <small> · {run.error.code}</small>}
          </span>
        </li>
      })}
    </ol>}

    <div className="cred-process-grid">
      {Object.keys(calls).length > 0 && <div className="cred-process-block">
        <h3>Model steps</h3>
        <ul className="cred-steps">
          {Object.entries(calls).map(([purpose, count]) => <li key={purpose}>
            <span>{purposeNames[purpose] ?? purpose.replaceAll('_', ' ')}</span><b>{count}×</b>
          </li>)}
        </ul>
      </div>}

      {report.checks.references.extracted_count > 0 && <div className="cred-process-block">
        <h3>Reference sources</h3>
        <ul className="cred-steps">
          <li><span>Matched to a bibliographic source</span><b>{report.reference_verification_summary.matched_count}</b></li>
          {agentVerified > 0 && <li><span>Of those, found by the reference agent</span><b>{agentVerified}</b></li>}
          {Object.entries(unresolved).map(([code, count]) => <li key={code}>
            <span>Unresolved · {code.replaceAll('_', ' ')}</span><b>{count}</b>
          </li>)}
        </ul>
      </div>}

      {discovery.status !== 'not_run' && <div className="cred-process-block">
        <h3>Code search</h3>
        <p className="cred-muted">{discovery.reason}</p>
        {discovery.queries.length > 0 && <>
          <ul className="cred-steps">
            {discovery.queries.map(query => <li key={query.query_id}>
              <span><small>{searchProviders[query.provider] ?? query.provider}</small> {query.query}</span><b>{query.result_count} results</b>
            </li>)}
          </ul>
          <dl className="cred-facts">
            <div><dt>Searches</dt><dd>{used(discovery.budget.queries_used, discovery.budget.max_queries)}</dd></div>
            <div><dt>Tool calls</dt><dd>{used(discovery.budget.tool_calls_used, discovery.budget.max_tool_calls)}</dd></div>
            <div><dt>Model rounds</dt><dd>{used(discovery.budget.model_rounds_used, discovery.budget.max_model_rounds)}</dd></div>
            <div><dt>Pages read</dt><dd>{used(discovery.budget.source_reads_used, discovery.budget.max_source_reads)}</dd></div>
          </dl>
        </>}
        {discovery.candidates.length > 0 && <details className="cred-scope">
          <summary>{discovery.candidates.length} candidate links · {discovery.candidates.filter(item => item.status === 'verified').length} verified · {discovery.candidates.filter(item => item.status === 'rejected').length} rejected</summary>
          <ul className="cred-candidates">
            {discovery.candidates.map(item => <li key={item.candidate_id}>
              <span className={`cred-candidate is-${item.status}`}>{item.status}</span>
              <span className="cred-candidate-url">{item.title || item.url}</span>
              <small>{item.reason}</small>
            </li>)}
          </ul>
        </details>}
      </div>}
    </div>
  </section>
}
