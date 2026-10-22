import { errorTitles, outOfScopeCodes, stageDefinitions } from '../constants/labels'
import type { Assessment } from '../types/assessment'

interface Props {
  assessment: Assessment
  isRetrying: boolean
  requestError: string | null
  onRetry: () => void
  onBack: () => void
}

export function AssessmentErrorView({ assessment, isRetrying, requestError, onRetry, onBack }: Props) {
  const error = assessment.error
  if (!error) return null

  const stage = stageDefinitions.find((item) => item.stage === error.stage)?.label ?? error.stage ?? '—'
  // The eligibility gate declining a paper is a scope decision, not a fault;
  // showing it in failure red makes people think the product broke.
  const outOfScope = outOfScopeCodes.has(error.code)
  const title = errorTitles[error.code] ?? 'The assessment could not be completed'

  return (
    <section className={`view error-view ${outOfScope ? 'is-scope' : ''}`} aria-labelledby="error-title">
      <div className="card error-card">
        <div className="error-mark" aria-hidden="true">{outOfScope ? 'i' : '!'}</div>
        <h1 id="error-title">{title}</h1>
        <p className="error-message">{error.message}</p>

        {assessment.paper?.title && (
          <div className="error-paper">
            <span>Paper that was read</span>
            {assessment.paper.title}
          </div>
        )}

        {requestError && <p className="field-error" role="alert">{requestError}</p>}

        <dl className="error-meta">
          <div>
            <dt>Failed at</dt>
            <dd>{stage}</dd>
          </div>
          <div>
            <dt>Code</dt>
            <dd><code>{error.code}</code></dd>
          </div>
          {error.debug_id && (
            <div>
              <dt>Debug ID</dt>
              <dd><code>{error.debug_id}</code></dd>
            </div>
          )}
          <div>
            <dt>Retry</dt>
            <dd>{error.retryable ? 'Can be retried as-is' : 'Needs a different input'}</dd>
          </div>
        </dl>

        <div className="error-actions">
          {error.retryable && (
            <button className="button button-primary" type="button" disabled={isRetrying} onClick={onRetry}>
              {isRetrying ? 'Retrying…' : 'Try again'}
            </button>
          )}
          <button className="button button-secondary" type="button" onClick={onBack}>
            {error.retryable ? 'Change the input' : 'Submit another paper'}
          </button>
        </div>
      </div>
    </section>
  )
}
