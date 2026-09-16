import {act, cleanup, fireEvent, render, screen} from '@testing-library/react'
import InstallPromptBanner from './InstallPromptBanner'

const INSTALLED = 'ods-pwa-installed'
const banner = () => screen.queryByRole('dialog', {name:'Add ODS to your home screen'})
function installable() {
  const event = new Event('beforeinstallprompt', {cancelable:true})
  event.prompt = vi.fn().mockResolvedValue()
  event.userChoice = Promise.resolve({outcome:'accepted'})
  act(() => window.dispatchEvent(event))
  return event
}
beforeEach(() => {
  localStorage.clear(); sessionStorage.clear()
  localStorage.setItem('ods-pwa-visit-count','5')
  vi.stubGlobal('matchMedia',vi.fn(() => ({matches:false})))
})
afterEach(() => {cleanup();vi.restoreAllMocks();vi.unstubAllGlobals()})

test('a fresh browser install offer supersedes the retained installed marker after reload', async () => {
  localStorage.setItem(INSTALLED,'1')
  render(<InstallPromptBanner/>)
  expect(banner()).toBeNull()
  const event = installable()
  expect(banner()).toBeInTheDocument()
  expect(event.defaultPrevented).toBe(true)
  expect(localStorage.getItem(INSTALLED)).not.toBe('1')
  await act(async () => fireEvent.click(screen.getByRole('button',{name:'Add to home screen'})))
  expect(event.prompt).toHaveBeenCalledOnce()
  expect(localStorage.getItem(INSTALLED)).toBe('1')
  expect(banner()).toBeNull()
})

test('an open tab can receive another install offer after an appinstalled event', () => {
  render(<InstallPromptBanner/>)
  installable()
  expect(banner()).toBeInTheDocument()
  act(() => window.dispatchEvent(new Event('appinstalled')))
  expect(banner()).toBeNull()
  expect(localStorage.getItem(INSTALLED)).toBe('1')
  installable()
  expect(banner()).toBeInTheDocument()
  expect(localStorage.getItem(INSTALLED)).not.toBe('1')
})

test('a fresh install offer still respects an explicit dismissal', () => {
  localStorage.setItem(INSTALLED,'1')
  localStorage.setItem('ods-pwa-prompt-dismissed','1')
  render(<InstallPromptBanner/>)
  installable()
  expect(banner()).toBeNull()
  expect(localStorage.getItem('ods-pwa-prompt-dismissed')).toBe('1')
})

test('a standalone app stays installed even if it receives an install offer', () => {
  window.matchMedia.mockReturnValue({matches:true})
  localStorage.setItem(INSTALLED,'1')
  render(<InstallPromptBanner/>)
  installable()
  expect(banner()).toBeNull()
  expect(localStorage.getItem(INSTALLED)).toBe('1')
})

test('a denied session-storage getter does not prevent accepting a fresh install offer', async () => {
  vi.spyOn(globalThis, 'sessionStorage', 'get').mockImplementation(() => {
    throw new window.DOMException('Storage blocked', 'SecurityError')
  })
  render(<InstallPromptBanner />)
  const event = installable()
  expect(banner()).toBeInTheDocument()
  await act(async () => fireEvent.click(screen.getByRole('button', { name: 'Add to home screen' })))
  expect(event.prompt).toHaveBeenCalledOnce()
  expect(banner()).toBeNull()
})

test('a denied local-storage getter does not crash the rendered banner or install events', () => {
  vi.spyOn(globalThis, 'localStorage', 'get').mockImplementation(() => {
    throw new window.DOMException('Storage blocked', 'SecurityError')
  })
  render(<InstallPromptBanner />)
  installable()
  act(() => window.dispatchEvent(new Event('appinstalled')))
  expect(banner()).toBeNull()
})
