// Checks the assistant's knowledge (guide.md) before it is used. No dependencies; Node 20 or later.
//
//   npm run assistant:check          static checks only, no model calls
//   npm run assistant:check -- --ask also asks the regression questions in questions.json through the running service
//
// Static checks:
//   - every quoted interface label in the guide ("Start assessment") still exists in the frontend source, so a renamed
//     button cannot leave the assistant describing a control that is gone;
//   - no secrets or service addresses (keys, bearer tokens, http(s) links) are in the guide;
//   - every section has content, and the guide stays within its size budget (it is sent with every question).
// Exit code 1 when a check fails.
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'

const root = fileURLToPath(new URL('..', import.meta.url))
const guide = readFileSync(join(root, 'assistant/guide.md'), 'utf8')
const BUDGET = 24_000
// Quoted phrases in the guide that are not interface labels: names used by the browser or by the rubric.
const NOT_LABELS = new Set(['Save as PDF', 'not the same as wrong'])

const failures = []
const warnings = []

function sourceText(dir) {
  return readdirSync(dir).map(name => {
    const path = join(dir, name)
    if (statSync(path).isDirectory()) return name === 'test' ? '' : sourceText(path)
    return /\.(tsx?|css)$/.test(name) && !/\.test\./.test(name) ? readFileSync(path, 'utf8') : ''
  }).join('\n')
}
// Labels can be split across JSX lines or use typographic quotes; compare on collapsed whitespace.
const source = sourceText(join(root, 'src')).replace(/\s+/g, ' ')

const labels = [...new Set([...guide.matchAll(/"([A-Z][^"\n]{1,60})"/g)].map(match => match[1]))]
const missing = labels.filter(label => !NOT_LABELS.has(label) && !source.includes(label))
for (const label of missing) failures.push(`"${label}" is quoted as an interface label but no longer appears in src/`)

for (const [pattern, what] of [[/sk-[A-Za-z0-9_-]{12,}/, 'an API key'], [/Bearer\s+\S+/i, 'a bearer token'], [/https?:\/\//, 'a link or service address']]) {
  if (pattern.test(guide)) failures.push(`the guide contains ${what}; keep keys and addresses out of it`)
}

const sections = guide.split(/^## /m).slice(1)
for (const section of sections) {
  const [title, ...body] = section.split('\n')
  if (!body.join('\n').trim()) failures.push(`section "${title.trim()}" is empty`)
}
if (guide.length > BUDGET) failures.push(`the guide is ${guide.length} characters; keep it under ${BUDGET} (it is sent with every question)`)
else if (guide.length > BUDGET * 0.85) warnings.push(`the guide is ${guide.length} of ${BUDGET} characters`)

console.log(`guide.md: ${sections.length} sections, ${guide.length} characters, ${labels.length} quoted labels checked`)

if (process.argv.includes('--ask')) {
  const port = Number(process.env.ASSISTANT_PORT || 8020)
  const questions = JSON.parse(readFileSync(join(root, 'assistant/questions.json'), 'utf8'))
  for (const { question, context, expect } of questions) {
    let answer = ''
    try {
      const response = await fetch(`http://127.0.0.1:${port}/assistant/chat`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ messages: [{ role: 'user', content: question }], context }),
      })
      if (!response.ok) throw new Error((await response.json().catch(() => null))?.error ?? `HTTP ${response.status}`)
      for (const line of (await response.text()).split('\n\n')) {
        const data = line.startsWith('data:') ? JSON.parse(line.slice(5)) : null
        if (data?.delta) answer += data.delta
        if (data?.error) throw new Error(data.error)
      }
    } catch (error) {
      failures.push(`"${question}": the service did not answer (${error.message}); is it running on port ${port}?`)
      continue
    }
    const lost = expect.filter(term => !answer.toLowerCase().includes(term.toLowerCase()))
    console.log(`${lost.length ? '✗' : '✓'} ${question}`)
    if (lost.length) failures.push(`"${question}": the answer does not mention ${lost.map(term => `"${term}"`).join(', ')}`)
  }
}

for (const warning of warnings) console.log(`warning: ${warning}`)
for (const failure of failures) console.log(`✗ ${failure}`)
if (failures.length) process.exit(1)
console.log('✓ guide.md checks passed')
