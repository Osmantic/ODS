import {render, act, fireEvent} from '@testing-library/react'
import WallpaperVideo from './WallpaperVideo'
import {useTheme} from '../contexts/ThemeContext'
const {HTMLMediaElement} = window
vi.mock('../contexts/ThemeContext', () => ({useTheme:vi.fn()}))
let media, theme
beforeEach(() => {
  theme={theme:'custom-test',wallpaperMotion:true,wallpapers:[{id:'custom-test',kind:'video',video:new Blob(['v'],{type:'video/webm'}),image:'data:image/webp;base64,YQ=='}]}
  useTheme.mockImplementation(() => theme)
  media={matches:false,addEventListener:vi.fn(),removeEventListener:vi.fn()}
  vi.stubGlobal('matchMedia',vi.fn(() => media))
  vi.stubGlobal('URL',Object.assign(class extends URL {},{createObjectURL:vi.fn(() => 'blob:local-video'),revokeObjectURL:vi.fn()}))
  vi.spyOn(HTMLMediaElement.prototype,'play').mockResolvedValue(undefined)
  vi.spyOn(HTMLMediaElement.prototype,'pause').mockImplementation(() => {})
})
afterEach(() => {vi.restoreAllMocks();vi.unstubAllGlobals()})

it('loops silently, pauses in a hidden tab, and releases its source on theme change', async () => {
  const view=render(<WallpaperVideo/>), video=view.container.querySelector('video')
  await act(async () => {})
  expect(video.muted).toBe(true)
  expect(video.loop).toBe(true)
  expect(video.playsInline).toBe(true)
  expect(video).toHaveAttribute('aria-hidden','true')
  expect(video.play).toHaveBeenCalled()
  vi.spyOn(document,'hidden','get').mockReturnValue(true)
  act(() => document.dispatchEvent(new Event('visibilitychange')))
  expect(video.pause).toHaveBeenCalled()
  theme={...theme,theme:'ods'}
  view.rerender(<WallpaperVideo/>)
  expect(view.container.querySelector('video')).toBeNull()
  expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:local-video')
})

it('respects reduced motion and the explicit pause setting', () => {
  media.matches=true
  const view=render(<WallpaperVideo/>)
  expect(HTMLMediaElement.prototype.play).not.toHaveBeenCalled()
  media.matches=false
  theme={...theme,wallpaperMotion:false}
  view.rerender(<WallpaperVideo/>)
  expect(HTMLMediaElement.prototype.play).not.toHaveBeenCalled()
})

it('falls back to the theme poster when decoding or autoplay fails', async () => {
  const view=render(<WallpaperVideo/>)
  fireEvent.error(view.container.querySelector('video'))
  expect(view.container.querySelector('video')).toBeNull()
  view.unmount()
  expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:local-video')
  HTMLMediaElement.prototype.play.mockRejectedValue(new Error('Autoplay unavailable'))
  const retry=render(<WallpaperVideo/>)
  await act(async () => {})
  expect(retry.container.querySelector('video')).toBeNull()
})

it('resumes video after hiding the tab interrupts a pending play', async () => {
  let rejectPlay
  HTMLMediaElement.prototype.play.mockImplementationOnce(() => new Promise((_, reject) => { rejectPlay = reject }))
  const hidden = vi.spyOn(document, 'hidden', 'get').mockReturnValue(false)
  const view = render(<WallpaperVideo/>)
  hidden.mockReturnValue(true)
  fireEvent(document, new Event('visibilitychange'))
  await act(async () => { rejectPlay(new DOMException('Playback interrupted by pause', 'AbortError')) })
  expect(view.container.querySelector('video')).not.toBeNull()
  hidden.mockReturnValue(false)
  fireEvent(document, new Event('visibilitychange'))
  expect(HTMLMediaElement.prototype.play).toHaveBeenCalledTimes(2)
})
