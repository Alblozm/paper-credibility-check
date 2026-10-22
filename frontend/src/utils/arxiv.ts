/**
 * Mirrors `normalize_arxiv_id` in the backend (`app/parsing/arxiv.py`).
 * Keep both sides in sync: the backend rejects anything this accepts by mistake,
 * and the user then loses a round trip for no reason.
 */

const MODERN_ID = /^\d{4}\.\d{4,5}(v[1-9]\d*)?$/i
const LEGACY_ID = /^[a-z][a-z0-9.-]+\/\d{7}(v[1-9]\d*)?$/i
const ARXIV_HOSTS = new Set(['arxiv.org', 'www.arxiv.org', 'export.arxiv.org'])

export type ArxivParseResult =
  | { ok: true; arxivId: string }
  | { ok: false; reason: 'empty' | 'foreign_host' | 'malformed' }

function decode(value: string) {
  try {
    return decodeURIComponent(value)
  } catch {
    return value
  }
}

export function parseArxivInput(value: string): ArxivParseResult {
  let candidate = decode((value ?? '').trim())
  if (!candidate) return { ok: false, reason: 'empty' }

  // Browsers hide the scheme, so "arxiv.org/abs/1706.03762" is a very common
  // paste. The backend requires a scheme, but it only ever receives the
  // canonical ID we resolve here, so accepting this form locally is safe.
  if (!candidate.includes('://') && /^(www\.|export\.)?arxiv\.org\//i.test(candidate)) {
    candidate = `https://${candidate}`
  }

  if (candidate.includes('://')) {
    let url: URL
    try {
      url = new URL(candidate)
    } catch {
      return { ok: false, reason: 'malformed' }
    }
    if (!['http:', 'https:'].includes(url.protocol) || !ARXIV_HOSTS.has(url.hostname.toLowerCase())) {
      return { ok: false, reason: 'foreign_host' }
    }
    candidate = url.pathname.replace(/^\/+/, '')
    if (/^(abs|pdf)\//i.test(candidate)) candidate = candidate.slice(candidate.indexOf('/') + 1)
    if (/\.pdf$/i.test(candidate)) candidate = candidate.slice(0, -4)
  } else {
    candidate = candidate.replace(/^arxiv:\s*/i, '')
  }

  candidate = candidate.trim().replace(/^\/+|\/+$/g, '')
  if (!MODERN_ID.test(candidate) && !LEGACY_ID.test(candidate)) {
    return { ok: false, reason: 'malformed' }
  }
  // arXiv archive names are case-insensitive; the canonical identifier is lowercase.
  return { ok: true, arxivId: candidate.toLowerCase() }
}

export function normalizeArxiv(value: string) {
  const parsed = parseArxivInput(value)
  return parsed.ok ? parsed.arxivId : value.trim()
}

export function isValidArxiv(value: string) {
  return parseArxivInput(value).ok
}
