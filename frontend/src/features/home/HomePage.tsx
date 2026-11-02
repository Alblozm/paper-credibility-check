import { useQueryClient } from '@tanstack/react-query'
import { useNavigate } from '@tanstack/react-router'
import { useState, type DragEvent, type FormEvent } from 'react'
import { flushSync } from 'react-dom'
import { credibilityApi, isMock } from '../../app/api'
import { openTab } from '../../app/jobs'
import { startQuality } from '../../app/quality'
import { savePdf } from '../../app/pdfStore'
import { useModels } from '../../app/queries'
import { CREDIBILITY_RUBRIC_VERSION, CREDIBILITY_SCHEMA_VERSION } from '../../types/credibility'
import { Button } from '../../ui/Button'
import { Segmented } from '../../ui/Controls'
import { Icon, type IconName } from '../../ui/Icon'
import { ModelPicker } from '../../ui/ModelPicker'
import { parseArxivInput } from '../../utils/arxiv'

const checks: [IconName, string, string][] = [
  ['text_quote', 'Claims and evidence', 'Each principal claim is compared with the passages in the paper intended to support it.'],
  ['book_open', 'References', 'Each bibliographic entry is matched against external records; unresolved entries are reported with the reason.'],
  ['code', 'Code and reproduction', 'The paper and the web are searched for official implementations and independent reproduction reports.'],
]

