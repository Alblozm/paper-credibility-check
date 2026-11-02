import { renderToStaticMarkup } from 'react-dom/server'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { httpAssessmentApi } from '../api/httpAssessmentApi'
import { ReportDocument } from '../features/report/documents'
import type { Reported } from '../features/report/widgets'
import { QualityReport, type Scored } from '../features/score/QualityReport'
import credibility from '../test/fixtures/credibility-paper-002.json'
import scoring from '../test/fixtures/scoring-paper-002.json'
import type { Assessment } from '../types/assessment'
import { containsChinese, englishServiceMessage, withEnglishAssessmentCopy } from './englishCopy'

afterEach(() => vi.unstubAllGlobals())

function legacyAssessment() {
  const value = structuredClone(scoring) as unknown as Assessment
  value.warnings = [
    { code: 'PAGE_NUMBER_CORRECTED', message: "'Table 4' 实际在第 13 页，模型写的 14 已按解析结果更正", stage: 'linking_evidence' },
    { code: 'CHECKLIST_DOWNGRADED', message: "'preprocessing_details' 被模型标为 available，但解析文本中没有对应信号，已降为 unable_to_verify", stage: 'linking_evidence' },
    { code: 'EVIDENCE_EXCERPT_UNVERIFIED', message: "evidence_id 'ev_10' 的 quote 在解析文本中找不到，location_confidence 已降为 low", stage: 'linking_evidence' },
  ]
  value.report!.reproducibility_checklist[0].detail = 'The preprocessing protocol is described. [自动核对：解析文本中未找到对应信号，状态已降为 unable_to_verify]'
  return value
}

describe('English service copy', () => {
  it('loads old reports in English across the quality page and printable combined report', async () => {
    const raw = legacyAssessment()
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify(raw), { status: 200 })))
    const value = await httpAssessmentApi.getAssessment(raw.assessment_id)
    expect(value.warnings.map(item => item.message)).toEqual([
      "'Table 4' is on page 13; the model's page 14 was corrected from the parsed document",
      "'preprocessing_details' was marked available by the model, but the parsed text has no matching signal; lowered to unable_to_verify",
      "The quote of evidence_id 'ev_10' was not found in the parsed text; location_confidence was lowered to low",
    ])
    const page = renderToStaticMarkup(<QualityReport assessment={value as Scored} />)
    const printed = renderToStaticMarkup(<ReportDocument assessment={credibility as unknown as Reported} quality={value} includeQuality modelName="GPT Review Model" />)
    for (const html of [page, printed]) {
      expect(containsChinese(html)).toBe(false)
      expect(html).toContain('Automatic check: no matching signal in the parsed text; status lowered to unable_to_verify')
    }
    expect(value.report!.overall_assessment).toEqual(raw.report!.overall_assessment)
    expect(value.report!.evidence).toEqual(raw.report!.evidence)
    expect(value.report!.reproducibility_checklist[0].status).toBe(raw.report!.reproducibility_checklist[0].status)
    expect(containsChinese(raw.warnings[0].message)).toBe(true)
  })

  it.each([
    ['UNABLE_TO_ASSESS', '无法得出评估结果：该文档无法确认为机器学习领域论文。', 'The document could not be confirmed as a machine-learning research paper.'],
    ['MODEL_UNAVAILABLE', '分析服务暂时不可用，请稍后重试。', 'The analysis service is temporarily unavailable. Please try again later.'],
    ['MODEL_OUTPUT_INVALID', '模型输出无法满足报告结构要求，请重试。', 'The model output did not meet the report structure requirements. Please try again.'],
  ])('translates stored %s failures without changing retry or stage information', (code, message, expected) => {
    const raw = legacyAssessment()
    raw.status = 'failed'
    raw.report = null
    raw.error = { code, message, retryable: false, stage: 'analyzing', debug_id: 'original-id' }
    const value = withEnglishAssessmentCopy(raw)
    expect(value.error).toEqual({ ...raw.error, message: expected })
    expect(raw.error.message).toBe(message)
  })

  it('keeps source quotations and names intact while translating service-authored notes', () => {
    const raw = legacyAssessment()
    raw.paper!.authors = ['牛 奕童']
    raw.report!.evidence[0].excerpt = 'Original source: 牛 奕童'
    const value = withEnglishAssessmentCopy(raw)
    expect(value.paper).toEqual(raw.paper)
    expect(value.report!.evidence).toEqual(raw.report!.evidence)
  })

  it('retains specific English errors and gives an English fallback for unknown service messages', () => {
    expect(englishServiceMessage('The upload exceeds the 20 MB limit.', 'FILE_TOO_LARGE')).toBe('The upload exceeds the 20 MB limit.')
    expect(englishServiceMessage('未知中文错误', 'UNKNOWN_ERROR')).toBe('The service could not complete this request. Please try again.')
  })
})
