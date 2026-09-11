import {render, screen, fireEvent, act, cleanup} from '@testing-library/react'
import {ThemeProvider, useTheme} from './ThemeContext'
import WallpaperVideo from '../components/WallpaperVideo'
import {readCustomWallpapers} from '../lib/customWallpapers'

vi.mock('../lib/customWallpapers', async original => ({...await original(),readCustomWallpapers:vi.fn()}))
const id = 'custom-11111111-2222-4333-8444-555555555555'
const movie = () => ({id,name:'Movie',kind:'video',image:'data:image/webp;base64,YQ==',video:new Blob(['movie'],{type:'video/webm'})})
function Stage() {
  const {wallpapers,setTheme} = useTheme()
  return <><WallpaperVideo/>{wallpapers.map(row => <button key={row.id} onClick={()=>setTheme(row.id)}>{row.name}</button>)}</>
}
beforeEach(()=>{
  localStorage.clear()
  localStorage.setItem('ods-theme',id)
  readCustomWallpapers.mockReset().mockImplementation(async()=>[movie()])
  vi.stubGlobal('matchMedia',()=>({matches:false,addEventListener:vi.fn(),removeEventListener:vi.fn()}))
  vi.stubGlobal('URL',Object.assign(class extends URL {},{createObjectURL:vi.fn(()=>`blob:movie`),revokeObjectURL:vi.fn()}))
  vi.spyOn(window.HTMLMediaElement.prototype,'play').mockResolvedValue(undefined)
  vi.spyOn(window.HTMLMediaElement.prototype,'pause').mockImplementation(()=>{})
})
afterEach(()=>{cleanup();vi.restoreAllMocks();vi.unstubAllGlobals()})

it('keeps the playing resource on focus refresh but releases it when storage removes the video',async()=>{
  const {container}=render(<ThemeProvider><Stage/></ThemeProvider>)
  await screen.findByRole('button',{name:'Movie'})
  const video=container.querySelector('video')
  video.currentTime=12
  await act(async()=>window.dispatchEvent(new Event('focus')))
  expect(readCustomWallpapers).toHaveBeenCalledTimes(2)
  expect(URL.createObjectURL).toHaveBeenCalledTimes(1)
  expect(URL.revokeObjectURL).not.toHaveBeenCalled()
  expect(container.querySelector('video')).toBe(video)
  expect(video.currentTime).toBe(12)
  readCustomWallpapers.mockResolvedValue([])
  await act(async()=>window.dispatchEvent(new Event('focus')))
  expect(container.querySelector('video')).toBeNull()
  expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:movie')
  expect(localStorage.getItem('ods-theme')).toBe('ods')
})

it('still discovers and selects newly added records after a refresh',async()=>{
  render(<ThemeProvider><Stage/></ThemeProvider>)
  await screen.findByRole('button',{name:'Movie'})
  const image={id:'custom-aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee',name:'New image',image:'data:image/png;base64,Yg=='}
  readCustomWallpapers.mockResolvedValue([movie(),image])
  await act(async()=>window.dispatchEvent(new Event('focus')))
  fireEvent.click(screen.getByRole('button',{name:'New image'}))
  expect(document.documentElement).toHaveAttribute('data-wallpaper',image.id)
  expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:movie')
})
