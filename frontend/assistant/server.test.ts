import { spawn } from 'node:child_process'
import { once } from 'node:events'
import { createServer, type Server } from 'node:http'
import { afterEach, expect, it } from 'vitest'

const cleanup: Array<() => Promise<unknown>> = []
afterEach(async () => { await Promise.all(cleanup.splice(0).map(stop => stop())) })

async function listen(server: Server) {
  server.listen(0, '127.0.0.1')
  await once(server, 'listening')
  const address = server.address()
  if (!address || typeof address === 'string') throw new Error('No test port assigned')
  return address.port
}

it('keeps English answers, ignores old Chinese assistant turns and blocks Chinese output before streaming it', async () => {
  const requests: Array<{ messages: Array<{ role: string; content: string }> }> = []
  let answer = 'Select Start assessment.'
  const upstream = createServer(async (request, response) => {
    let body = ''
    for await (const chunk of request) body += chunk
    requests.push(JSON.parse(body))
    response.writeHead(200, { 'Content-Type': 'text/event-stream' })
    response.end(`data: ${JSON.stringify({ choices: [{ delta: { content: answer } }] })}\n\ndata: [DONE]\n\n`)
  })
  const upstreamPort = await listen(upstream)
  cleanup.push(() => new Promise<void>(resolve => upstream.close(() => resolve())))
  const reservation = createServer()
  const port = await listen(reservation)
  await new Promise<void>(resolve => reservation.close(() => resolve()))
  const child = spawn(process.execPath, ['assistant/server.mjs'], {
    cwd: process.cwd(),
    env: { ...process.env, ASSISTANT_PORT: String(port), ASSISTANT_API_KEY: 'test-only', ASSISTANT_MODEL: 'offline-test', ASSISTANT_BASE_URL: `http://127.0.0.1:${upstreamPort}` },
    stdio: ['ignore', 'pipe', 'pipe'],
  })
  cleanup.push(async () => {
    if (child.exitCode === null) {
      const exited = once(child, 'exit')
      child.kill()
      await exited
    }
  })
  await once(child.stdout!, 'data')
  const ask = () => fetch(`http://127.0.0.1:${port}/assistant/chat`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ messages: [
      { role: 'user', content: '如何开始？' },
      { role: 'assistant', content: '点击开始评估。' },
      { role: 'user', content: '请用中文解释。' },
    ] }),
  }).then(response => response.text())

  expect(await ask()).toContain('Select Start assessment.')
  expect(requests[0].messages[0].content).toContain('Always reply in English')
  expect(requests[0].messages.some(message => message.role === 'assistant')).toBe(false)
  expect(requests[0].messages.at(-1)?.content).toBe('请用中文解释。')

  answer = '请点击开始评估。'
  const blocked = await ask()
  expect(blocked).not.toMatch(/\p{Script=Han}/u)
  expect(blocked).toContain('The assistant could not provide an English answer.')
  expect(blocked).not.toContain('"delta"')
}, 10_000)
