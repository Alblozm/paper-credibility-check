import { useMemo, useSyncExternalStore } from 'react'

/* Browser-side bookkeeping for checks: which ones are open as title-bar tabs and which quality
   assessment belongs to which check. Uploaded PDFs are kept separately in pdfStore. */

const TABS = 'papertrace-tabs'
const PAIRS = 'papertrace-pairs'
const MAX_TABS = 6
const listeners = new Set<() => void>()

function read(key: string) {
  try { return window.localStorage.getItem(key) } catch { return null }
}
function write(key: string, value: string | null) {
  try {
    if (value == null) window.localStorage.removeItem(key)
    else window.localStorage.setItem(key, value)
  } catch { /* storage blocked */ }
  listeners.forEach(listener => listener())
}

function subscribe(listener: () => void) {
  listeners.add(listener)
  const onStorage = (event: StorageEvent) => { if (event.key === TABS || event.key === PAIRS) listener() }
  window.addEventListener('storage', onStorage)
  return () => { listeners.delete(listener); window.removeEventListener('storage', onStorage) }
}

function parse<T>(raw: string | null, fallback: T): T {
  try { return raw ? JSON.parse(raw) as T : fallback } catch { return fallback }
}

/** Opened checks stay as tabs until closed, so a running one is never left with no way back (spec 3.7). */
export function openTab(id: string) {
  const tabs = parse<string[]>(read(TABS), [])
  if (!tabs.includes(id)) write(TABS, JSON.stringify([...tabs, id].slice(-MAX_TABS)))
}

export function closeTab(id: string) {
  write(TABS, JSON.stringify(parse<string[]>(read(TABS), []).filter(tab => tab !== id)))
}

export function useTabs() {
  const raw = useSyncExternalStore(subscribe, () => read(TABS) ?? '[]', () => '[]')
  return useMemo(() => parse<string[]>(raw, []), [raw])
}

/** Remember the quality assessment that was started beside a check. */
export function pairScoring(checkId: string, scoringId: string) {
  write(PAIRS, JSON.stringify({ ...parse<Record<string, string>>(read(PAIRS), {}), [checkId]: scoringId }))
}

export function scoringFor(checkId: string): string | null {
  return parse<Record<string, string>>(read(PAIRS), {})[checkId] ?? null
}

export function useScoringFor(checkId: string) {
  return useSyncExternalStore(subscribe, () => scoringFor(checkId), () => null)
}

/** Forgets a deleted run: its tab and its remembered pair. */
export function forgetRun(checkId: string) {
  closeTab(checkId)
  const pairs = parse<Record<string, string>>(read(PAIRS), {})
  if (checkId in pairs) { delete pairs[checkId]; write(PAIRS, JSON.stringify(pairs)) }
}

/** Forgets a quality assessment that no longer exists, so its run shows "Not run" instead of waiting for it. */
export function forgetQuality(scoringId: string) {
  const pairs = parse<Record<string, string>>(read(PAIRS), {})
  const kept = Object.fromEntries(Object.entries(pairs).filter(([, score]) => score !== scoringId))
  if (Object.keys(kept).length !== Object.keys(pairs).length) write(PAIRS, JSON.stringify(kept))
}

/** The check a quality assessment was started beside, if this browser started both. */
export function checkForScoring(scoringId: string): string | null {
  return Object.entries(parse<Record<string, string>>(read(PAIRS), {})).find(([, score]) => score === scoringId)?.[0] ?? null
}