/** Start page (spec 5.1). */
export function HomePage() {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const models = useModels()
  // Set just before leaving, so the form grows into this check's waiting panel (spec 4.3).
  const [morphId, setMorphId] = useState<string | null>(null)
  const [sourceType, setSourceType] = useState<'arxiv' | 'pdf'>('arxiv')
  const [arxivInput, setArxivInput] = useState(isMock ? '2310.01798v1' : '')
  const [file, setFile] = useState<File | null>(null)
  const [over, setOver] = useState(false)
  const [modelChoice, setModelChoice] = useState<string | null>(null)
  const [alsoSummary, setAlsoSummary] = useState(true)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const modelId = isMock ? 'model_prototype_v1' : modelChoice ?? models.data?.defaultId ?? null
  const modelError = !isMock && models.data && !models.data.defaultId ? 'No model is available for live checks; the server has no model key configured.'
    : models.isError ? (models.error instanceof Error ? models.error.message : 'Model options could not be loaded.') : null

  async function submit(event: FormEvent) {
    event.preventDefault()
    setError(null)
    const parsed = parseArxivInput(arxivInput)
    if (sourceType === 'arxiv' && !parsed.ok) { setError('Enter a valid arXiv identifier or arxiv.org URL.'); return }
    if (sourceType === 'pdf' && (!file || !file.name.toLowerCase().endsWith('.pdf'))) { setError('Select a PDF file.'); return }
    if (!modelId) { setError(modelError ?? 'Model options are still loading.'); return }
    setSubmitting(true)
    try {
      const uploadId = sourceType === 'pdf' && credibilityApi.uploadPdf && !isMock ? await credibilityApi.uploadPdf(file!) : 'prototype-local-pdf'
      const created = await credibilityApi.createAssessment({
        schema_version: CREDIBILITY_SCHEMA_VERSION, analysis_mode: 'credibility',
        source: sourceType === 'arxiv' && parsed.ok ? { type: 'arxiv', arxiv_id: parsed.arxivId, upload_id: null } : { type: 'pdf', arxiv_id: null, upload_id: uploadId },
        model_id: modelId, rubric_version: CREDIBILITY_RUBRIC_VERSION,
      }, 'first_case')
      if (sourceType === 'pdf' && file) void savePdf(created.assessment_id, file)
      queryClient.setQueryData(['check', created.assessment_id], created)
      openTab(created.assessment_id)
      // The quality assessment runs beside the check; its failure never blocks the check (the Quality tab offers a retry).
      if (alsoSummary) void startQuality(created.assessment_id, { arxivId: parsed.ok ? parsed.arxivId : null, file, modelId }).catch(() => {})
      flushSync(() => setMorphId(created.assessment_id))
      await navigate({ to: '/check/$id', params: { id: created.assessment_id } })
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'The assessment could not be started.')
      setSubmitting(false)
    }
  }

  const pick = (files: FileList | null) => { const next = files?.[0]; if (next) { setFile(next); setError(null) } }
  const onDrop = (event: DragEvent) => { event.preventDefault(); setOver(false); setSourceType('pdf'); pick(event.dataTransfer.files) }

  // One screen: everything else (history, open assessments) is in the title bar.
  return <main className="mx-auto grid min-h-[calc(100vh-40px)] w-full max-w-[1040px] content-center px-4 py-10 sm:px-6" onDragOver={event => event.preventDefault()} onDrop={onDrop}>
    <section className="grid items-start gap-8 lg:grid-cols-[minmax(0,1fr)_320px]">
      <div className="grid gap-5">
        <div className="grid gap-2">
          <p className="cs-eyebrow">Paper due diligence</p>
          <h1 className="cs-serif text-display font-semibold">Credibility assessment for machine learning papers</h1>
          <p className="max-w-[580px] text-body-l text-muted">Verifies stated claims against the paper’s own evidence, confirms bibliographic references, and searches for code and independent reproduction reports. Each finding is linked to its location in the paper.</p>
        </div>
        <form className="cs-card cs-glass grid gap-4 p-5" onSubmit={submit} style={{ viewTransitionName: morphId ? `check-${morphId}` : undefined }}>
          <div className="flex flex-wrap items-center justify-between gap-3">
            <Segmented label="Paper source" value={sourceType} disabled={submitting} onChange={next => { setSourceType(next); setError(null) }}
              options={[{ value: 'arxiv', label: 'arXiv', icon: 'link' }, { value: 'pdf', label: 'PDF', icon: 'document_pdf' }]} />
            {!isMock && models.data && models.data.models.length + models.data.pending.length > 1 &&
              <ModelPicker value={modelId} disabled={submitting} onChange={setModelChoice} models={[
                ...models.data.models.map(item => ({ id: item.model_id, name: item.display_name, provider: item.provider, description: item.description, available: true })),
                ...models.data.pending.map(item => ({ id: item.model_id, name: item.display_name, provider: item.provider, description: item.description, available: false })),
              ]} />}
          </div>
          {sourceType === 'arxiv'
            ? <label className="grid gap-1.5 text-small text-muted">arXiv identifier or URL
                <input className="cs-input text-body-l" value={arxivInput} disabled={submitting} autoComplete="off" placeholder="2310.01798 or https://arxiv.org/abs/2310.01798"
                  onChange={event => setArxivInput(event.target.value)} /></label>
            : <label className={`cs-drop ${over ? 'is-over' : ''}`} onDragEnter={() => setOver(true)} onDragLeave={() => setOver(false)}>
                <Icon name="document_pdf" />
                <b className="text-body text-ink">{file ? file.name : 'Drop a PDF here or select a file'}</b>
                <span className="text-small">{file ? `${(file.size / 1024 / 1024).toFixed(1)} MB · uploaded and parsed on the server` : 'Maximum 30 MB and 120 pages'}</span>
                <input type="file" accept="application/pdf,.pdf" className="sr-only" disabled={submitting} onChange={event => pick(event.target.files)} />
              </label>}
          <label className="cs-check"><input type="checkbox" checked={alsoSummary} disabled={submitting} onChange={event => setAlsoSummary(event.target.checked)} />
            <span>Include a five-dimension quality assessment<small>Typically completes within a minute and is available while the credibility assessment runs.</small></span></label>
          {(error || modelError) && <p className="text-small text-concern" role="alert">{error ?? modelError}</p>}
          <div className="flex flex-wrap items-center justify-between gap-3 border-t border-hairline pt-3">
            {/* The picker shows the model's name; this line says what the chosen model is like. */}
            <span className="min-w-0 flex-1 text-small text-muted">{isMock ? 'Sample data · no API call' : models.data?.offline ? 'Fixture backend · no model call'
              : models.data?.models.find(item => item.model_id === modelId)?.description || (models.isLoading ? 'Loading models…' : '')}</span>
            <Button type="submit" variant="primary" iconEnd="arrow_right" disabled={submitting || !modelId}>{submitting ? 'Starting…' : 'Start assessment'}</Button>
          </div>
        </form>
      </div>
      <aside className="grid gap-3 pt-2 lg:pt-14" aria-label="Scope of the assessment">
        <p className="cs-eyebrow">Scope of the assessment</p>
        {checks.map(([icon, title, text]) => <div key={title} className="grid grid-cols-[28px_minmax(0,1fr)] gap-x-3">
          <span className="grid size-7 place-items-center rounded-md bg-accent-soft text-accent"><Icon name={icon} /></span>
          <div className="grid gap-0.5"><h3 className="cs-serif text-title-s font-bold">{title}</h3><p className="text-small text-muted">{text}</p></div>
        </div>)}
        <p className="border-t border-hairline pt-3 text-small text-muted">Typical duration: 5–15 minutes. The assessment does not execute code or evaluate suitability for publication; scores are provisional under rubric {CREDIBILITY_RUBRIC_VERSION}.</p>
      </aside>
    </section>
  </main>
}
