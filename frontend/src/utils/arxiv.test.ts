import { describe, expect, it } from 'vitest'
import { isValidArxiv, normalizeArxiv, parseArxivInput } from './arxiv'

describe('ArXiv input helpers', () => {
  it('normalizes abs and pdf URLs', () => {
    expect(normalizeArxiv('https://arxiv.org/abs/2501.01234')).toBe('2501.01234')
    expect(normalizeArxiv('https://arxiv.org/pdf/2501.01234.pdf')).toBe('2501.01234')
    expect(normalizeArxiv('http://export.arxiv.org/abs/2307.08122v3')).toBe('2307.08122v3')
  })

  it('accepts the forms the backend accepts', () => {
    expect(isValidArxiv('2501.01234v2')).toBe(true)
    expect(isValidArxiv('arXiv:2501.01234')).toBe(true)
    expect(isValidArxiv('  2501.01234  ')).toBe(true)
    expect(isValidArxiv('cs.LG/0701001')).toBe(true)
    // Browsers drop the scheme when you copy a URL; the canonical ID is what we send.
    expect(normalizeArxiv('arxiv.org/abs/1706.03762')).toBe('1706.03762')
    expect(normalizeArxiv('www.arxiv.org/pdf/1706.03762v5.pdf')).toBe('1706.03762v5')
  })

  it('reports why an input was rejected so the form can say something useful', () => {
    expect(parseArxivInput('')).toEqual({ ok: false, reason: 'empty' })
    expect(parseArxivInput('https://openreview.net/forum?id=abc')).toEqual({ ok: false, reason: 'foreign_host' })
    expect(parseArxivInput('not-a-paper')).toEqual({ ok: false, reason: 'malformed' })
    expect(parseArxivInput('2501.012')).toEqual({ ok: false, reason: 'malformed' })
  })

  it('lowercases legacy archive names like the backend does', () => {
    expect(normalizeArxiv('https://arxiv.org/abs/cs.LG/0701001')).toBe('cs.lg/0701001')
  })
})
