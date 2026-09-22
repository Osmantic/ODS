import { fireEvent, render, screen } from '@testing-library/react'
import { expect, it, vi } from 'vitest'
import { SuccessValidation } from './SuccessValidation'

it('stops live validation when the component unmounts', async () => {
  let resolve
  vi.stubGlobal('fetch', vi.fn(() => new Promise(done => { resolve = done })))
  const onAllPassed = vi.fn()
  const view = render(<SuccessValidation status={{ services: [] }} onAllPassed={onAllPassed} />)
  fireEvent.click(screen.getByRole('button', { name: 'Run Tests' }))
  view.unmount()
  resolve({ ok: true, json: async () => ({ success: true }) })
  await Promise.resolve()
  expect(onAllPassed).not.toHaveBeenCalled()
  vi.unstubAllGlobals()
})
