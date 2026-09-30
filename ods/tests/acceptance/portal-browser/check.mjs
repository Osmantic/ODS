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
  assert.deepEqual(pageErrors, [])
  console.log('PASS: installed Portal browser chat and reload history')
} finally {
  await browser.close()
}
