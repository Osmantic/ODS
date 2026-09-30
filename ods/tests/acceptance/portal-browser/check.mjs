import assert from 'node:assert/strict'
import { randomBytes } from 'node:crypto'
import { chromium, expect } from '@playwright/test'

const base = process.env.ODS_PORTAL_BROWSER_URL
assert.match(base || '', /^http:\/\/127\.0\.0\.1:\d+$/)

const browser = await chromium.launch({ headless: true })
try {
  const page = await browser.newPage()
  const pageErrors = []
  page.on('pageerror', error => pageErrors.push(error.message))

  await page.goto(base, { waitUntil: 'domcontentloaded', timeout: 60_000 })
  await expect(page.getByRole('heading', { name: 'Choose your password' })).toBeVisible({ timeout: 60_000 })
  const password = randomBytes(32).toString('base64url')
  await page.getByLabel('New password').fill(password)
  await page.getByLabel('Confirm password').fill(password)
  await page.getByRole('button', { name: 'Save password' }).click()
  const composer = page.locator('textarea.pixel-composer-input')
  await expect(composer).toBeVisible({ timeout: 60_000 })
  await expect(composer).toBeEnabled({ timeout: 60_000 })

  const prompt = `Portal browser acceptance ${Date.now()}: reply OK`
  await composer.fill(prompt)
  await page.locator('button[title="Send"]').click()
  await expect(page.locator('[data-pixel-message-index]').filter({ hasText: prompt })).toBeVisible()
  await expect(page.locator('[data-pixel-response]').last()).toContainText('OK', { timeout: 120_000 })

  const saved = await page.evaluate(() => JSON.parse(localStorage.getItem('ods.pixel.chat.v1') || 'null'))
  assert.equal(saved?.messages?.some(message => message.role === 'user' && message.content === prompt), true)
  assert.equal(saved?.messages?.some(message => message.role === 'assistant' && message.content.includes('OK')), true)

  await page.reload({ waitUntil: 'domcontentloaded' })
  await expect(page.locator('[data-pixel-message-index]').filter({ hasText: prompt })).toBeVisible({ timeout: 60_000 })
  await expect(page.locator('[data-pixel-response]').last()).toContainText('OK', { timeout: 60_000 })
  await page.goto(`${base}/dashboard`, { waitUntil: 'domcontentloaded' })
  const chatCard = page.getByRole('link', { name: /AI Chat/ })
  try {
    await expect(chatCard).toHaveAttribute('href', '/', { timeout: 60_000 })
  } catch (error) {
    const diagnostic = await page.evaluate(async () => {
      const read = async path => {
        try {
          const response = await fetch(path, { cache: 'no-store' })
          return { http: response.status, body: response.ok ? await response.json() : {} }
        } catch { return { http: 0, body: {} } }
      }
      const [pixel, features] = await Promise.all([read('/api/pixel/status'), read('/api/features')])
      return {
        path: location.pathname,
        pixel: { http: pixel.http, available: pixel.body.available, state: pixel.body.state },
        features: { http: features.http, ids: (features.body.features || []).map(feature => feature.id), chat: (features.body.features || []).find(feature => feature.id === 'chat')?.status },
      }
    })
    console.log('Dashboard card diagnostic:', JSON.stringify({ ...diagnostic,
      chatTextCount: await page.getByText('AI Chat', { exact: true }).count(),
      panelCount: await page.locator('aside[aria-label="Workspace panel"]').count(),
      pageErrors }))
    throw error
  }
  await chatCard.click()
  await expect(composer).toBeVisible({ timeout: 60_000 })
  assert.deepEqual(pageErrors, [])
  console.log('PASS: installed Portal browser chat, reload history, and Dashboard card route')
} finally {
  await browser.close()
}
