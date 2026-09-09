import { expect, test } from '@playwright/test'
import { readFileSync } from 'node:fs'

// Exercise the exact shipped dashboard CSP, including its inline-script restriction.
const nginx = readFileSync(new URL('../nginx.conf', import.meta.url), 'utf8')
const csp = nginx.match(/add_header Content-Security-Policy "([^"]+)"/)[1]
const metrics = {
  cluster: { active_gpus: 2, total_gpus: 3, failover_ready: true },
  agent: { session_count: 4, last_update: '2026-05-01T12:00:00Z' },
  throughput: { current: 8, average: 6, history: [
    { timestamp: '2026-05-01T11:59:55Z', tokens_per_sec: 4 },
    { timestamp: '2026-05-01T12:00:00Z', tokens_per_sec: 8 },
  ] },
}

test.beforeEach(async ({ page }) => {
  await page.route('**/*', async route => {
    const url = new URL(route.request().url())
    if (url.origin !== 'http://127.0.0.1:18244') return route.abort()
    if (url.pathname === '/agents.html') {
      const response = await route.fetch()
      return route.fulfill({ response, headers: { ...response.headers(), 'content-security-policy': csp } })
    }
    return route.continue()
  })
})

test('renders and refreshes local metrics without any CDN requests', async ({ page }) => {
  const external = []
  page.on('request', request => {
    if (!request.url().startsWith('http://127.0.0.1:18244/')) external.push(request.url())
  })
  let sessions = 4
  await page.route('**/api/agents/metrics', route => route.fulfill({
    json: { ...metrics, agent: { ...metrics.agent, session_count: sessions } },
  }))
  await page.goto('/agents.html')
  await expect(page.locator('#cluster')).toHaveText('2/3 GPUs')
  await expect(page.locator('#sessions')).toHaveText('4')
  await expect(page.locator('#throughput-current')).toHaveText('8.0')
  await expect(page.locator('#throughput-line')).toHaveAttribute('points', '0,80 600,0')
  sessions = 7
  await page.getByRole('button', { name: 'Refresh metrics' }).click()
  await expect(page.locator('#sessions')).toHaveText('7')
  expect(external).toEqual([])
})

test('shows an API failure and recovers through the refresh action', async ({ page }) => {
  let healthy = false
  await page.route('**/api/agents/metrics', route => healthy
    ? route.fulfill({ json: metrics })
    : route.fulfill({ status: 503, json: { detail: 'unavailable' } }))
  await page.goto('/agents.html')
  await expect(page.getByRole('alert')).toContainText('HTTP 503')
  healthy = true
  await page.getByRole('button', { name: 'Refresh metrics' }).click()
  await expect(page.locator('#sessions')).toHaveText('4')
  await expect(page.getByRole('alert')).toBeHidden()
})

test('does not interpret metric values as HTML', async ({ page }) => {
  await page.route('**/api/agents/metrics', route => route.fulfill({
    json: { ...metrics, agent: { session_count: '<img src=x onerror=alert(1)>', last_update: '<script>bad()</script>' } },
  }))
  await page.goto('/agents.html')
  await expect(page.locator('#sessions')).toHaveText('0')
  await expect(page.locator('#agent-update')).toHaveText('Updated: N/A')
  await expect(page.locator('img')).toHaveCount(0)
})
