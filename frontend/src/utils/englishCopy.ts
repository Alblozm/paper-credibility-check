import { errorTitles } from '../constants/labels'
import type { Assessment } from '../types/assessment'

export const containsChinese = (text: string) => /\p{Script=Han}/u.test(text)

/** Older saved assessments contain service-authored Chinese. Translate those notes without
 * changing the assessment, its evidence quotations, source metadata or numerical results. */
export function legacyServiceCopy(text: string): string {
  return text
    .replace(/\[自动核对：解析文本中未找到对应信号，状态已降为 unable_to_verify\]/g,
      '[Automatic check: no matching signal in the parsed text; status lowered to unable_to_verify]')
    .replace(/'([^']+)' 实际在第 (\d+) 页，模型写的 (\d+) 已按解析结果更正/g,
      "'$1' is on page $2; the model's page $3 was corrected from the parsed document")
    .replace(/'([^']+)' 被模型标为 available，但解析文本中没有对应信号，已降为 unable_to_verify/g,
      "'$1' was marked available by the model, but the parsed text has no matching signal; lowered to unable_to_verify")
    .replace(/evidence_id '([^']+)' 的 quote 在解析文本中找不到，location_confidence 已降为 low/g,
      "The quote of evidence_id '$1' was not found in the parsed text; location_confidence was lowered to low")
}

const serviceMessages: Record<string, string> = {
  INVALID_PDF: 'The PDF is damaged or could not be read. Upload a valid PDF and try again.',
  UNABLE_TO_ASSESS: 'The document could not be confirmed as a machine-learning research paper.',
  MODEL_UNAVAILABLE: 'The analysis service is temporarily unavailable. Please try again later.',
  MODEL_OUTPUT_INVALID: 'The model output did not meet the report structure requirements. Please try again.',
  PAGE_NUMBER_CORRECTED: 'An evidence page number was corrected using the parsed document.',
  EVIDENCE_LOCATOR_PAGE_FIXED: 'An evidence page number was corrected using the parsed document.',
  CHECKLIST_DOWNGRADED: 'A reproducibility item could not be verified in the parsed text; its status was lowered to unable_to_verify.',
  EVIDENCE_EXCERPT_UNVERIFIED: 'An evidence quotation could not be found in the parsed text; its location confidence was lowered to low.',
  EVIDENCE_EXCERPT_NOT_FOUND: 'An evidence quotation could not be found in the parsed text; its location confidence was lowered to low.',
}

/** Preserve useful English server details; never expose untranslated service errors or warnings. */
export function englishServiceMessage(message: string, code?: string, fallback = 'The service could not complete this request. Please try again.'): string {
  const translated = legacyServiceCopy(message)
  if (!containsChinese(translated)) return translated
  return (code && (serviceMessages[code] ?? errorTitles[code])) || fallback
}

type ServiceMessages = {
  warnings: Array<{ code: string; message: string }>
  error: { code: string; message: string } | null
}

export function withEnglishMessages<T extends ServiceMessages>(value: T): T {
  return {
    ...value,
    warnings: value.warnings.map(item => ({
      ...item,
      message: englishServiceMessage(item.message, item.code, 'An additional parsing note was returned without an English description.'),
    })),
    error: value.error ? { ...value.error, message: englishServiceMessage(value.error.message, value.error.code) } : null,
  }
}

export function withEnglishAssessmentCopy(value: Assessment): Assessment {
  const translated = withEnglishMessages(value)
  return {
    ...translated,
    report: translated.report ? {
      ...translated.report,
      reproducibility_checklist: translated.report.reproducibility_checklist.map(item => ({ ...item, detail: legacyServiceCopy(item.detail) })),
    } : null,
  }
}
