import {render, screen, fireEvent, waitFor, act} from '@testing-library/react'
import {ThemeProvider, useTheme} from './ThemeContext'
import CustomWallpaperPicker from '../components/CustomWallpaperPicker'
import {readCustomWallpapers, addCustomWallpaper, deleteCustomWallpaper} from '../lib/customWallpapers'

vi.mock('../lib/customWallpapers', async importOriginal => ({...await importOriginal(), readCustomWallpapers:vi.fn(), addCustomWallpaper:vi.fn(), deleteCustomWallpaper:vi.fn()}))
const row = {id:'custom-11111111-2222-4333-8444-555555555555',name:'My forest',image:'data:image/webp;base64,YQ=='}
function Gallery() {
  const {wallpapers,theme,setTheme} = useTheme()
  return <><output>{theme}</output>{wallpapers.map(item => <button key={item.id} onClick={()=>setTheme(item.id)}>{item.name}</button>)}<CustomWallpaperPicker/></>
}
beforeEach(()=>{localStorage.clear();vi.resetAllMocks();readCustomWallpapers.mockResolvedValue([])})

it('imports, selects, restores and removes a custom image while retaining the default palette',async()=>{
  addCustomWallpaper.mockResolvedValue(row)
  deleteCustomWallpaper.mockResolvedValue(undefined)
  const view=render(<ThemeProvider><Gallery/></ThemeProvider>)
  await act(async()=>{})
  const file=new File(['image'],'forest.jpg',{type:'image/jpeg'})
  fireEvent.change(screen.getByLabelText('Choose local wallpaper'),{target:{files:[file]}})
  expect(await screen.findByRole('button',{name:'My forest'})).toBeVisible()
  expect(addCustomWallpaper).toHaveBeenCalledWith(file)
  expect(document.documentElement).toHaveAttribute('data-theme','ods')
  expect(document.documentElement).toHaveAttribute('data-wallpaper',row.id)
  expect(localStorage.getItem('ods-theme')).toBe(row.id)
  view.unmount()
  readCustomWallpapers.mockResolvedValue([row])
  render(<ThemeProvider><Gallery/></ThemeProvider>)
  await screen.findByRole('button',{name:'My forest'})
  expect(document.documentElement.style.getPropertyValue('--workspace-wallpaper')).toContain(row.image)
  fireEvent.click(screen.getByRole('button',{name:'Remove selected wallpaper'}))
  await waitFor(()=>expect(screen.queryByRole('button',{name:'My forest'})).toBeNull())
  expect(deleteCustomWallpaper).toHaveBeenCalledWith(row.id)
  expect(localStorage.getItem('ods-theme')).toBe('ods')
})

it('keeps the selected preset when storage rejects an import, and allows another attempt',async()=>{
  localStorage.setItem('ods-theme','forest')
  addCustomWallpaper.mockRejectedValue(new Error('Storage full'))
  render(<ThemeProvider><Gallery/></ThemeProvider>)
  await act(async()=>{})
  fireEvent.change(screen.getByLabelText('Choose local wallpaper'),{target:{files:[new File(['x'],'x.png',{type:'image/png'})]}})
  expect(await screen.findByRole('alert')).toHaveTextContent('Storage full')
  expect(document.documentElement).toHaveAttribute('data-wallpaper','forest')
  expect(screen.getByRole('button',{name:'Add wallpaper'})).toBeEnabled()
})

it('falls back if a saved custom image was removed elsewhere',async()=>{
  localStorage.setItem('ods-theme',row.id)
  readCustomWallpapers.mockResolvedValue([row])
  render(<ThemeProvider><Gallery/></ThemeProvider>)
  await screen.findByRole('button',{name:'My forest'})
  readCustomWallpapers.mockResolvedValue([])
  act(()=>window.dispatchEvent(new Event('focus')))
  await waitFor(()=>expect(document.documentElement).not.toHaveAttribute('data-wallpaper'))
  expect(localStorage.getItem('ods-theme')).toBe('ods')
})

it('ignores an older gallery read that completes after a successful import',async()=>{
  let finishRead
  readCustomWallpapers.mockReturnValue(new Promise(resolve=>{finishRead=resolve}))
  addCustomWallpaper.mockResolvedValue(row)
  render(<ThemeProvider><Gallery/></ThemeProvider>)
  fireEvent.change(screen.getByLabelText('Choose local wallpaper'),{target:{files:[new File(['x'],'x.png',{type:'image/png'})]}})
  await screen.findByRole('button',{name:'My forest'})
  await act(async()=>finishRead([]))
  expect(screen.getByRole('button',{name:'My forest'})).toBeVisible()
  expect(document.documentElement).toHaveAttribute('data-wallpaper',row.id)
})

it('imports and restores video backgrounds and saves the pause preference',async()=>{
  const movie={...row,kind:'video',video:new Blob(['video'],{type:'video/mp4'})}
  addCustomWallpaper.mockResolvedValue(movie)
  const view=render(<ThemeProvider><Gallery/></ThemeProvider>)
  await act(async()=>{})
  const input=screen.getByLabelText('Choose local wallpaper')
  expect(input.accept).toContain('video/mp4')
  expect(input.accept).toContain('video/webm')
  fireEvent.change(input,{target:{files:[new File(['v'],'v.mp4',{type:'video/mp4'})]}})
  const toggle=await screen.findByRole('checkbox',{name:'Animate video background'})
  expect(document.documentElement).toHaveAttribute('data-wallpaper-kind','video')
  fireEvent.click(toggle)
  expect(localStorage.getItem('ods-wallpaper-motion')).toBe('paused')
  view.unmount()
  readCustomWallpapers.mockResolvedValue([movie])
  render(<ThemeProvider><Gallery/></ThemeProvider>)
  expect(await screen.findByRole('checkbox',{name:'Animate video background'})).not.toBeChecked()
  fireEvent.click(screen.getByRole('button',{name:'Remove selected wallpaper'}))
  await waitFor(()=>expect(document.documentElement).not.toHaveAttribute('data-wallpaper-kind'))
})
