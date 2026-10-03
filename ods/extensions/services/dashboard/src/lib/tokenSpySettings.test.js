// @vitest-environment node
// Exercise the shipped Token Spy HTML with the dashboard's existing DOM runner.
import { execFileSync } from 'node:child_process'
import { resolve } from 'node:path'
import process from 'node:process'
import { JSDOM } from 'jsdom'
import { afterEach, expect, test, vi } from 'vitest'

const source = resolve('../token-spy/main.py')
const html = execFileSync(process.platform === 'win32' ? 'python' : 'python3', ['-X', 'utf8', '-c', [
  'import ast, pathlib, sys',
  'module = ast.parse(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))',
  'value = next(n.value for n in module.body if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "DASHBOARD_HTML" for t in n.targets))',
  'sys.stdout.write(ast.literal_eval(value))',
].join('\n'), source], { encoding: 'utf8' })
let browser
afterEach(() => browser?.window.close())

async function settingsPage(agents) {
  browser = new JSDOM(html, { url: 'http://localhost', runScripts: 'outside-only' })
  const { window } = browser
  window.setInterval = vi.fn()
  window.setTimeout = vi.fn()
  const fetch = vi.fn(async (_url, options) => options?.method === 'POST'
    ? { ok: false, json: async () => ({ error: 'fixture: leave form open' }) }
    : { status: 200, json: async () => ({ session_char_limit: 200000, poll_interval_minutes: 5, agents }) })
  window.fetch = fetch
  window.eval(window.document.scripts[1].textContent)
  await window.loadSettingsUI()
  return { window, fetch, groups: [...window.document.querySelectorAll('#settings-grid .setting-group')].slice(1) }
}

test('agents whose names normalize to the same DOM id retain separate overrides', async () => {
  const { window, fetch, groups } = await settingsPage({
    'assistant-runner': { session_char_limit: 30000, poll_interval_minutes: 2 },
    assistant_runner: { session_char_limit: 80000, poll_interval_minutes: 8 },
  })
  expect(groups.map(group => [...group.querySelectorAll('input')].map(input => input.value)))
    .toEqual([['30000', '2'], ['80000', '8']])
  groups[0].querySelector('input').value = '40000'
  groups[1].querySelectorAll('input')[1].value = '9'
  await window.saveSettings()
  const [, request] = fetch.mock.calls.find(([, options]) => options?.method === 'POST')
  expect(JSON.parse(request.body).agents).toEqual({
    'assistant-runner': { session_char_limit: 40000, poll_interval_minutes: 2 },
    assistant_runner: { session_char_limit: 80000, poll_interval_minutes: 9 },
  })
})

test('display headings are not parsed back into agent identifiers', async () => {
  const agent = 'Daily Override helper'
  const { window, fetch, groups } = await settingsPage({
    [agent]: { session_char_limit: null, poll_interval_minutes: 3 },
  })
  groups[0].querySelectorAll('input')[1].value = ''
  await window.saveSettings()
  const [, request] = fetch.mock.calls.find(([, options]) => options?.method === 'POST')
  expect(JSON.parse(request.body)).toEqual({
    session_char_limit: 200000, poll_interval_minutes: 5,
    agents: { [agent]: { session_char_limit: null, poll_interval_minutes: null } },
  })
})

test('agent names remain literal text and special object keys survive saving', async () => {
  const names = ['<em>Daily</em> & helper', '__proto__', 'constructor']
  const agents = Object.fromEntries(names.map(name => [name, {
    session_char_limit: 30000, poll_interval_minutes: 2,
  }]))
  const { window, fetch, groups } = await settingsPage(agents)
  expect(groups.map(group => group.querySelector('h4').textContent))
    .toEqual(names.map(name => `${name} Override`))
  expect(groups[0].querySelector('em')).toBeNull()
  await window.saveSettings()
  const [, request] = fetch.mock.calls.find(([, options]) => options?.method === 'POST')
  expect(JSON.parse(request.body).agents).toEqual(agents)
})
