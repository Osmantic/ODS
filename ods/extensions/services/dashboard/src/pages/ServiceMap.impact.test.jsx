import { createElement } from 'react'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import ServiceMap from './ServiceMap'

test.each([false, true])('traces direct and indirect affected services from the live status map (compact=%s)', async (compact) => {
  vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, json: async () => ({ services: [
    { id: 'llama-server', name: 'Inference', status: 'healthy' },
    { id: 'litellm', name: 'Gateway', status: 'healthy' },
    { id: 'open-webui', name: 'Chat', status: 'healthy' },
    { id: 'whisper', name: 'Voice', status: 'healthy' },
  ] }) })))
  render(createElement(ServiceMap, { compact }))
  const selector = await screen.findByLabelText('Service to inspect')
  fireEvent.change(selector, { target: { value: 'llama-server' } })
  const affected = within(screen.getByRole('list', { name: 'Affected services' }))
  expect(affected.getByText('Gateway → Inference')).toBeInTheDocument()
  expect(affected.getByText('Chat → Gateway → Inference')).toBeInTheDocument()
  expect(affected.queryByText('Voice')).not.toBeInTheDocument()
  fireEvent.change(selector, { target: { value: 'open-webui' } })
  expect(screen.getByText('No dependent services are shown in this map.')).toBeInTheDocument()
  fireEvent.change(selector, { target: { value: '' } })
  expect(screen.queryByText(/potentially affected/)).not.toBeInTheDocument()
})

test('clears a removed impact selection when the compact snapshot refreshes', async () => {
  let services = [
    { id: 'llama-server', name: 'Inference', status: 'healthy' },
    { id: 'litellm', name: 'Gateway', status: 'healthy' },
  ]
  vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, json: async () => ({ services }) })))
  render(createElement(ServiceMap, { compact: true }))
  fireEvent.change(await screen.findByLabelText('Service to inspect'), { target: { value: 'llama-server' } })
  expect(screen.getByRole('list', { name: 'Affected services' })).toBeInTheDocument()
  services = [services[1]]
  fireEvent.click(screen.getByRole('button', { name: 'Refresh integrations' }))
  await waitFor(() => expect(screen.getByLabelText('Service to inspect')).toHaveValue(''))
  expect(screen.queryByRole('list', { name: 'Affected services' })).not.toBeInTheDocument()
})
