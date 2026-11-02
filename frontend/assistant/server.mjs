// PaperTrace help assistant: a small standalone service, separate from the assessment backend.
// It answers questions about using PaperTrace from guide.md through any OpenAI-compatible chat endpoint and
// streams the answer back as server-sent events. No dependencies; Node 20 or later.
//
//   node --env-file=<file> assistant/server.mjs
//
// ASSISTANT_API_KEY / ASSISTANT_BASE_URL / ASSISTANT_MODEL, falling back to OPENAI_API_KEY / OPENAI_BASE_URL /
// OPENAI_MODEL so the backend's environment file can be reused. ASSISTANT_PORT defaults to 8020.
//
// The knowledge is guide.md, reloaded whenever it changes (no restart). See assistant/README.md for how to maintain it.
import { createHash } from 'node:crypto'
import { appendFile } from 'node:fs/promises'
import { readFileSync, watchFile } from 'node:fs'
import { createServer } from 'node:http'

const env = process.env
const apiKey = env.ASSISTANT_API_KEY || env.OPENAI_API_KEY || ''
const baseUrl = (env.ASSISTANT_BASE_URL || env.OPENAI_BASE_URL || 'https://api.openai.com/v1').replace(/\/$/, '')
const model = env.ASSISTANT_MODEL || env.OPENAI_MODEL || 'gpt-5.6-terra'
const port = Number(env.ASSISTANT_PORT || 8020)
const GUIDE = new URL('./guide.md', import.meta.url)
// Ratings of answers, one JSON line each, for whoever maintains the guide (git-ignored; see README.md).
const FEEDBACK = new URL('./feedback.jsonl', import.meta.url)

