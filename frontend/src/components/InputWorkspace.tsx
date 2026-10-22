import { useMemo, useRef, useState } from 'react'
import { formatBytes, rubricDimensions } from '../constants/labels'
import type {
  AssessmentOptions,
  CreateAssessmentRequest,
  MockScenario,
} from '../types/assessment'
import { parseArxivInput } from '../utils/arxiv'

interface Props {
  options: AssessmentOptions
  isSubmitting: boolean
  requestError: string | null
  showMockControls?: boolean
  scenario: MockScenario
  onScenarioChange: (scenario: MockScenario) => void
  onSubmit: (request: CreateAssessmentRequest) => void
}

const arxivErrors: Record<string, string> = {
  empty: 'Enter an arXiv ID or paper link.',
  foreign_host: 'Only arxiv.org links are supported. Paste the arXiv ID instead.',
  malformed: 'That does not look like an arXiv ID. Try a form like 2307.08122 or arxiv.org/abs/2307.08122.',
}

const examples = ['2307.08122v3', '2501.01234', 'https://arxiv.org/abs/1706.03762']

const steps = [
  { title: 'Pick a source', note: 'An arXiv ID or link, or a PDF from your machine.' },
  { title: 'Choose rubric and model', note: 'The rubric fixes the five dimensions and their weights.' },
  { title: 'Read with the evidence', note: 'Every judgement links back to a page, section, table or figure.' },
]

