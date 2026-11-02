import { useEffect, useState } from 'react'

/* Uploaded PDFs are kept in this browser (IndexedDB), so the reader can show the paper again after a reload or
   from history. The backend has no route to read an upload back, so another browser falls back to excerpts. */

const DB = 'papertrace'
const STORE = 'pdfs'
/** Only the most recent uploads are kept; older ones fall back to excerpts. */
const KEEP = 12

interface Saved { file: Blob; name: string; savedAt: number }

const memory = new Map<string, File>()
/** Readers of a check's PDF, told when a file is saved for it. */
const listeners = new Set<(checkId: string) => void>()

function openDb(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(DB, 1)
    request.onupgradeneeded = () => request.result.createObjectStore(STORE)
    request.onsuccess = () => resolve(request.result)
    request.onerror = () => reject(request.error)
  })
}

/** Runs `work` in one transaction and resolves when it commits. */
async function inStore<T>(mode: IDBTransactionMode, work: (store: IDBObjectStore) => T): Promise<T> {
  const db = await openDb()
  try {
    return await new Promise<T>((resolve, reject) => {
      const tx = db.transaction(STORE, mode)
      const result = work(tx.objectStore(STORE))
      tx.oncomplete = () => resolve(result)
      tx.onerror = tx.onabort = () => reject(tx.error)
    })
  } finally {
    db.close()
  }
}

/** Keeps the file for this check; storage that is blocked or full leaves it for this session only. */
export async function savePdf(checkId: string, file: File) {
  memory.set(checkId, file)
  listeners.forEach(listener => listener(checkId))
  try {
    await inStore('readwrite', store => { store.put({ file, name: file.name, savedAt: Date.now() } satisfies Saved, checkId) })
    await inStore('readwrite', store => {
      const rows: { key: IDBValidKey; at: number }[] = []
      const cursor = store.openCursor()
      cursor.onsuccess = () => {
        const row = cursor.result
        if (row) { rows.push({ key: row.key, at: (row.value as Saved).savedAt }); row.continue(); return }
        rows.sort((a, b) => b.at - a.at).slice(KEEP).forEach(old => store.delete(old.key))
      }
    })
  } catch { /* storage blocked or full */ }
}

/** Drops the kept file of a deleted check. */
export async function deletePdf(checkId: string) {
  memory.delete(checkId)
  try { await inStore('readwrite', store => { store.delete(checkId) }) } catch { /* storage blocked */ }
}

export async function loadPdf(checkId: string): Promise<File | null> {
  const cached = memory.get(checkId)
  if (cached) return cached
  try {
    const request = await inStore('readonly', store => store.get(checkId) as IDBRequest<Saved | undefined>)
    const saved = request.result
    if (!saved) return null
    const file = new File([saved.file], saved.name, { type: 'application/pdf' })
    memory.set(checkId, file)
    return file
  } catch {
    return null
  }
}

/** The uploaded PDF of a check, if this browser still has it. Pass null to skip the lookup (arXiv papers). */
export function useLocalPdf(checkId: string | null) {
  const [found, setFound] = useState<{ id: string; file: File | null } | null>(null)
  useEffect(() => {
    if (!checkId) return
    let live = true
    const load = () => void loadPdf(checkId).then(file => { if (live) setFound({ id: checkId, file }) })
    const onSave = (saved: string) => { if (saved === checkId) load() }
    load()
    listeners.add(onSave)
    return () => { live = false; listeners.delete(onSave) }
  }, [checkId])
  const settled = !checkId || found?.id === checkId
  return { file: settled && checkId ? found?.file ?? null : null, loading: !settled }
}
