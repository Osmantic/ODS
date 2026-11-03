import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import { createElement } from 'react'
import { render } from '../../test/test-utils'
import PortalIntegrationsSettings from './PortalIntegrationsSettings'

const APPS = {
  documents: { enabled: false, url: 'http://127.0.0.1:7807', hasKey: false },
  photos: { enabled: false, url: 'http://127.0.0.1:2283', hasKey: false },
  watch: { enabled: false, url: 'http://127.0.0.1:11039', changes: 'ask', hasKey: false },
  news: { enabled: false, url: 'http://127.0.0.1:11029', username: '', digest: { enabled: false, time: '07:00', timezone: 'UTC' }, hasKey: false },
}
const SETTINGS = {
  schemaVersion: 1, revision: 1,
  notifications: { enabled: true, url: 'http://127.0.0.1:8097', topic: 'portal', username: '', notifyApprovals: true, hasPassword: false, approvalLink: '', scheduledTasks: 'all' },
  notes: { enabled: false, vaultPath: '', write: 'ask' }, mcp: { servers: [] }, apps: APPS,
}
const TELEGRAM = { enabled: false, ownerId: '', apiRoot: 'https://api.telegram.org', hasToken: false, bot: '', lastError: '', lastMessageAt: 0, refusedCount: 0 }
const response = (body, status = 200) => ({ ok: status >= 200 && status < 300, status, json: async () => body })
const TOKEN = '987654321:AAH' + 'x'.repeat(32)

function fakeService(telegram = TELEGRAM) {
  let current = { settings: SETTINGS, approvals: [], activity: [], telegram }
  const posts = []
  vi.stubGlobal('fetch', vi.fn(async (url, options) => {
    if (options.method !== 'POST') return response(current)
    const action = url.split('/').pop()
    const body = JSON.parse(options.body)
    posts.push([action, body])
    if (action === 'telegram-save') {
      if (body.enabled && !(body.ownerId && (body.token || current.telegram.hasToken))) return response({ detail: 'Give the bot token and your Telegram user ID, or turn Telegram off.', code: 'invalid-settings' }, 400)
      current = { ...current, telegram: { ...current.telegram, enabled: body.enabled, ownerId: body.ownerId, apiRoot: body.apiRoot, hasToken: current.telegram.hasToken || Boolean(body.token) } }
      return response({ ...current, notice: 'Telegram saved.' })
    }
    if (action === 'telegram-test') {
      current = { ...current, telegram: { ...current.telegram, bot: '@portal_test_bot' } }
      return response({ ...current, notice: 'Connected to @portal_test_bot. Message it from your own Telegram account to talk to Portal.' })
    }
    return response(current)
  }))
  render(createElement(PortalIntegrationsSettings))
  return posts
}

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })

const card = async () => within((await screen.findByRole('heading', { name: 'Telegram' })).closest('section'))

it('saves Telegram with the token only when typed, then tests the bot', async () => {
  const posts = fakeService()
  const telegram = await card()
  expect(telegram.getByText('@BotFather')).toBeInTheDocument()
  expect(telegram.getByText('@userinfobot')).toBeInTheDocument()
  expect(telegram.getByRole('button', { name: 'Save Telegram' })).toBeDisabled()
  expect(telegram.getByRole('button', { name: 'Test' })).toBeDisabled()
  fireEvent.click(telegram.getByLabelText('Let me chat with Portal from Telegram'))
  fireEvent.change(telegram.getByLabelText('Bot token'), { target: { value: TOKEN } })
  fireEvent.change(telegram.getByLabelText('Your Telegram user ID'), { target: { value: ' 123456789 ' } })
  fireEvent.click(telegram.getByRole('button', { name: 'Save Telegram' }))
  expect(await telegram.findByText('Telegram saved.')).toBeInTheDocument()
  expect(posts).toEqual([['telegram-save', { enabled: true, ownerId: '123456789', apiRoot: 'https://api.telegram.org', token: TOKEN }]])
  expect(telegram.getByLabelText('Bot token')).toHaveValue('')
  expect(telegram.getByLabelText('Bot token')).toHaveAttribute('placeholder', 'Saved. Type to replace.')
  fireEvent.click(telegram.getByRole('button', { name: 'Test' }))
  expect(await telegram.findByText(/Connected to @portal_test_bot/)).toBeInTheDocument()
  expect(telegram.getByText(/Portal answers you at/)).toHaveTextContent('Portal answers you at @portal_test_bot.')
  // Saving again without typing a token leaves the saved one alone.
  fireEvent.change(telegram.getByLabelText('Your Telegram user ID'), { target: { value: '123456780' } })
  fireEvent.click(telegram.getByRole('button', { name: 'Save Telegram' }))
  await waitFor(() => expect(posts.at(-1)).toEqual(['telegram-save', { enabled: true, ownerId: '123456780', apiRoot: 'https://api.telegram.org' }]))
})

it('a refused save says why next to the button', async () => {
  fakeService()
  const telegram = await card()
  fireEvent.click(telegram.getByLabelText('Let me chat with Portal from Telegram'))
  fireEvent.click(telegram.getByRole('button', { name: 'Save Telegram' }))
  expect(await telegram.findByRole('alert')).toHaveTextContent('Give the bot token and your Telegram user ID, or turn Telegram off.')
})

it('shows a Telegram problem and that strangers were ignored', async () => {
  fakeService({ ...TELEGRAM, enabled: true, ownerId: '123456789', hasToken: true, bot: '@portal_test_bot',
    lastError: 'Telegram refused the bot token. Check it in Settings → Portal integrations.', refusedCount: 2 })
  const telegram = await card()
  expect(telegram.getByRole('alert')).toHaveTextContent('Telegram refused the bot token.')
  expect(telegram.getByText(/Someone other than you wrote to your bot/)).toBeInTheDocument()
  expect(telegram.getByText(/Telegram carries these messages on its servers/)).toBeInTheDocument()
})