export function InputWorkspace({
  options,
  isSubmitting,
  requestError,
  showMockControls = false,
  scenario,
  onScenarioChange,
  onSubmit,
}: Props) {
  const [sourceMode, setSourceMode] = useState<'arxiv' | 'pdf'>('arxiv')
  const [arxiv, setArxiv] = useState('')
  const [file, setFile] = useState<File | null>(null)
  const [isDragging, setIsDragging] = useState(false)
  const [venueId, setVenueId] = useState(options.venues[0]?.venue_id ?? '')
  const defaultModel = options.models.find((item) => item.is_default && item.is_available)
    ?? options.models.find((item) => item.is_available)
    ?? options.models[0]
  const [modelId, setModelId] = useState(defaultModel?.model_id ?? '')
  const [validationError, setValidationError] = useState<string | null>(null)
  const arxivRef = useRef<HTMLInputElement>(null)
  const fileRef = useRef<HTMLInputElement>(null)

  const selectedModel = useMemo(
    () => options.models.find((item) => item.model_id === modelId),
    [modelId, options.models],
  )
  const selectedVenue = useMemo(
    () => options.venues.find((item) => item.venue_id === venueId),
    [venueId, options.venues],
  )
  // Live echo of what will actually be sent, so a pasted URL is visibly understood.
  const parsedArxiv = useMemo(() => parseArxivInput(arxiv), [arxiv])
  const noOptions = !options.venues.length || !options.models.some((item) => item.is_available)

  function changeMode(mode: 'arxiv' | 'pdf') {
    setSourceMode(mode)
    setValidationError(null)
  }

  function acceptFile(next: File | null | undefined) {
    if (!next) return
    if (!/\.pdf$/i.test(next.name) && next.type !== 'application/pdf') {
      setValidationError('Only PDF files can be assessed.')
      return
    }
    if (next.size === 0) {
      setValidationError('That file is empty.')
      return
    }
    setFile(next)
    setValidationError(null)
  }

  function handleSubmit(event: React.FormEvent) {
    event.preventDefault()
    if (sourceMode === 'arxiv') {
      if (!parsedArxiv.ok) {
        setValidationError(arxivErrors[parsedArxiv.reason])
        arxivRef.current?.focus()
        return
      }
    } else if (!file) {
      setValidationError('Choose a PDF file first.')
      fileRef.current?.click()
      return
    }
    if (!venueId || !selectedModel?.is_available) {
      setValidationError('No rubric or analysis model is currently available.')
      return
    }
    setValidationError(null)
    if (sourceMode === 'arxiv' && parsedArxiv.ok) {
      onSubmit({ source_type: 'arxiv', arxiv_id: parsedArxiv.arxivId, venue_id: venueId, model_id: modelId })
    } else if (file) {
      onSubmit({ source_type: 'pdf', file, venue_id: venueId, model_id: modelId })
    }
  }

  function handleShortcut(event: React.KeyboardEvent<HTMLFormElement>) {
    if ((event.metaKey || event.ctrlKey) && event.key === 'Enter') {
      event.currentTarget.requestSubmit()
    }
  }

  const error = validationError ?? requestError
  const canSubmit = !isSubmitting && Boolean(venueId) && Boolean(selectedModel?.is_available)

  return (
    <section className="view" aria-labelledby="compose-title">
      <div className="compose-intro">
        <h1 id="compose-title">Assess a machine learning paper</h1>
        <p>
          Five scored dimensions, a reading recommendation and a claim check — each one anchored
          to the passage, table or figure it came from.
        </p>
        <p className="field-hint">Scored against an ICLR-inspired rubric. Not an official review.</p>
      </div>

      <div className="compose-grid">
        <form className="card compose-card" noValidate onSubmit={handleSubmit} onKeyDown={handleShortcut}>
          <fieldset>
            <legend className="visually-hidden">Paper source</legend>
            <div className="tabs" role="tablist" aria-label="Paper source">
              {([['arxiv', 'arXiv'], ['pdf', 'Upload PDF']] as const).map(([mode, label]) => (
                <button
                  className={`tab ${sourceMode === mode ? 'is-active' : ''}`}
                  key={mode}
                  type="button"
                  role="tab"
                  aria-selected={sourceMode === mode}
                  onClick={() => changeMode(mode)}
                >
                  {label}
                </button>
              ))}
            </div>
          </fieldset>

          {sourceMode === 'arxiv' ? (
            <div className="form-block" role="tabpanel">
              <label className="field-label" htmlFor="arxiv-input">arXiv ID or link</label>
              <input
                ref={arxivRef}
                id="arxiv-input"
                className="text-input"
                name="arxiv"
                type="text"
                autoComplete="off"
                spellCheck={false}
                placeholder="2307.08122v3"
                value={arxiv}
                aria-invalid={Boolean(validationError) && sourceMode === 'arxiv'}
                aria-describedby={error ? 'compose-error' : undefined}
                onChange={(event) => {
                  setArxiv(event.target.value)
                  setValidationError(null)
                }}
              />
              {arxiv.trim() && parsedArxiv.ok ? (
                <p className="resolved">
                  <span aria-hidden="true">✓</span>
                  Will be submitted as <code>{parsedArxiv.arxivId}</code>
                </p>
              ) : (
                <p className="field-hint">Accepts an ID, an <code>abs/</code> link or a <code>pdf/</code> link.</p>
              )}
              <div className="examples">
                <span>Try</span>
                {examples.map((item) => (
                  <button
                    className="example-chip"
                    key={item}
                    type="button"
                    onClick={() => { setArxiv(item); setValidationError(null) }}
                  >
                    {item}
                  </button>
                ))}
              </div>
            </div>
          ) : (
            <div className="form-block" role="tabpanel">
              <span className="field-label" id="pdf-label">PDF file</span>
              <button
                type="button"
                className={`dropzone ${isDragging ? 'is-dragging' : ''} ${file ? 'has-file' : ''}`}
                aria-labelledby="pdf-label"
                onClick={() => fileRef.current?.click()}
                onDragOver={(event) => { event.preventDefault(); setIsDragging(true) }}
                onDragLeave={() => setIsDragging(false)}
                onDrop={(event) => {
                  event.preventDefault()
                  setIsDragging(false)
                  acceptFile(event.dataTransfer.files?.[0])
                }}
              >
                <span className="dropzone-mark" aria-hidden="true">PDF</span>
                <span className="dropzone-text">
                  <strong>{file ? file.name : 'Drop a PDF here, or click to browse'}</strong>
                  <span>{file ? formatBytes(file.size) : 'Text-layer PDFs only — scanned papers are not OCR-ed.'}</span>
                </span>
                {file && (
                  <span
                    className="button button-secondary button-sm dropzone-clear"
                    role="button"
                    tabIndex={0}
                    onClick={(event) => { event.stopPropagation(); setFile(null) }}
                    onKeyDown={(event) => {
                      if (event.key === 'Enter' || event.key === ' ') {
                        event.preventDefault()
                        event.stopPropagation()
                        setFile(null)
                      }
                    }}
                  >
                    Remove
                  </span>
                )}
              </button>
              <input
                ref={fileRef}
                id="pdf-input"
                className="visually-hidden"
                name="pdf"
                type="file"
                accept="application/pdf,.pdf"
                onChange={(event) => acceptFile(event.target.files?.[0])}
              />
            </div>
          )}

          <div className="option-grid">
            <div>
              <span className="field-label" id="venue-label">Rubric</span>
              {options.venues.length > 1 ? (
                <div className="picker-group" role="radiogroup" aria-labelledby="venue-label">
                  {options.venues.map((venue) => (
                    <button
                      key={venue.venue_id}
                      type="button"
                      role="radio"
                      aria-checked={venue.venue_id === venueId}
                      className={`picker ${venue.venue_id === venueId ? 'is-selected' : ''}`}
                      onClick={() => setVenueId(venue.venue_id)}
                    >
                      <span className="picker-radio" aria-hidden="true" />
                      <span className="picker-body">
                        <span className="picker-title">{venue.code} {venue.year}</span>
                        <span className="picker-note">Rubric {venue.rubric_version}</span>
                      </span>
                    </button>
                  ))}
                </div>
              ) : selectedVenue ? (
                <div className="picker is-selected is-locked">
                  <span className="picker-radio" aria-hidden="true" />
                  <span className="picker-body">
                    <span className="picker-title">
                      {selectedVenue.code} {selectedVenue.year}
                      <span className="chip chip-outline">{selectedVenue.track}</span>
                    </span>
                    <span className="picker-note">Rubric {selectedVenue.rubric_id} · v{selectedVenue.rubric_version}</span>
                  </span>
                </div>
              ) : null}
            </div>

            <div>
              <span className="field-label" id="model-label">Analysis model</span>
              <div className="picker-group" role="radiogroup" aria-labelledby="model-label">
                {options.models.map((model) => (
                  <button
                    key={model.model_id}
                    type="button"
                    role="radio"
                    aria-checked={model.model_id === modelId}
                    disabled={!model.is_available}
                    className={`picker ${model.model_id === modelId ? 'is-selected' : ''} ${!model.is_available ? 'is-unavailable' : ''}`}
                    onClick={() => setModelId(model.model_id)}
                  >
                    <span className="picker-radio" aria-hidden="true" />
                    <span className="picker-body">
                      <span className="picker-title">
                        {model.display_name}
                        {model.speed_tier && <span className="chip chip-outline">{model.speed_tier}</span>}
                        {!model.is_available && <span className="chip chip-amber">Unavailable</span>}
                      </span>
                      <span className="picker-meta">{model.provider} · {model.model_version}</span>
                      <span className="picker-note">
                        {model.is_available ? model.description : 'This model is not enabled on the local server.'}
                      </span>
                    </span>
                  </button>
                ))}
              </div>
            </div>
          </div>

          {import.meta.env.DEV && showMockControls && (
            <div className="form-block">
              <label className="field-label" htmlFor="scenario-select">Demo scenario (dev only)</label>
              <select
                id="scenario-select"
                className="text-input"
                value={scenario}
                onChange={(event) => onScenarioChange(event.target.value as MockScenario)}
              >
                <option value="completed">Complete report</option>
                <option value="provisional">Provisional report</option>
                <option value="retryable_error">Retryable failure</option>
                <option value="non_retryable_error">Non-retryable failure</option>
              </select>
            </div>
          )}

          {noOptions && (
            <p className="field-error" role="alert">
              No rubric or analysis model is currently available. Check the backend configuration and reload.
            </p>
          )}
          {error && <p id="compose-error" className="field-error" role="alert">{error}</p>}

          <div className="submit-row">
            <button className="button button-primary button-block" type="submit" disabled={!canSubmit}>
              {isSubmitting ? 'Creating assessment…' : 'Run assessment'}
              {!isSubmitting && <span className="kbd">⌘↵</span>}
            </button>
            <p className="submit-note">Typically two to four minutes for a full-length paper.</p>
          </div>
        </form>

        <aside>
          <div className="card side-block">
            <h3>What gets scored</h3>
            {rubricDimensions.map((item) => (
              <div className="rubric-item" key={item.id}>
                <span>{item.name}</span>
                <b>{Math.round(item.weight * 100)}%</b>
              </div>
            ))}
            <p className="field-hint">Each dimension is scored 1–10. A dimension with no reliable evidence stays unscored rather than defaulting to zero.</p>
          </div>

          <div className="card side-block">
            <h3>How it works</h3>
            <div className="steps">
              {steps.map((step) => (
                <div className="step" key={step.title}>
                  <strong>{step.title}</strong>
                  <span>{step.note}</span>
                </div>
              ))}
            </div>
          </div>

          <div className="card side-block">
            <h3>Scope</h3>
            <p className="field-hint scope-copy">
              English-language machine learning papers with a readable text layer. Papers outside
              that scope are declined rather than scored. This is not a substitute for peer review.
            </p>
          </div>
        </aside>
      </div>
    </section>
  )
}
