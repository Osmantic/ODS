import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import PortalResponseError from './PortalResponseError'

describe('PortalResponseError', () => {
  it('announces a failed response as an alert', () => {
    render(<PortalResponseError content="Request failed" />)
    expect(screen.getByRole('alert')).toHaveTextContent('The response could not be received')
  })
})
