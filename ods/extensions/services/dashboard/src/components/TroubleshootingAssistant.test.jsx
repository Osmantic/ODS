import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { TroubleshootingAssistant } from './TroubleshootingAssistant'

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })

it('reports denied clipboard access for troubleshooting commands', async () => {
  vi.stubGlobal('navigator', { clipboard: { writeText: vi.fn().mockRejectedValue(new Error('denied')) } })
  render(<TroubleshootingAssistant serviceStatus={{ services: [] }} />)
  fireEvent.click(screen.getByRole('button', { name: /port already in use/i }))
  const copy = screen.getByRole('button', { name: /copy find and stop/i })
  fireEvent.click(copy)
  await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent(/clipboard access failed/i))
})
