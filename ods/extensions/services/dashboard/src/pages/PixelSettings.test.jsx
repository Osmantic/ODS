import { fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes, useLocation, useNavigate, useNavigationType } from 'react-router-dom'
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

function RedirectProbe() {
  const location = useLocation()
  const navigationType = useNavigationType()
  const navigate = useNavigate()
  return <>
    <output data-testid="redirect-location">{location.pathname + location.search + location.hash}</output>
    <output data-testid="redirect-action">{navigationType}</output>
    <button onClick={() => navigate(-1)}>Back</button>
  </>
}

it.each([
  'https://attacker.example/login',
  '//attacker.example/login',
  '\\\\attacker.example/login',
  'javascript:alert(document.domain)',
  'data:text/html,<script>alert(1)</script>',
  '%2f%2fattacker.example/login',
  'access&next=https://attacker.example/',
])('keeps the legacy redirect internal for an untrusted section: %s', async section => {
  const query = new URLSearchParams({section, next: 'https://attacker.example/', token: 'private'})
  render(<MemoryRouter initialEntries={['/before', `/pixel/settings?${query}#https://attacker.example/`]} initialIndex={1}>
    <Routes>
      <Route path="/before" element={<p>Previous safe page</p>}/>
      <Route path="/pixel/settings" element={<PixelSettings/>}/>
      <Route path="/settings" element={<RedirectProbe/>}/>
    </Routes>
  </MemoryRouter>)
  expect(await screen.findByTestId('redirect-location')).toHaveTextContent(/^\/settings\?section=connections$/)
  expect(screen.getByTestId('redirect-action')).toHaveTextContent('REPLACE')
  fireEvent.click(screen.getByRole('button', {name: 'Back'}))
  expect(await screen.findByText('Previous safe page')).toBeVisible()
})
