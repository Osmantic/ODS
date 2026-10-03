import { render, screen, fireEvent, waitFor, act } from '@testing-library/react'
import PortalExtensionMention, { extensionMentionQuery } from './PortalExtensionMention'

afterEach(() => vi.unstubAllGlobals())

test('only a complete extension command opens catalog suggestions', () => {
  expect(extensionMentionQuery('/extension @')).toBe('')
  expect(extensionMentionQuery('/extensions @doc')).toBe('doc')
  for (const text of ['Explain /extension @doc', '/extension @doc ', '/extension @a/b', 'hello', null]) {
    expect(extensionMentionQuery(text)).toBeUndefined()
  }
})

test('uses actual catalog eligibility and selects a draft without installing', async () => {
  const fetcher = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ extensions: [
    { id: 'docling', name: 'Docling', installable: true },
    { id: 'docling', name: 'Docling', installable: true },
    { id: 'gpu-only', name: 'GPU only', installable: true, status: 'incompatible' },
    { id: 'reference', name: 'Reference', installable: false },
    { id: 'internal', name: 'Internal', installable: true, category: 'core' },
    { id: '../invalid', name: 'Invalid', installable: true },
  ] }) })
  vi.stubGlobal('fetch', fetcher)
  const select = vi.fn()
  const view = render(<PortalExtensionMention query="" onSelect={select} onDismiss={() => {}} />)
  const choice = await screen.findByRole('button', { name: /Docling/ })
  expect(screen.getAllByRole('button', { name: /Docling/ })).toHaveLength(1)
  for (const name of [/GPU only/, /Reference/, /Internal/]) expect(screen.getByRole('button', { name })).toBeDisabled()
  expect(screen.queryByText('Invalid')).not.toBeInTheDocument()
  fireEvent.click(choice)
  expect(select).toHaveBeenCalledWith('/extensions @docling ')
  view.rerender(<PortalExtensionMention query="doc" onSelect={select} onDismiss={() => {}} />)
  expect(screen.queryByRole('button', { name: /GPU only/ })).not.toBeInTheDocument()
  expect(fetcher).toHaveBeenCalledTimes(1)
  expect(fetcher.mock.calls[0][0]).toBe('/api/extensions/catalog')
})

test('catalog failures can be retried and requests abort on close', async () => {
  const fetcher = vi.fn().mockRejectedValueOnce(new Error('offline')).mockResolvedValueOnce({ ok: true, json: async () => ({ extensions: [] }) })
  vi.stubGlobal('fetch', fetcher)
  const view = render(<PortalExtensionMention query="" onSelect={() => {}} onDismiss={() => {}} />)
  fireEvent.click(await screen.findByRole('button', { name: /Retry/ }))
  await screen.findByText('No matching extensions.')
  const signal = fetcher.mock.calls[1][1].signal
  view.unmount()
  expect(signal.aborted).toBe(true)
})

test('user definitions do not imply a completed installation and active installs cannot be repeated', async () => {
  const entries = ['installing', 'setting_up', 'error', 'not_installed', 'disabled', 'enabled'].map(status => ({
    id: status, name: status, status, source: 'user', installable: true,
  }))
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => ({ extensions: entries }) }))
  const select = vi.fn()
  render(<PortalExtensionMention query="" onSelect={select} onDismiss={() => {}} />)
  for (const name of ['installing', 'setting_up']) {
    const button = await screen.findByRole('button', { name: new RegExp(`^${name}`) })
    expect(button).toBeDisabled()
    expect(button).toHaveTextContent('Installation in progress')
    fireEvent.click(button)
  }
  expect(select).not.toHaveBeenCalled()
  expect(screen.getByRole('button', { name: /^error/ })).toHaveTextContent('Installation needs attention')
  expect(screen.getByRole('button', { name: /^not_installed/ })).toHaveTextContent('Available to install')
  expect(screen.getByRole('button', { name: /^disabled/ })).toHaveTextContent('Installed · disabled')
  expect(screen.getByRole('button', { name: /^enabled/ })).toHaveTextContent('Installed · running')
})

test('keyboard selection returns focus to the composer on Escape', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => ({ extensions: [{ id: 'docling', name: 'Docling', installable: true }] }) }))
  const dismiss = vi.fn()
  render(<><textarea aria-label="Composer" /><PortalExtensionMention query="" onSelect={() => {}} onDismiss={dismiss}/></>)
  const field = screen.getByRole('textbox')
  const choice = await screen.findByRole('button', { name: /Docling/ })
  field.focus()
  fireEvent.keyDown(field, { key: 'ArrowDown' })
  expect(choice).toHaveFocus()
  fireEvent.keyDown(choice, { key: 'Escape' })
  await waitFor(() => expect(field).toHaveFocus())
  expect(dismiss).toHaveBeenCalledOnce()
})

test('present built-ins can be reused without download while uninstalled user recipes still require installability', async () => {
  const entries = ['enabled', 'cli_installed', 'disabled', 'stopped'].map(status => ({
    id: status, name: status, status, source: 'builtin', installable: false,
  }))
  entries.push({ id: 'missing-recipe', name: 'Missing recipe', status: 'not_installed', source: 'user', installable: false })
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => ({ extensions: entries }) }))
  const select = vi.fn()
  render(<PortalExtensionMention query="" onSelect={select} onDismiss={() => {}} />)
  for (const status of ['enabled', 'cli_installed', 'disabled', 'stopped']) {
    const button = await screen.findByRole('button', { name: new RegExp(`^${status}`) })
    expect(button).toBeEnabled()
    fireEvent.click(button)
    expect(select).toHaveBeenLastCalledWith(`/extensions @${status} `)
  }
  expect(screen.getByRole('button', { name: /Missing recipe/ })).toBeDisabled()
})

test('slow catalog health checks remain pending beyond eight seconds and still have a bounded timeout', async () => {
  vi.useFakeTimers()
  let signal
  vi.stubGlobal('fetch', vi.fn((_url, options) => new Promise((_resolve, reject) => {
    signal = options.signal
    signal.addEventListener('abort', () => reject(new Error('aborted')), { once: true })
  })))
  const view = render(<PortalExtensionMention query="" onSelect={() => {}} onDismiss={() => {}} />)
  try {
    await act(async () => { await vi.advanceTimersByTimeAsync(9000) })
    expect(signal.aborted).toBe(false)
    expect(screen.getByText('Loading catalog…')).toBeInTheDocument()
    await act(async () => { await vi.advanceTimersByTimeAsync(51000) })
    expect(signal.aborted).toBe(true)
    expect(screen.getByRole('button', { name: /Retry/ })).toBeInTheDocument()
  } finally {
    view.unmount()
    vi.useRealTimers()
  }
})
