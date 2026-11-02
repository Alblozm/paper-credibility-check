import { useQueryClient } from '@tanstack/react-query'
import { useRouterState } from '@tanstack/react-router'
import { useEffect, useRef, useState, type KeyboardEvent, type ReactNode } from 'react'
import { categoryNames } from '../../constants/credibility'
import { labels } from '../../constants/labels'
import type { Assessment } from '../../types/assessment'
import type { CredibilityAssessment } from '../../types/credibility'
import { Icon, type IconName } from '../../ui/Icon'
import { skills } from '../report/model'
import { containsChinese, englishServiceMessage } from '../../utils/englishCopy'

interface Message {
  role: 'user' | 'assistant'
  content: string
  /** Version of the guide that answered, sent back with a rating. */
  guide?: string
  rating?: 'up' | 'down'
}

const STORE = 'papertrace-assistant'
type Page = 'home' | 'waiting' | 'report' | 'quality' | 'history'
interface Prompt { icon: IconName; text: string }

/** Starter questions: first what fits the page the reader is on, then the general ones (Ant Design X "Prompts"). */
const pagePrompts: Record<Page, Prompt[]> = {
  home: [{ icon: 'flash', text: 'Which model should I choose?' }, { icon: 'document_pdf', text: 'Can I assess an uploaded PDF?' }],
  waiting: [{ icon: 'clock', text: 'How long does an assessment take?' }, { icon: 'stop', text: 'Can I stop just one check?' }],
  report: [{ icon: 'gauge', text: 'How is the trial score calculated?' }, { icon: 'warning', text: 'Why do some checks have a fallback score?' }],
  quality: [{ icon: 'note', text: 'How does the quality assessment differ from credibility?' }, { icon: 'gauge', text: 'What do the rating bands mean?' }],
  history: [{ icon: 'delete', text: 'How do I delete a run?' }, { icon: 'history', text: 'Why are two runs of one paper listed separately?' }],
}
const generalPrompts: Prompt[] = [
  { icon: 'add', text: 'How do I start an assessment?' }, { icon: 'arrow_download', text: 'How do I save the report as a PDF?' },
]

/** The page the reader is on, in a few lines, so answers can refer to what is on screen. */
function usePageContext() {
  const pathname = useRouterState({ select: state => state.location.pathname })
  const part = useRouterState({ select: state => (state.location.search as { part?: string }).part })
  const queryClient = useQueryClient()
  const check = pathname.match(/^\/check\/([^/]+)$/)?.[1]
  const running = check ? ['queued', 'processing'].includes(queryClient.getQueryData<CredibilityAssessment>(['check', check])?.status ?? '') : false
  const page: Page = check ? (part === 'quality' ? 'quality' : running ? 'waiting' : 'report')
    : pathname.startsWith('/score/') ? 'quality' : pathname === '/reports' ? 'history' : 'home'
  const describe = () => {
    const check = pathname.match(/^\/check\/([^/]+)/)?.[1]
    const score = pathname.match(/^\/score\/([^/]+)/)?.[1]
    if (check) {
      const value = queryClient.getQueryData<CredibilityAssessment>(['check', check])
      if (!value) return 'The user is opening a credibility assessment.'
      const title = value.paper?.title ?? value.source.arxiv_id ?? 'an uploaded PDF'
      if (value.status === 'failed') return `Credibility assessment of "${title}" failed: ${value.error?.code} — ${value.error?.message}`
      if (!value.report) return `Credibility assessment of "${title}" is running (stage ${value.stage}, ${value.progress_percent}%).`
      const { overall, findings } = value.report
      const fallback = (overall.calculation.fallback_dimension_ids ?? [])
        .map(id => skills.find(skill => skill.dimension === id)?.name ?? id)
      return [
        `The user is reading the credibility report of "${title}".`,
        `Trial score ${overall.score?.toFixed(1) ?? 'none'}${overall.category ? ` (${categoryNames[overall.category]})` : ''}.`,
        `Concerns identified: ${findings.filter(item => item.kind === 'concern').length}; verification gaps: ${findings.filter(item => item.kind === 'verification_gap').length}.`,
        `Checks with a fallback score: ${fallback.length ? fallback.join(', ') : 'none'}.`,
      ].join('\n')
    }
    if (score) {
      const value = queryClient.getQueryData<Assessment>(['score', score])
      const overall = value?.report?.overall_assessment
      return overall
        ? `The user is reading a five-dimension quality assessment: ${overall.overall_score ?? 'unscored'} / 10, ${labels.rating[overall.rating as keyof typeof labels.rating] ?? overall.rating}.`
        : 'The user is opening a five-dimension quality assessment.'
    }
    return pathname === '/reports' ? 'The user is on the assessment history page.' : 'The user is on the start page.'
  }
  return { page, describe }
}

