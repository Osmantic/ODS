import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import { createElement } from 'react'
import { render as renderBase } from '@testing-library/react'
import { createMemoryRouter, RouterProvider } from 'react-router-dom'
import { ThemeProvider } from '../../contexts/ThemeContext'
import PortalIntegrationsSettings from './PortalIntegrationsSettings'

// The page asks before leaving with unsaved changes (useBlocker), which needs a data router.
const routers = []
function render(ui) {
  const router = createMemoryRouter([{ path: '*', element: createElement(ThemeProvider, null, ui) }],
    { initialEntries: ['/settings?section=portal-integrations'] })
  routers.push(router)
  return renderBase(createElement(RouterProvider, { router }))
}
afterEach(() => { for (const router of routers.splice(0)) router.dispose() })

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

let saveElsewhere

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
      // As the service: the address is saved without a trailing slash.
      // As the service: the saved token goes only to the address it was saved for.
      const apiRoot = body.apiRoot.replace(/\/+$/, ''), moved = apiRoot !== current.telegram.apiRoot
      current = { ...current, telegram: { ...current.telegram, enabled: body.enabled, ownerId: body.ownerId, apiRoot, hasToken: Boolean(body.token) || (current.telegram.hasToken && !moved) } }
      return response({ ...current, notice: 'Telegram saved.' })
    }
    if (action === 'telegram-test') {
      current = { ...current, telegram: { ...current.telegram, bot: '@portal_test_bot' } }
      return response({ ...current, notice: 'Connected to @portal_test_bot. Message it from your own Telegram account to talk to Portal.' })
    }
    return response(current)
  }))
  render(createElement(PortalIntegrationsSettings))
  // Another window saving Telegram.
  saveElsewhere = patch => { current = { ...current, telegram: { ...current.telegram, ...patch } } }
  return posts
}

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })

// Telegram opens from its tile in the integrations hub.
const card = async () => {
  fireEvent.click(await screen.findByRole('button', { name: /^Telegram,/ }))
  return within((await screen.findByRole('heading', { name: 'Telegram' })).closest('section'))
}

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

it('an API address typed with a trailing slash still lets Test run after saving', async () => {
  const posts = fakeService()
  const telegram = await card()
  fireEvent.click(telegram.getByLabelText('Let me chat with Portal from Telegram'))
  fireEvent.change(telegram.getByLabelText('Bot token'), { target: { value: TOKEN } })
  fireEvent.change(telegram.getByLabelText('Your Telegram user ID'), { target: { value: '123456789' } })
  fireEvent.change(telegram.getByLabelText('Telegram API address'), { target: { value: ' https://api.telegram.org/ ' } })
  fireEvent.click(telegram.getByRole('button', { name: 'Save Telegram' }))
  expect(await telegram.findByText('Telegram saved.')).toBeInTheDocument()
  expect(posts.at(-1)[1].apiRoot).toBe('https://api.telegram.org')
  expect(telegram.getByRole('button', { name: 'Save Telegram' })).toBeDisabled()
  expect(telegram.getByRole('button', { name: 'Test' })).toBeEnabled()
  // A slash alone is no change; a different address is.
  fireEvent.change(telegram.getByLabelText('Telegram API address'), { target: { value: 'https://api.telegram.org//' } })
  expect(telegram.getByRole('button', { name: 'Test' })).toBeEnabled()
  fireEvent.change(telegram.getByLabelText('Telegram API address'), { target: { value: 'https://bot-api.example.org' } })
  expect(telegram.getByRole('button', { name: 'Test' })).toBeDisabled()
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

it('shows Telegram saved in another window, and its Save does not undo it', async () => {
  const posts = fakeService()
  let telegram = await card()
  expect(telegram.getByLabelText('Let me chat with Portal from Telegram')).not.toBeChecked()
  saveElsewhere({ enabled: true, ownerId: '123456789', hasToken: true })
  fireEvent.click(screen.getByRole('button', { name: 'Reload' }))
  telegram = within((await screen.findByRole('heading', { name: 'Telegram' })).closest('section'))
  await waitFor(() => expect(telegram.getByLabelText('Let me chat with Portal from Telegram')).toBeChecked())
  expect(telegram.getByLabelText('Your Telegram user ID')).toHaveValue('123456789')
  expect(telegram.getByRole('button', { name: 'Save Telegram' })).toBeDisabled()
  expect(telegram.getByRole('button', { name: 'Test' })).toBeEnabled()
  // An edit here changes only that field.
  fireEvent.change(telegram.getByLabelText('Your Telegram user ID'), { target: { value: '123456780' } })
  fireEvent.click(telegram.getByRole('button', { name: 'Save Telegram' }))
  await waitFor(() => expect(posts.at(-1)).toEqual(['telegram-save', { enabled: true, ownerId: '123456780', apiRoot: 'https://api.telegram.org' }]))
})

it('unsaved Telegram edits mark its tile and ask before leaving the page, outside the page Save', async () => {
  fakeService()
  const telegram = await card()
  const leaving = vi.spyOn(window, 'addEventListener')
  fireEvent.change(telegram.getByLabelText('Your Telegram user ID'), { target: { value: '123456789' } })
  expect(screen.getByRole('navigation', { name: 'Integration' })).toHaveTextContent('Unsaved changes')
  await waitFor(() => expect(leaving).toHaveBeenCalledWith('beforeunload', expect.any(Function)))
  // Telegram saves with its own button, so the page's save bar stays away.
  expect(screen.queryByRole('region', { name: 'Unsaved changes' })).toBeNull()
  fireEvent.click(screen.getByRole('button', { name: 'All integrations' }))
  expect(await screen.findByRole('button', { name: /^Telegram,/ })).toHaveAttribute('aria-label', expect.stringMatching(/, unsaved changes$/))
  // Saved: the mark goes.
  fireEvent.click(screen.getByRole('button', { name: /^Telegram,/ }))
  fireEvent.click(telegram.getByRole('button', { name: 'Save Telegram' }))
  expect(await telegram.findByText('Telegram saved.')).toBeInTheDocument()
  expect(screen.getByRole('navigation', { name: 'Integration' })).not.toHaveTextContent('Unsaved changes')
})

it('says another API address needs the bot token again', async () => {
  fakeService({ ...TELEGRAM, enabled: false, ownerId: '123456789', hasToken: true })
  const telegram = await card()
  expect(telegram.queryByText(/needs the bot token again/)).toBeNull()
  fireEvent.change(telegram.getByLabelText('Telegram API address'), { target: { value: 'https://bots.example' } })
  expect(telegram.getByRole('note')).toHaveTextContent('Another address needs the bot token again')
  fireEvent.change(telegram.getByLabelText('Bot token'), { target: { value: TOKEN } })
  expect(telegram.queryByText(/needs the bot token again/)).toBeNull()
  fireEvent.change(telegram.getByLabelText('Bot token'), { target: { value: '' } })
  fireEvent.click(telegram.getByRole('button', { name: 'Save Telegram' }))
  expect(await telegram.findByText('Telegram saved.')).toBeInTheDocument()
  expect(telegram.getByLabelText('Bot token')).toHaveAttribute('placeholder', '123456789:AAH…')
})
