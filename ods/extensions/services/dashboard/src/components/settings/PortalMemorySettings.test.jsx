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
const response = (body, status = 200) => ({ ok: status >= 200 && status < 300, status, json: async () => body })
const fact = (id, text) => ({ id, text, createdAt: 1, updatedAt: 1 })

// A fake service that keeps memory the way the integrations service does.
function fakeService(memory) {
  let current = { settings: SETTINGS, approvals: [], activity: [], memory }
  const posts = []
  vi.stubGlobal('fetch', vi.fn(async (url, options) => {
    if (options.method !== 'POST') return response(current)
    const action = url.split('/').pop()
    const body = JSON.parse(options.body)
    posts.push([action, body])
    const m = current.memory
    if (action === 'memory-settings') current = { ...current, memory: { ...m, enabled: body.enabled, askFirst: body.askFirst } }
    if (action === 'memory-edit') {
      if (!body.text.trim()) return response({ detail: 'Give the fact as text.', code: 'invalid-memory' }, 400)
      current = { ...current, memory: { ...m, items: m.items.map(item => item.id === body.id ? { ...item, text: body.text, updatedAt: 2 } : item) } }
    }
    if (action === 'memory-delete') current = { ...current, memory: { ...m, items: m.items.filter(item => item.id !== body.id) } }
    if (action === 'memory-clear') current = { ...current, memory: { ...m, items: [], pending: [] } }
    if (action === 'memory-decide') {
      const waiting = m.pending.find(item => item.id === body.id)
      current = { ...current, memory: { ...m, pending: m.pending.filter(item => item.id !== body.id),
        items: body.decision === 'save' ? [waiting, ...m.items] : m.items } }
    }
    return response(current)
  }))
  render(createElement(PortalIntegrationsSettings))
  return posts
}

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })

// Memory opens from its tile in the integrations hub.
const card = async () => {
  fireEvent.click(await screen.findByRole('button', { name: /^Memory,/ }))
  return within((await screen.findByRole('heading', { name: 'Memory' })).closest('section'))
}

it('lists what Portal remembers and changes it at once, outside the page Save', async () => {
  const posts = fakeService({ enabled: true, askFirst: true, items: [fact('a', 'The owner is vegetarian.'), fact('b', 'The owner\'s sister is Ana.')], pending: [] })
  const memory = await card()
  expect(memory.getByText('Portal remembers 2 things')).toBeInTheDocument()
  expect(memory.getByText(/Portal asks in the chat before it keeps something/)).toBeInTheDocument()
  fireEvent.click(memory.getByRole('button', { name: 'Delete memory: The owner\'s sister is Ana.' }))
  await waitFor(() => expect(memory.getByText('Portal remembers 1 thing')).toBeInTheDocument())
  expect(posts).toEqual([['memory-delete', { id: 'b' }]])
  expect(screen.getByRole('status')).toHaveTextContent('Memory deleted.')
  expect(screen.queryByRole('region', { name: 'Unsaved changes' })).not.toBeInTheDocument()
})

it('edits a memory in place, and Escape or Cancel keeps it', async () => {
  const posts = fakeService({ enabled: true, askFirst: true, items: [fact('a', 'The owner runs on Sundays.')], pending: [] })
  const memory = await card()
  fireEvent.click(memory.getByRole('button', { name: 'Edit memory: The owner runs on Sundays.' }))
  const field = memory.getByLabelText('Memory')
  expect(field).toHaveFocus()
  fireEvent.change(field, { target: { value: 'The owner runs on Saturdays.' } })
  fireEvent.keyDown(field, { key: 'Escape' })
  expect(memory.getByText('The owner runs on Sundays.')).toBeInTheDocument()
  fireEvent.click(memory.getByRole('button', { name: 'Edit memory: The owner runs on Sundays.' }))
  fireEvent.change(memory.getByLabelText('Memory'), { target: { value: 'The owner runs on Saturdays.' } })
  fireEvent.click(memory.getByRole('button', { name: 'Save' }))
  await waitFor(() => expect(memory.getByText('The owner runs on Saturdays.')).toBeInTheDocument())
  expect(posts).toEqual([['memory-edit', { id: 'a', text: 'The owner runs on Saturdays.' }]])
})

it('a refused edit stays open with the reason shown', async () => {
  fakeService({ enabled: true, askFirst: true, items: [fact('a', 'The owner likes tea.')], pending: [] })
  const memory = await card()
  fireEvent.click(memory.getByRole('button', { name: 'Edit memory: The owner likes tea.' }))
  fireEvent.change(memory.getByLabelText('Memory'), { target: { value: '   ' } })
  expect(memory.getByRole('button', { name: 'Save' })).toBeDisabled()
  fireEvent.change(memory.getByLabelText('Memory'), { target: { value: ' x ' } })
  expect(memory.getByRole('button', { name: 'Save' })).toBeEnabled()
})

it('answers facts waiting from the chat, and the switches save at once', async () => {
  const posts = fakeService({ enabled: true, askFirst: true, items: [], pending: [fact('p', 'The owner has a dog named Rex.')] })
  const memory = await card()
  expect(memory.getByText('Portal remembers nothing yet')).toBeInTheDocument()
  const waiting = within(memory.getByRole('list', { name: 'Memories waiting for your answer' }))
  fireEvent.click(waiting.getByRole('button', { name: 'Save' }))
  await waitFor(() => expect(memory.getByText('Portal remembers 1 thing')).toBeInTheDocument())
  expect(memory.queryByRole('list', { name: 'Memories waiting for your answer' })).not.toBeInTheDocument()
  fireEvent.click(memory.getByLabelText('Save memories without asking me first'))
  await waitFor(() => expect(memory.getByText(/keeps what it learns right away/)).toBeInTheDocument())
  fireEvent.click(memory.getByLabelText('Let Portal remember things about me'))
  await waitFor(() => expect(memory.getByLabelText('Save memories without asking me first')).toBeDisabled())
  expect(posts).toEqual([['memory-decide', { id: 'p', decision: 'save' }], ['memory-settings', { enabled: true, askFirst: false }],
    ['memory-settings', { enabled: false, askFirst: false }]])
})

it('forgets everything only after a confirmation that can be cancelled', async () => {
  const posts = fakeService({ enabled: true, askFirst: true, items: [fact('a', 'One'), fact('b', 'Two')], pending: [] })
  const memory = await card()
  fireEvent.click(memory.getByRole('button', { name: 'Forget everything…' }))
  expect(memory.getByText('Forget all 2 memories? This cannot be undone.')).toBeInTheDocument()
  fireEvent.click(memory.getByRole('button', { name: 'Cancel' }))
  expect(posts).toEqual([])
  fireEvent.click(memory.getByRole('button', { name: 'Forget everything…' }))
  fireEvent.click(memory.getByRole('button', { name: 'Forget everything' }))
  await waitFor(() => expect(memory.getByText('Portal remembers nothing yet')).toBeInTheDocument())
  expect(posts).toEqual([['memory-clear', {}]])
  expect(memory.queryByRole('button', { name: /Forget everything/ })).not.toBeInTheDocument()
})

it('an older service without memory shows no memory card', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => response({ settings: SETTINGS, approvals: [], activity: [] })))
  render(createElement(PortalIntegrationsSettings))
  await screen.findByRole('button', { name: /^ntfy,/ })
  expect(screen.queryByRole('heading', { name: 'Memory' })).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: /^Memory,/ })).not.toBeInTheDocument()
})