const shared: Record<Page, string> = {
  home: 'the start page', waiting: 'the progress of this assessment', report: "this report's score and counts",
  quality: 'this quality assessment', history: 'the history page',
}

/** Short paragraphs, lists, **bold** and `code`: enough for help answers, rendered without HTML injection. */
function Rich({ text }: { text: string }) {
  const inline = (line: string) => line.split(/(\*\*[^*]+\*\*|`[^`]+`)/g).map((part, index) =>
    part.startsWith('**') && part.endsWith('**') && part.length > 4 ? <b key={index}>{part.slice(2, -2)}</b>
      : part.startsWith('`') && part.endsWith('`') && part.length > 2 ? <code key={index}>{part.slice(1, -1)}</code> : part)
  const blocks: ReactNode[] = []
  let list: { ordered: boolean; items: string[] } | null = null
  const flush = () => {
    if (!list) return
    const items = list.items.map((item, index) => <li key={index}>{inline(item)}</li>)
    blocks.push(list.ordered ? <ol key={blocks.length}>{items}</ol> : <ul key={blocks.length}>{items}</ul>)
    list = null
  }
  for (const raw of text.split('\n')) {
    const line = raw.trimEnd()
    const bullet = line.match(/^\s*[-*•]\s+(.*)$/), numbered = line.match(/^\s*\d+[.)]\s+(.*)$/)
    if (bullet || numbered) {
      const ordered = Boolean(numbered)
      if (!list || list.ordered !== ordered) { flush(); list = { ordered, items: [] } }
      list.items.push((bullet ?? numbered)![1])
      continue
    }
    flush()
    if (line.trim()) blocks.push(<p key={blocks.length}>{inline(line.replace(/^#+\s*/, ''))}</p>)
  }
  flush()
  return <>{blocks}</>
}

function load(): Message[] {
  try { return JSON.parse(sessionStorage.getItem(STORE) ?? '[]') as Message[] } catch { return [] }
}

/** Help assistant (spec 3.11): a floating chat that answers questions about using PaperTrace from the user guide. */
export function Assistant() {
  const [open, setOpen] = useState(false)
  const [messages, setMessages] = useState<Message[]>(load)
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const abort = useRef<AbortController | null>(null)
  const list = useRef<HTMLDivElement>(null)
  const field = useRef<HTMLTextAreaElement>(null)
  const { page, describe } = usePageContext()
  const [copied, setCopied] = useState<number | null>(null)
  const onReport = useRouterState({ select: state => /^\/check\/[^/]+$/.test(state.location.pathname) })

  useEffect(() => { try { sessionStorage.setItem(STORE, JSON.stringify(messages)) } catch { /* storage blocked */ } }, [messages])
  useEffect(() => { list.current?.scrollTo({ top: list.current.scrollHeight }) }, [messages, open])
  useEffect(() => {
    if (!open) return
    field.current?.focus()
    const onKey = (event: globalThis.KeyboardEvent) => { if (event.key === 'Escape') setOpen(false) }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open])

  async function ask(question: string) {
    const text = question.trim()
    if (!text || busy) return
    const history: Message[] = [...messages, { role: 'user', content: text }]
    setMessages([...history, { role: 'assistant', content: '' }])
    setInput(''); setError(null); setBusy(true)
    const controller = new AbortController()
    abort.current = controller
    const append = (delta: string) => setMessages(value => {
      const next = [...value]
      next[next.length - 1] = { role: 'assistant', content: next[next.length - 1].content + delta }
      return next
    })
    try {
      const response = await fetch('/assistant/chat', {
        method: 'POST', signal: controller.signal, headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ messages: history.map(({ role, content }) => ({ role, content })), context: describe() }),
      })
      if (!response.ok || !response.body) {
        const body = await response.json().catch(() => null) as { error?: string } | null
        throw new Error(englishServiceMessage(body?.error ?? 'The assistant is unavailable right now.', undefined, 'The assistant is unavailable right now.'))
      }
      // Anything but an event stream means the request never reached the assistant service (for example, it is not running).
      if (!response.headers.get('content-type')?.includes('text/event-stream')) throw new Error('The assistant service is not running on this server.')
      const reader = response.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''
      for (;;) {
        const { value, done } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })
        let end
        while ((end = buffer.indexOf('\n\n')) >= 0) {
          const line = buffer.slice(0, end).replace(/^data:\s*/, '')
          buffer = buffer.slice(end + 2)
          const data = JSON.parse(line) as { delta?: string; error?: string; guide?: string }
          if (data.delta) {
            if (containsChinese(data.delta)) throw new Error('The assistant could not provide an English answer. Please try again.')
            append(data.delta)
          }
          if (data.guide) setMessages(value => value.map((message, index) => (index === value.length - 1 ? { ...message, guide: data.guide } : message)))
          if (data.error) throw new Error(englishServiceMessage(data.error, undefined, 'The answer was interrupted. Please try again.'))
        }
      }
    } catch (cause) {
      if (!controller.signal.aborted) setError(englishServiceMessage(cause instanceof Error && cause.message !== 'Failed to fetch' ? cause.message : 'The assistant is unavailable right now.', undefined, 'The assistant is unavailable right now.'))
    } finally {
      // An answer that never started is dropped, so the question can simply be asked again.
      setMessages(value => (value.at(-1)?.role === 'assistant' && !value.at(-1)!.content ? value.slice(0, -1) : value))
      setBusy(false)
      abort.current = null
    }
  }

  const onKey = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); void ask(input) }
  }
  async function copy(index: number) {
    try { await navigator.clipboard.writeText(messages[index].content); setCopied(index); window.setTimeout(() => setCopied(null), 1500) }
    catch { /* clipboard blocked */ }
  }
  // A rating goes to the assistant service's feedback log, with the question it answered (see assistant/README.md).
  function rate(index: number, rating: 'up' | 'down') {
    const message = messages[index]
    if (message.rating === rating) return
    setMessages(value => value.map((item, at) => (at === index ? { ...item, rating } : item)))
    void fetch('/assistant/feedback', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ rating, guide: message.guide, page, question: messages[index - 1]?.content, answer: message.content }),
    }).catch(() => { /* a lost rating is not worth interrupting the reader */ })
  }

  return <>
    {open && <section className={`cs-ai cs-glass ${onReport ? 'is-report' : ''}`} role="dialog" aria-label="Help assistant">
      <header>
        <span className="cs-ai-mark"><Icon name="sparkle" /></span>
        <div className="grid"><b>Assistant</b><small>Answers from the PaperTrace guide</small></div>
        <span className="flex-1" />
        {messages.length > 0 && <button type="button" className="cs-ibtn" title="New conversation" aria-label="New conversation"
          disabled={busy} onClick={() => { setMessages([]); setError(null) }}><Icon name="compose" /></button>}
        <button type="button" className="cs-ibtn" title="Close" aria-label="Close the assistant" onClick={() => setOpen(false)}><Icon name="dismiss" /></button>
      </header>
      <div ref={list} className="cs-ai-list" aria-live="polite">
        {messages.length === 0 && <div className="cs-ai-hello">
          <div className="cs-ai-welcome">
            <span className="cs-ai-mark is-lg"><Icon name="sparkle" /></span>
            <b>How can I help with PaperTrace?</b>
            <p>I explain how to run assessments and how to read their reports, from the PaperTrace guide. I do not judge papers beyond what their reports state.</p>
          </div>
          <div className="cs-ai-prompts">
            <small>{page === 'home' ? 'Getting started' : 'On this page'}</small>
            {pagePrompts[page].map(item => <button key={item.text} type="button" className="cs-ai-prompt" onClick={() => void ask(item.text)}>
              <Icon name={item.icon} /><span>{item.text}</span></button>)}
            <small>General</small>
            {generalPrompts.map(item => <button key={item.text} type="button" className="cs-ai-prompt" onClick={() => void ask(item.text)}>
              <Icon name={item.icon} /><span>{item.text}</span></button>)}
          </div>
        </div>}
        {messages.map((message, index) => {
          const last = index === messages.length - 1
          const streaming = busy && last && message.role === 'assistant'
          const legacyAnswer = message.role === 'assistant' && containsChinese(message.content)
          return message.role === 'user'
            ? <div key={index} className="cs-ai-msg is-user">{message.content}</div>
            : <div key={index} className="cs-ai-msg is-assistant">
                <span className="cs-ai-mark is-sm" aria-hidden="true"><Icon name="sparkle" /></span>
                <div className="grid min-w-0 gap-1.5">
                  {legacyAnswer ? <p>This earlier answer is unavailable in English. Please ask your question again.</p>
                    : message.content ? <Rich text={message.content} /> : <span className="cs-ai-thinking">Thinking<i /><i /><i /></span>}
                  {streaming && message.content && <i className="cs-ai-caret" aria-hidden="true" />}
                  {!streaming && message.content && !legacyAnswer && <div className="cs-ai-actions">
                    <button type="button" title="Copy" aria-label="Copy the answer" onClick={() => void copy(index)}><Icon name={copied === index ? 'checkmark' : 'copy'} /></button>
                    <button type="button" title="Helpful" aria-label="Helpful" aria-pressed={message.rating === 'up'} onClick={() => rate(index, 'up')}><Icon name="thumb_like" /></button>
                    <button type="button" title="Not helpful" aria-label="Not helpful" aria-pressed={message.rating === 'down'} onClick={() => rate(index, 'down')}><Icon name="thumb_dislike" /></button>
                    {message.rating && <small>Thanks, noted.</small>}
                  </div>}
                </div>
              </div>
        })}
        {error && <p className="cs-ai-error" role="alert">{error}</p>}
      </div>
      <footer>
        {page !== 'home' && <span className="cs-ai-ctx" title={describe()}><Icon name="info" />Shares {shared[page]} with the assistant</span>}
        <div className="cs-ai-sender">
          <textarea ref={field} rows={1} value={input} placeholder="Ask how to use PaperTrace or read a report" aria-label="Question"
            onChange={event => setInput(event.target.value)} onKeyDown={onKey} />
          {busy
            ? <button type="button" className="cs-ai-send" aria-label="Stop the answer" onClick={() => abort.current?.abort()}><Icon name="stop" /></button>
            : <button type="button" className="cs-ai-send" aria-label="Send" disabled={!input.trim()} onClick={() => void ask(input)}><Icon name="send" /></button>}
        </div>
        <small className="cs-ai-note">AI-generated answers may be inaccurate; check them against the report.</small>
      </footer>
    </section>}
    <button type="button" className={`cs-ai-fab ${open ? 'is-open' : ''} ${onReport ? 'is-report' : ''}`} aria-expanded={open} aria-label={open ? 'Close the assistant' : 'Open the help assistant'}
      title={open ? undefined : 'Assistant'} onClick={() => setOpen(value => !value)}><Icon name={open ? 'dismiss' : 'chat_sparkle'} /></button>
  </>
}
