import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import { randomBytes } from 'node:crypto'
import { chromium, expect } from '@playwright/test'

const base = process.env.ODS_N8N_BROWSER_URL
assert.match(base || '', /^http:\/\/127\.0\.0\.1:\d+$/)
const apiIdentity = () => execFileSync('docker', [
  'inspect', '--format', '{{.State.StartedAt}}|{{.RestartCount}}', 'ods-dashboard-api',
], { encoding: 'utf8' }).trim()

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
  await expect(page.getByRole('heading', { name: 'Choose your password' })).toBeHidden({ timeout: 60_000 })

  await page.goto(`${base}/extensions`, { waitUntil: 'domcontentloaded', timeout: 60_000 })
  await page.getByLabel('Search extensions').fill('n8n')
  const beforeLibraryActions = apiIdentity()
  const add = page.getByRole('button', { name: 'Add n8n (Workflows)' })
  await expect(add).toBeVisible({ timeout: 60_000 })
  await add.click()
  const dialog = page.getByRole('dialog', { name: 'Confirm action' })
  await expect(dialog).toContainText('Enable n8n (Workflows)?')

  const enabledResponse = page.waitForResponse(response =>
    response.url().endsWith('/api/extensions/n8n/enable')
      && response.request().method() === 'POST', { timeout: 300_000 })
  await dialog.getByRole('button', { name: 'Enable' }).click()
  assert.equal((await enabledResponse).status(), 200)
  await expect(dialog).toBeHidden({ timeout: 60_000 })
  const disable = page.getByRole('button', { name: 'Disable n8n (Workflows)' })
  await expect(disable).toBeEnabled({ timeout: 180_000 })
  assert.equal(apiIdentity(), beforeLibraryActions, 'Dashboard API restarted during browser Add')
  assert.deepEqual(pageErrors, [])
  console.log('PASS: browser Library Add reached enabled n8n card')

  await disable.click()
  await expect(dialog).toContainText('Disable n8n (Workflows)?')
  const disabledResponse = page.waitForResponse(response =>
    response.url().endsWith('/api/extensions/n8n/disable')
      && response.request().method() === 'POST', { timeout: 180_000 })
  await dialog.getByRole('button', { name: 'Disable' }).click()
  assert.equal((await disabledResponse).status(), 200)
  await expect(dialog).toBeHidden({ timeout: 60_000 })
  await expect(add).toBeEnabled({ timeout: 60_000 })
  assert.equal(apiIdentity(), beforeLibraryActions, 'Dashboard API restarted during browser Disable')
  assert.deepEqual(pageErrors, [])
  console.log('PASS: browser Library Disable restored addable n8n card')
} finally {
  await browser.close()
}
