import { act, fireEvent, screen, waitFor } from '@testing-library/react'
import { render } from '../../test/test-utils'
import { TemplatePreview } from '../TemplatePicker' // eslint-disable-line no-unused-vars

const template = {
  id: 'test-template',
  name: 'Test Template',
  description: 'Template apply contract',
  services: ['svc-a'],
}

const response = body => ({
  ok: true,
  status: 200,
  json: async () => body,
})

describe('TemplatePreview apply result', () => {
  afterEach(() => {
    vi.useRealTimers()
    vi.restoreAllMocks()
  })

  test('keeps a long-running apply request alive beyond two minutes', async () => {
    let applySignal
    let finishApply
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(response({
        changes: { to_enable: ['svc-a'], already_enabled: [], incompatible: [] },
        warnings: [],
      }))
      .mockImplementationOnce((_url, options) => {
        applySignal = options.signal
        return new Promise(resolve => { finishApply = resolve })
      })
    vi.stubGlobal('fetch', fetchMock)

    render(<TemplatePreview template={template} onClose={vi.fn()} />)
    const applyButton = await screen.findByRole('button', { name: /apply template/i })
    vi.useFakeTimers()
    fireEvent.click(applyButton)

    await act(async () => {
      await vi.advanceTimersByTimeAsync(120001)
    })
    expect(applySignal.aborted).toBe(false)
    expect(screen.queryByText(/request timed out/i)).not.toBeInTheDocument()

    await act(async () => {
      finishApply(response({
        enabled_count: 1,
        started_count: 1,
        failed_services: [],
        skipped_services: [],
        warnings: [],
        restart_required: false,
      }))
      await Promise.resolve()
    })
    vi.useRealTimers()
    expect(await screen.findByText(/template applied/i)).toBeInTheDocument()
  })

  test('aborts a stale preview when the selected template changes', async () => {
    let firstSignal
    const fetchMock = vi.fn()
      .mockImplementationOnce((_url, options) => {
        firstSignal = options.signal
        return new Promise((_, reject) => {
          options.signal.addEventListener('abort', () => {
            const error = new Error('Aborted')
            error.name = 'AbortError'
            reject(error)
          }, { once: true })
        })
      })
      .mockResolvedValueOnce(response({
        changes: { to_enable: [], already_enabled: ['svc-b'], incompatible: [] },
        warnings: [],
      }))
    vi.stubGlobal('fetch', fetchMock)

    const { rerender } = render(
      <TemplatePreview template={template} onClose={vi.fn()} />,
    )
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1))

    rerender(
      <TemplatePreview
        template={{ ...template, id: 'next-template', services: ['svc-b'] }}
        onClose={vi.fn()}
      />,
    )

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2))
    expect(firstSignal.aborted).toBe(true)
    expect(await screen.findByText('svc-b')).toBeInTheDocument()
  })

  test('does not report all services active when apply skipped a service', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(response({
        changes: { to_enable: ['svc-a'], already_enabled: [], incompatible: [] },
        warnings: [],
      }))
      .mockResolvedValueOnce(response({
        enabled_count: 0,
        started_count: 0,
        failed_services: [],
        skipped_services: ['svc-a'],
        warnings: ['svc-a: incompatible GPU backend'],
        restart_required: false,
      }))
    vi.stubGlobal('fetch', fetchMock)

    render(<TemplatePreview template={template} onClose={vi.fn()} />)

    const applyButton = await screen.findByRole('button', { name: /apply template/i })
    fireEvent.click(applyButton)

    expect(await screen.findByText(/applied with exceptions/i)).toBeInTheDocument()
    expect(screen.getByText(/skipped: svc-a/i)).toBeInTheDocument()
    expect(screen.queryByText(/all services.*already active/i)).not.toBeInTheDocument()
  })

  test('does not replace the current preview with a late old response body', async () => {
    let finishOld
    vi.stubGlobal('fetch', vi.fn()
      .mockResolvedValueOnce({ ok: true, json: () => new Promise(resolve => { finishOld = resolve }) })
      .mockResolvedValueOnce(response({ changes: { to_enable: ['current-service'] }, warnings: [] })))
    const { rerender } = render(<TemplatePreview template={template} onClose={vi.fn()} />)
    await waitFor(() => expect(finishOld).toBeTypeOf('function'))
    rerender(<TemplatePreview template={{ ...template, id: 'current' }} onClose={vi.fn()} />)
    expect(await screen.findByText('current-service')).toBeVisible()
    await act(async () => { finishOld({ changes: { to_enable: ['obsolete-service'] } }) })
    expect(screen.queryByText('obsolete-service')).not.toBeInTheDocument()
    expect(screen.getByText('current-service')).toBeVisible()
  })

  test('shows targeted restart recovery when a service failed to start', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(response({
        changes: { to_enable: ['svc-a'], already_enabled: [], incompatible: [] },
        warnings: [],
      }))
      .mockResolvedValueOnce(response({
        enabled_count: 1,
        started_count: 0,
        failed_services: ['svc-a'],
        skipped_services: [],
        warnings: [],
        restart_required: true,
      }))
    vi.stubGlobal('fetch', fetchMock)

    render(<TemplatePreview template={template} onClose={vi.fn()} />)
    fireEvent.click(await screen.findByRole('button', { name: /apply template/i }))

    await waitFor(() => expect(screen.getByText(/failed to start: svc-a/i)).toBeInTheDocument())
    expect(screen.getByText(/run ods restart to retry/i)).toBeInTheDocument()
  })
})
