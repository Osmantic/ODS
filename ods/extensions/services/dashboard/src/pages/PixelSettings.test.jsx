import { render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import PixelSettings from './PixelSettings'

function Destination() { return <p>{useLocation().search}</p> }
it.each(['access', 'sharing', 'connections', 'pixel-diagnostics'])('preserves the %s section in the ODS settings panel', async section => {
  render(<MemoryRouter initialEntries={[`/pixel/settings?section=${section}`]}><Routes>
    <Route path="/pixel/settings" element={<PixelSettings/>}/><Route path="/settings" element={<Destination/>}/>
  </Routes></MemoryRouter>)
  expect(await screen.findByText(`?section=${section}`)).toBeInTheDocument()
})
it('uses a fixed default instead of forwarding arbitrary query parameters', async () => {
  render(<MemoryRouter initialEntries={['/pixel/settings?section=https://example.com&token=private']}><Routes>
    <Route path="/pixel/settings" element={<PixelSettings/>}/><Route path="/settings" element={<Destination/>}/>
  </Routes></MemoryRouter>)
  expect(await screen.findByText('?section=connections')).toBeInTheDocument()
})
