import { useParams } from '@tanstack/react-router'
import { useEffect, useRef } from 'react'
import { usePairedQuality } from '../../app/pairing'
import { isRunning, useCheck, useModels, useScoring } from '../../app/queries'
import { Button } from '../../ui/Button'
import { ReportDocument } from './documents'
import type { Reported } from './widgets'

/** Printable report (spec 5.5): opened in its own tab, saved as PDF through the print dialog. */
export function PrintReport() {
  const { id } = useParams({ from: '/check/$id/print' })
  const check = useCheck(id)
  const value = check.data
  if (!value) return <main className="cs-print"><p>{check.isError ? 'This assessment could not be loaded.' : 'Loading the assessment…'}</p></main>
  if (!value.report) return <main className="cs-print"><p>This assessment has no report to export.</p></main>
  return <Printable assessment={value as Reported} />
}

function Printable({ assessment }: { assessment: Reported }) {
  const qualityId = usePairedQuality({ assessment_id: assessment.assessment_id, created_at: assessment.created_at, title: assessment.paper?.title })
  const quality = useScoring(qualityId)
  const models = useModels()
  const modelName = models.data?.models.find(item => item.model_id === assessment.model_id)?.display_name ?? assessment.model_id
  // Print once both parts are settled and the fonts are in, so the PDF is complete and uses the report typefaces.
  const ready = !qualityId || (quality.data != null && !isRunning(quality.data)) || quality.isError
  const printed = useRef(false)
  useEffect(() => {
    if (!ready || printed.current) return
    printed.current = true
    void document.fonts.ready.then(() => window.setTimeout(() => window.print(), 300))
  }, [ready])

  return <main className="cs-print">
    <div className="cs-print-bar">
      <span>Use “Save as PDF” as the destination in the print dialog.</span>
      <Button variant="primary" icon="arrow_download" onClick={() => window.print()}>Print or save as PDF</Button>
    </div>
    <ReportDocument assessment={assessment} quality={quality.data} qualityRequested={Boolean(qualityId)} includeQuality modelName={modelName} />
  </main>
}