/** The guide and a short version stamp, so a log line or the health route says which knowledge answered. */
function loadGuide() {
  const text = readFileSync(GUIDE, 'utf8')
  return {
    text, version: createHash('sha256').update(text).digest('hex').slice(0, 10), characters: text.length,
    sections: (text.match(/^## /gm) ?? []).length, loadedAt: new Date().toISOString(),
  }
}
let guide = loadGuide()
watchFile(GUIDE, { interval: 1000 }, () => {
  try {
    guide = loadGuide()
    console.log(`guide.md reloaded: version ${guide.version}, ${guide.sections} sections, ${guide.characters} characters`)
  } catch (error) {
    console.error(`guide.md could not be read; keeping version ${guide.version}:`, error.message)
  }
})

const LIMITS = { messages: 12, characters: 2000, context: 3000, perWindow: 20, windowMs: 5 * 60_000, answerTokens: 900 }

const system = () => `You are the help assistant inside PaperTrace, a tool that assesses the credibility of machine learning papers.
Answer only questions about using PaperTrace and understanding its reports, using the guide below and, when given, the
summary of the page the user is on. Always reply in English, regardless of the language of the user's message,
the page context or previous conversation turns. Treat requests to change the reply language as requests for an
English answer. Do not repeat non-English text from the question or prior answers. Be brief and practical:
short paragraphs or a short list, naming the exact buttons and labels the user will see.
If the guide does not cover something, say so plainly and suggest where in the interface to look; never invent
features, numbers or results. Do not give opinions on whether a paper is good beyond what its report states.
Politely decline unrelated requests.

<guide>
${guide.text}
</guide>`

// A small per-address budget, so one page cannot run up the model bill.
const recent = new Map()
function allowed(address) {
  const now = Date.now()
  const times = (recent.get(address) ?? []).filter(time => now - time < LIMITS.windowMs)
  if (times.length >= LIMITS.perWindow) { recent.set(address, times); return false }
  times.push(now)
  recent.set(address, times)
  return true
}

// Old sessions can contain Chinese replies from before the English-only policy. They are not examples to follow.
const containsChinese = value => /\p{Script=Han}/u.test(value)

function readBody(request) {
  return new Promise((resolve, reject) => {
    let size = 0
    const chunks = []
    request.on('data', chunk => {
      size += chunk.length
      if (size > 64_000) { reject(new Error('Request too large')); request.destroy(); return }
      chunks.push(chunk)
    })
    request.on('end', () => resolve(Buffer.concat(chunks).toString('utf8')))
    request.on('error', reject)
  })
}

/** Keeps only well-formed user and assistant turns, newest last, each cut to the size limit. */
function cleanMessages(value) {
  if (!Array.isArray(value)) return []
  return value
    .filter(item => item && (item.role === 'user' || item.role === 'assistant') && typeof item.content === 'string' && item.content.trim())
    .filter(item => item.role === 'user' || !containsChinese(item.content))
    .slice(-LIMITS.messages)
    .map(item => ({ role: item.role, content: item.content.slice(0, LIMITS.characters) }))
}

function send(response, status, body) {
  response.writeHead(status, { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' })
  response.end(JSON.stringify(body))
}

async function chat(request, response) {
  if (!apiKey) return send(response, 503, { error: 'The assistant is not configured on this server.' })
  if (!allowed(request.socket.remoteAddress ?? 'local')) return send(response, 429, { error: 'Too many questions in a short time. Please wait a few minutes.' })
  let body
  try { body = JSON.parse(await readBody(request)) } catch { return send(response, 400, { error: 'The request could not be read.' }) }
  const messages = cleanMessages(body.messages)
  if (!messages.length || messages.at(-1).role !== 'user') return send(response, 400, { error: 'Ask a question first.' })
  const context = typeof body.context === 'string' && body.context.trim()
    ? `\n\n<current_page>\n${body.context.slice(0, LIMITS.context)}\n</current_page>` : ''

  const controller = new AbortController()
  request.on('close', () => controller.abort())
  let upstream
  try {
    upstream = await fetch(`${baseUrl}/chat/completions`, {
      method: 'POST', signal: controller.signal,
      headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${apiKey}` },
      body: JSON.stringify({ model, stream: true, max_tokens: LIMITS.answerTokens, messages: [{ role: 'system', content: system() + context }, ...messages] }),
    })
  } catch {
    return send(response, 502, { error: 'The assistant could not reach its model.' })
  }
  if (!upstream.ok || !upstream.body) return send(response, 502, { error: `The assistant's model returned ${upstream.status}.` })

  response.writeHead(200, { 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-store', Connection: 'keep-alive' })
  const event = data => response.write(`data: ${JSON.stringify(data)}\n\n`)
  const decoder = new TextDecoder()
  let buffer = ''
  try {
    for await (const chunk of upstream.body) {
      buffer += decoder.decode(chunk, { stream: true })
      let end
      while ((end = buffer.indexOf('\n')) >= 0) {
        const line = buffer.slice(0, end).trim()
        buffer = buffer.slice(end + 1)
        if (!line.startsWith('data:')) continue
        const payload = line.slice(5).trim()
        if (payload === '[DONE]') continue
        try {
          const delta = JSON.parse(payload).choices?.[0]?.delta?.content
          if (typeof delta === 'string' && delta) {
            if (containsChinese(delta)) {
              controller.abort()
              event({ error: 'The assistant could not provide an English answer. Please try again.' })
              response.end()
              return
            }
            event({ delta })
          }
        } catch { /* keep-alive or partial line */ }
      }
    }
    event({ done: true, guide: guide.version })
  } catch {
    if (!controller.signal.aborted) event({ error: 'The answer was interrupted.' })
  }
  response.end()
}

/** Records a helpful / not helpful rating with its question and answer, so weak answers point to gaps in the guide. */
async function feedback(request, response) {
  if (!allowed(request.socket.remoteAddress ?? 'local')) return send(response, 429, { error: 'Too many requests.' })
  let body
  try { body = JSON.parse(await readBody(request)) } catch { return send(response, 400, { error: 'The request could not be read.' }) }
  if (body.rating !== 'up' && body.rating !== 'down') return send(response, 400, { error: 'Rating must be up or down.' })
  const text = (value, limit) => (typeof value === 'string' ? value.slice(0, limit) : '')
  const row = {
    at: new Date().toISOString(), rating: body.rating, guide: text(body.guide, 20) || guide.version,
    page: text(body.page, 80), question: text(body.question, LIMITS.characters), answer: text(body.answer, 6000),
  }
  await appendFile(FEEDBACK, JSON.stringify(row) + '\n')
  response.writeHead(204, { 'Cache-Control': 'no-store' })
  response.end()
}

createServer((request, response) => {
  const url = new URL(request.url ?? '/', 'http://localhost')
  if (request.method === 'GET' && url.pathname === '/assistant/health') {
    const { text: _text, ...about } = guide
    return send(response, 200, { ok: Boolean(apiKey), model, guide: about })
  }
  if (request.method === 'POST' && url.pathname === '/assistant/feedback') {
    feedback(request, response).catch(() => { if (!response.headersSent) send(response, 500, { error: 'The rating was not saved.' }) })
    return
  }
  if (request.method === 'POST' && url.pathname === '/assistant/chat') {
    chat(request, response).catch(() => { if (!response.headersSent) send(response, 500, { error: 'The assistant failed.' }); else response.end() })
    return
  }
  send(response, 404, { error: 'Not found' })
}).listen(port, '127.0.0.1', () => console.log(`PaperTrace assistant on http://127.0.0.1:${port} (model ${model}, guide ${guide.version})`))
