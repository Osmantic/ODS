import {readFileSync} from 'node:fs'
import {act, cleanup, fireEvent, render, screen, waitFor} from '@testing-library/react'
import {ThemeProvider} from './ThemeContext'
import CustomWallpaperPicker from '../components/CustomWallpaperPicker'
import WallpaperVideo from '../components/WallpaperVideo'
import {readCustomWallpapers} from '../lib/customWallpapers'

vi.mock('../lib/customWallpapers', async original => ({...await original(), readCustomWallpapers:vi.fn()}))
const styles = readFileSync('src/wallpaper-themes.css','utf8')
beforeEach(() => {
  localStorage.clear()
  localStorage.setItem('ods-theme','forest')
  readCustomWallpapers.mockResolvedValue([])
})
afterEach(() => {cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals()})
function stage() {
  return render(<ThemeProvider><style>{styles}</style><CustomWallpaperPicker/><WallpaperVideo/><div id="root"><div className="pixel-app"><main className="pixel-workspace dashboard-market-shell"/></div></div></ThemeProvider>)
}

test('applies the real fit selector to both wallpaper surfaces, persists it and restores fill', async () => {
  const {container,unmount} = stage()
  const select = screen.getByRole('combobox', {name:'Wallpaper fit'})
  expect(select).toHaveValue('cover')
  fireEvent.change(select, {target:{value:'contain'}})
  expect(localStorage.getItem('ods-wallpaper-fit')).toBe('contain')
  expect(window.getComputedStyle(container.querySelector('.pixel-app')).backgroundSize).toBe('cover, contain')
  expect(window.getComputedStyle(container.querySelector('main')).backgroundSize).toBe('cover, cover, contain')
  expect(window.getComputedStyle(container.querySelector('main')).backgroundRepeat).toBe('no-repeat')
  expect(localStorage.getItem('ods-theme')).toBe('forest')
  unmount()
  stage()
  expect(screen.getByRole('combobox', {name:'Wallpaper fit'})).toHaveValue('contain')
  fireEvent.change(screen.getByRole('combobox', {name:'Wallpaper fit'}), {target:{value:'cover'}})
  expect(document.documentElement).toHaveAttribute('data-wallpaper-fit','cover')
  await waitFor(() => expect(readCustomWallpapers).toHaveBeenCalledTimes(2))
})

test('reconciles queued cross-tab events from current storage and resets invalid/cleared preferences', () => {
  stage()
  localStorage.setItem('ods-wallpaper-fit','contain')
  act(() => window.dispatchEvent(new StorageEvent('storage',{key:'ods-wallpaper-fit',newValue:'cover'})))
  expect(screen.getByRole('combobox', {name:'Wallpaper fit'})).toHaveValue('contain')
  localStorage.setItem('ods-wallpaper-fit','stretch')
  act(() => window.dispatchEvent(new StorageEvent('storage',{key:'ods-wallpaper-fit',newValue:'contain'})))
  expect(screen.getByRole('combobox', {name:'Wallpaper fit'})).toHaveValue('cover')
  fireEvent.change(screen.getByRole('combobox', {name:'Wallpaper fit'}), {target:{value:'contain'}})
  localStorage.clear()
  act(() => window.dispatchEvent(new StorageEvent('storage',{key:null})))
  expect(screen.getByRole('combobox', {name:'Wallpaper fit'})).toHaveValue('cover')
})

test('keeps a playing video resource while changing its object fit', async () => {
  const id = 'custom-11111111-2222-4333-8444-555555555555'
  localStorage.setItem('ods-theme',id)
  readCustomWallpapers.mockResolvedValue([{id,name:'Video',kind:'video',image:'data:image/webp;base64,YQ==',video:new Blob(['video'],{type:'video/webm'})}])
  vi.stubGlobal('matchMedia',() => ({matches:false,addEventListener:vi.fn(),removeEventListener:vi.fn()}))
  vi.stubGlobal('URL',Object.assign(class extends URL {},{createObjectURL:vi.fn(() => 'blob:fit-video'),revokeObjectURL:vi.fn()}))
  vi.spyOn(window.HTMLMediaElement.prototype,'play').mockResolvedValue()
  vi.spyOn(window.HTMLMediaElement.prototype,'pause').mockImplementation(() => {})
  const {container} = stage()
  await waitFor(() => expect(container.querySelector('video')).not.toBeNull())
  const video = container.querySelector('video')
  video.currentTime = 12
  fireEvent.change(screen.getByRole('combobox', {name:'Wallpaper fit'}), {target:{value:'contain'}})
  expect(container.querySelector('video')).toBe(video)
  expect(window.getComputedStyle(video).objectFit).toBe('contain')
  expect(window.getComputedStyle(container.querySelector('main')).backgroundColor).not.toBe('rgb(8, 12, 18)')
  expect(video.currentTime).toBe(12)
  expect(URL.createObjectURL).toHaveBeenCalledOnce()
  expect(URL.revokeObjectURL).not.toHaveBeenCalled()
})

test('keeps the tab preference and explains a failed persistence write', () => {
  stage()
  vi.spyOn(Storage.prototype,'setItem').mockImplementation(() => {throw new globalThis.DOMException('Full','QuotaExceededError')})
  fireEvent.change(screen.getByRole('combobox', {name:'Wallpaper fit'}), {target:{value:'contain'}})
  expect(screen.getByRole('combobox', {name:'Wallpaper fit'})).toHaveValue('contain')
  expect(screen.getByRole('alert')).toHaveTextContent('could not be saved')
})
