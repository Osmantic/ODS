import { act, fireEvent, render, screen, within } from '@testing-library/react'
import Invites from './Invites' // eslint-disable-line no-unused-vars

const response = (body, status = 200) => ({
  ok: status >= 200 && status < 300, status, json: async () => body,
})
const kinds = [
  ['owner', 'Print owner card', 'Create owner card', 'Generate owner QR', 'Owner card created'],
  ['guest', 'New guest invite', 'Create guest invite', 'Generate', 'Invite created'],
]

afterEach(() => vi.unstubAllGlobals())

async function start(kind) {
  let settle
  const pending = new Promise(resolve => { settle = resolve })
  const fetcher = vi.fn(async url => {
    if (url.endsWith('/list')) return response({ tokens: [] })
    if (url.endsWith('/status')) return response({ ready: true })
    if (url.endsWith('/generate')) return pending
    if (url.includes('/qr?')) return response({ data_url: 'data:image/png;base64,test' })
    throw new Error('Unexpected request')
  })
  vi.stubGlobal('fetch', fetcher)
  render(<Invites />)
  await screen.findByText('No owner cards yet')
  fireEvent.click(screen.getByRole('button', { name: kind[1] }))
  const dialog = screen.getByRole('dialog', { name: kind[2] })
  fireEvent.change(within(dialog).getByPlaceholderText('alice'), { target: { value: 'alice' } })
  fireEvent.click(within(dialog).getByRole('button', { name: kind[3], exact: true }))
  return { dialog, settle, fetcher }
}

describe.each(kinds)('%s creation ownership', (...kind) => {
  test.each(['Cancel', 'Close', 'Escape', 'backdrop'])('retains the result owner during %s', async dismissal => {
    const { dialog, settle, fetcher } = await start(kind)
    if (dismissal === 'Escape') fireEvent.keyDown(document, { key: 'Escape' })
    else if (dismissal === 'backdrop') fireEvent.click(dialog.parentElement)
    else fireEvent.click(within(dialog).getByRole('button', { name: dismissal, exact: true }))
    expect(screen.getByRole('dialog', { name: kind[2] })).toBeVisible()
    expect(within(dialog).getByRole('button', { name: 'Cancel', exact: true })).toBeDisabled()
    await act(async () => settle(response({
      token_type: kind[0], target_username: 'alice', url: 'http://ods.test/invite/test',
      expires_at: new Date(Date.now() + 3600000).toISOString(),
    })))
    expect(await screen.findByRole('dialog', { name: kind[4] })).toBeVisible()
    expect(fetcher.mock.calls.filter(([url]) => url.endsWith('/generate'))).toHaveLength(1)
  })

  test('unlocks dismissal after a failed generation', async () => {
    const { dialog, settle } = await start(kind)
    await act(async () => settle(response({ detail: 'Creation unavailable' }, 503)))
    expect(await screen.findByText('Creation unavailable')).toBeVisible()
    const cancel = within(dialog).getByRole('button', { name: 'Cancel', exact: true })
    expect(cancel).toBeEnabled()
    fireEvent.click(cancel)
    expect(screen.queryByRole('dialog')).toBeNull()
  })
})
