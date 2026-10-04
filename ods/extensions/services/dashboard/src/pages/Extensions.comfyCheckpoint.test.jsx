import { expect, it, vi } from 'vitest'
import { fireEvent, screen, waitFor } from '@testing-library/react'
import { render } from '../test/test-utils'
import Extensions from './Extensions' // eslint-disable-line no-unused-vars

const response = (value) => ({ ok: true, json: async () => value })

it('requires a separate size confirmation before ComfyUI downloads a model', async () => {
  const checkpoint = {
    selected: true,
    catalog: { model_id: 'sdxl_lightning_4step', name: 'SDXL Lightning 4-step',
      size_label: '6.94 GB', size_bytes: 6938040682 },
    download: { state: 'idle', bytes_done: 0, bytes_total: 6938040682 },
  }
  const fetchMock = vi.fn(async (url, options = {}) => {
    if (url === '/api/extensions/catalog') return response({
      agent_available: true,
      extensions: [{ id: 'comfyui', name: 'ComfyUI', source: 'core', status: 'enabled',
        library_manageable: true, library_selected: true,
        features: [{ category: 'tools', icon: 'Box' }] }],
      summary: { total: 1, installed: 1 },
    })
    if (url === '/api/webui/selection') return response({ enabled: false, supported: false })
    if (url === '/api/templates') return response({ templates: [] })
    if (url === '/api/extensions/comfyui/checkpoint') return response(checkpoint)
    if (url === '/api/extensions/comfyui/checkpoint/download' && options.method === 'POST') {
      expect(JSON.parse(options.body)).toEqual({
        model_id: 'sdxl_lightning_4step', acknowledge_size_bytes: 6938040682,
      })
      return response({ state: 'downloading', bytes_done: 0, bytes_total: 6938040682 })
    }
    throw new Error(`Unmocked fetch: ${url}`)
  })
  vi.stubGlobal('fetch', fetchMock)
  render(<Extensions compact />)
  fireEvent.click(await screen.findByRole('button', { name: 'Download image model' }))
  expect(screen.getByText(/6,938,040,682 bytes/)).toBeVisible()
  expect(fetchMock).not.toHaveBeenCalledWith('/api/extensions/comfyui/checkpoint/download', expect.anything())
  fireEvent.click(screen.getByRole('button', { name: 'Confirm download' }))
  await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
    '/api/extensions/comfyui/checkpoint/download', expect.objectContaining({ method: 'POST' }),
  ))
})

it('shows automatic checkpoint reverification without a false download cancel action', async () => {
  const fetchMock = vi.fn(async (url) => {
    if (url === '/api/extensions/catalog') return response({
      agent_available: true,
      extensions: [{ id: 'comfyui', name: 'ComfyUI', source: 'core', status: 'enabled',
        library_manageable: true, library_selected: true,
        features: [{ category: 'tools', icon: 'Box' }] }],
      summary: { total: 1, installed: 1 },
    })
    if (url === '/api/webui/selection') return response({ enabled: false, supported: false })
    if (url === '/api/templates') return response({ templates: [] })
    if (url === '/api/extensions/comfyui/checkpoint') return response({
      selected: true,
      catalog: { model_id: 'sdxl_lightning_4step', name: 'SDXL Lightning 4-step',
        size_label: '6.94 GB', size_bytes: 6938040682 },
      download: { state: 'verifying', reverify_only: true, bytes_done: 6938040682,
        bytes_total: 6938040682, error: null },
    })
    throw new Error(`Unmocked fetch: ${url}`)
  })
  vi.stubGlobal('fetch', fetchMock)
  render(<Extensions compact />)
  expect(await screen.findByText('Verifying the model…')).toBeVisible()
  expect(screen.queryByRole('button', { name: 'Cancel download' })).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'Download image model' })).not.toBeInTheDocument()
  expect(screen.queryByText(/ComfyUI can open without a model/)).not.toBeInTheDocument()
})
