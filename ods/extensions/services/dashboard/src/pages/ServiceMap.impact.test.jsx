import { createElement } from 'react'
import { fireEvent, render, screen, within } from '@testing-library/react'
import ServiceMap from './ServiceMap'

test('traces direct and indirect affected services from the live status map', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, json: async () => ({ services: [
    { id: 'llama-server', name: 'Inference', status: 'healthy' },
    { id: 'litellm', name: 'Gateway', status: 'healthy' },
    { id: 'open-webui', name: 'Chat', status: 'healthy' },
    { id: 'whisper', name: 'Voice', status: 'healthy' },
  ] }) })))
  render(createElement(ServiceMap))
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
