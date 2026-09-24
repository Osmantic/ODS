import {fireEvent, render, screen} from '@testing-library/react'
import {useTheme} from '../contexts/ThemeContext'
import CustomWallpaperPicker from './CustomWallpaperPicker'
vi.mock('../contexts/ThemeContext', () => ({useTheme:vi.fn()}))
const id = 'custom-'+'a'.repeat(36)
const stored = {id, name:'../../private name', image:'data:image/png;base64,AQID'}
let theme, saved, link
const bytes = blob => new Promise(resolve => {const reader = new globalThis.FileReader(); reader.onload = () => resolve(new Uint8Array(reader.result)); reader.readAsArrayBuffer(blob)})
beforeEach(() => {
  theme = {theme:id, wallpapers:[stored], addWallpaper:vi.fn(), removeWallpaper:vi.fn()}
  useTheme.mockImplementation(() => theme)
  vi.stubGlobal('fetch', vi.fn())
  vi.stubGlobal('URL', Object.assign(URL, {createObjectURL:vi.fn(blob => {saved=blob; return 'blob:saved-wallpaper'}), revokeObjectURL:vi.fn()}))
  vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function () {link=this})
})
afterEach(() => {vi.useRealTimers(); vi.restoreAllMocks(); vi.unstubAllGlobals()})
test('downloads exactly the stored image with a fixed safe filename and no network or storage writes', async () => {
  render(<CustomWallpaperPicker/>)
  fireEvent.click(screen.getByRole('button', {name:'Download saved wallpaper'}))
  expect([...await bytes(saved)]).toEqual([1,2,3])
  expect(saved.type).toBe('image/png')
  expect(link.download).toBe(`ods-wallpaper-${'a'.repeat(36)}.png`)
  expect(link.isConnected).toBe(false)
  expect(fetch).not.toHaveBeenCalled()
  expect(theme.addWallpaper).not.toHaveBeenCalled()
  expect(theme.removeWallpaper).not.toHaveBeenCalled()
  expect(screen.getByText(/Images download as the resized browser copy/)).toBeVisible()
})
test('downloads the full stored video rather than its thumbnail and defers URL release', () => {
  vi.useFakeTimers()
  const video = new Blob(['video bytes'], {type:'video/webm'})
  theme.wallpapers = [{...stored, kind:'video', video}]
  render(<CustomWallpaperPicker/>)
  fireEvent.click(screen.getByRole('button', {name:'Download saved wallpaper'}))
  expect(saved).toBe(video)
  expect(link.download).toMatch(/\.webm$/)
  expect(URL.revokeObjectURL).not.toHaveBeenCalled()
  vi.advanceTimersByTime(1000)
  expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:saved-wallpaper')
})
test('does not offer built-in, absent or malformed saved media', () => {
  theme.theme = 'ods'
  const view = render(<CustomWallpaperPicker/>)
  expect(screen.queryByRole('button', {name:'Download saved wallpaper'})).toBeNull()
  theme.theme = id
  theme.wallpapers = [{...stored, image:'https://remote.invalid/image.png'}]
  view.rerender(<CustomWallpaperPicker/>)
  expect(screen.queryByRole('button', {name:'Download saved wallpaper'})).toBeNull()
})
test('reports a browser failure, releases resources, and resets receipts on selection change', () => {
  const click = HTMLAnchorElement.prototype.click
  click.mockImplementationOnce(function () {link=this; throw new Error('blocked')})
  const view = render(<CustomWallpaperPicker/>)
  fireEvent.click(screen.getByRole('button', {name:'Download saved wallpaper'}))
  expect(screen.getByRole('alert')).toHaveTextContent('could not start')
  expect(link.isConnected).toBe(false)
  fireEvent.click(screen.getByRole('button', {name:'Download saved wallpaper'}))
  expect(screen.getByRole('status')).toHaveTextContent('download started')
  const next = {...stored, id:'custom-'+'b'.repeat(36)}
  theme = {...theme, theme:next.id, wallpapers:[next]}
  view.rerender(<CustomWallpaperPicker/>)
  expect(screen.queryByRole('status')).toBeNull()
})
