import { fireEvent, screen, waitFor } from '@testing-library/react'
import { render } from '../test/test-utils'
import FirstBoot from './FirstBoot' // eslint-disable-line no-unused-vars

const response = (body, status = 200) => ({ ok: status >= 200 && status < 300, status, json: async () => body })

describe('FirstBoot phase receipts', () => {
  afterEach(() => {
    vi.restoreAllMocks()
    globalThis.localStorage.removeItem('ods-firstboot-progress')
  })

  it('does not reapply a successful template on a later Finish retry', async () => {
    let generateAttempts = 0
    const fetchMock = vi.fn(async (url, options = {}) => {
      if (url === '/api/auth/magic-link/owner-card/status') return response({ ready: true, url_mode: 'lan' })
      if (url === '/api/templates/onboarding-full-stack/apply') return response({ failed_services: [], skipped_services: [], enabled_count: 1, started_count: 1 })
      if (url === '/api/auth/magic-link/generate') {
        generateAttempts += 1
        return generateAttempts === 1 ? response({ detail: 'temporary failure' }, 500) : response({ url: 'http://owner.test/link' })
      }
      if (url === '/api/setup/complete') return response({ success: true })
      if (url === '/api/auth/admin-session') return response({ ok: true })
      throw new Error(`unexpected request: ${url} ${options.method || 'GET'}`)
    })
    vi.stubGlobal('fetch', fetchMock)

    render(<FirstBoot onComplete={vi.fn()} />)
    fireEvent.change(screen.getByDisplayValue('ods'), { target: { value: 'spark' } })
    fireEvent.click(screen.getByRole('button', { name: /^continue$/i }))
    fireEvent.change(screen.getByPlaceholderText('alice'), { target: { value: 'sam' } })
    fireEvent.click(screen.getByRole('button', { name: /^continue$/i }))
    fireEvent.click(screen.getByRole('button', { name: /full ods stack/i }))
    fireEvent.click(screen.getByRole('button', { name: /^continue$/i }))
    fireEvent.click(await screen.findByRole('button', { name: /^finish$/i }))
    expect(await screen.findByText('temporary failure')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /^finish$/i }))
    await waitFor(() => expect(screen.getByRole('heading', { name: /you.re set/i })).toBeInTheDocument())
    expect(fetchMock.mock.calls.filter(([url]) => url === '/api/templates/onboarding-full-stack/apply')).toHaveLength(1)
  })
})
