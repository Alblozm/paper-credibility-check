import { scoringApi } from './api'
import { pairScoring } from './jobs'
import { loadPdf } from './pdfStore'

/**
 * Starts a quality assessment for the same paper as a check and pairs the two. Used when the check starts, and to run
 * or retry it later. An uploaded paper needs this browser's copy of the PDF.
 */
export async function startQuality(checkId: string, input: { arxivId: string | null; file?: File | null; modelId: string | null }) {
  const options = await scoringApi.getOptions()
  const venue = options.venues[0]
  const available = options.models.filter(item => item.is_available)
  const model = available.find(item => item.model_id === input.modelId) ?? available.find(item => item.is_default) ?? available[0]
  if (!venue || !model) throw new Error('No rubric or model is available for the quality assessment.')
  const file = input.arxivId ? null : input.file ?? await loadPdf(checkId)
  if (!input.arxivId && !file) throw new Error('This browser has no copy of the uploaded PDF. Select it in the Paper view first.')
  const link = { credibility_assessment_id: checkId }
  const receipt = await scoringApi.createAssessment(input.arxivId
    ? { source_type: 'arxiv', arxiv_id: input.arxivId, venue_id: venue.venue_id, model_id: model.model_id, ...link }
    : { source_type: 'pdf', file: file!, venue_id: venue.venue_id, model_id: model.model_id, ...link })
  pairScoring(checkId, receipt.assessment_id)
  return receipt.assessment_id
}
