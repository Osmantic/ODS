import {render,screen,fireEvent,cleanup,waitFor} from '@testing-library/react'
import {afterEach,beforeEach,expect,it,vi} from 'vitest'
import ProfileSettings from './ProfileSettings'
import UserAvatar from '../UserAvatar'
import {useLocalProfile,saveProfile,readProfile,normalizeProfile,prepareProfilePhoto,PROFILE_KEY} from '../../lib/localProfile'

beforeEach(()=>localStorage.removeItem(PROFILE_KEY))
afterEach(()=>{cleanup();vi.restoreAllMocks();vi.unstubAllGlobals();localStorage.removeItem(PROFILE_KEY)})
const PHOTO = 'data:image/webp;base64,YWJj'
function LiveAvatar() {return <UserAvatar profile={useLocalProfile()}/>}

it('saves the name locally and updates other consumers without a reload',()=>{
  render(<><ProfileSettings/><LiveAvatar/></>)
  fireEvent.change(screen.getByRole('textbox',{name:'Display name'}),{target:{value:'Gabriel Silva'}})
  fireEvent.click(screen.getByRole('button',{name:'Save profile'}))
  expect(screen.getByRole('status')).toHaveTextContent('Profile saved.')
  expect(readProfile()).toEqual({name:'Gabriel Silva',photo:''})
  expect(screen.getAllByRole('img',{name:'Gabriel Silva avatar'})).toHaveLength(2)
  cleanup()
  render(<ProfileSettings/>)
  expect(screen.getByRole('textbox',{name:'Display name'})).toHaveValue('Gabriel Silva')
})

it('crops an upload locally, saves it, and can remove it',async()=>{
  const close=vi.fn(),drawImage=vi.fn()
  vi.stubGlobal('createImageBitmap',vi.fn(async()=>({width:600,height:400,close})))
  vi.spyOn(HTMLCanvasElement.prototype,'getContext').mockReturnValue({drawImage})
  vi.spyOn(HTMLCanvasElement.prototype,'toDataURL').mockReturnValue(PHOTO)
  render(<><ProfileSettings/><LiveAvatar/></>)
  fireEvent.change(screen.getByLabelText('Profile photo'),{target:{files:[new File(['image'],'portrait.png',{type:'image/png'})]}})
  await waitFor(()=>expect(screen.getByRole('button',{name:'Save profile'})).toBeEnabled())
  expect(drawImage).toHaveBeenCalledWith(expect.anything(),100,0,400,400,0,0,256,256)
  expect(close).toHaveBeenCalledTimes(1)
  fireEvent.click(screen.getByRole('button',{name:'Save profile'}))
  expect(screen.getAllByRole('img',{name:'You profile photo'})).toHaveLength(2)
  expect(readProfile().photo).toBe(PHOTO)
  fireEvent.click(screen.getByRole('button',{name:'Remove photo'}))
  fireEvent.click(screen.getByRole('button',{name:'Save profile'}))
  expect(readProfile().photo).toBe('')
  expect(screen.queryByRole('img',{name:'You profile photo'})).toBeNull()
})

it('rejects SVG, remote URLs, invalid data, and oversized uploads',async()=>{
  expect(normalizeProfile({name:'  A  ',photo:'https://example.com/photo.jpg'})).toEqual({name:'A',photo:''})
  expect(normalizeProfile({photo:'data:image/svg+xml;base64,YWJj'}).photo).toBe('')
  await expect(prepareProfilePhoto({type:'image/svg+xml',size:5})).rejects.toThrow('JPG, PNG or WebP')
  await expect(prepareProfilePhoto({type:'image/png',size:6*1024*1024})).rejects.toThrow('5 MB')
  expect(normalizeProfile({name:'a'.repeat(100)}).name).toHaveLength(60)
})

it('reports persistence failure without claiming success or replacing a saved profile',()=>{
  saveProfile({name:'Original'})
  render(<ProfileSettings/>)
  fireEvent.change(screen.getByRole('textbox',{name:'Display name'}),{target:{value:'New'}})
  vi.spyOn(Storage.prototype,'setItem').mockImplementation(()=>{throw new Error('quota')})
  fireEvent.click(screen.getByRole('button',{name:'Save profile'}))
  expect(screen.getByRole('alert')).toHaveTextContent('could not be saved')
  expect(screen.queryByRole('status')).toBeNull()
  expect(readProfile().name).toBe('Original')
})

it('refreshes other tabs when the saved profile changes',()=>{
  render(<LiveAvatar/>)
  localStorage.setItem(PROFILE_KEY,JSON.stringify({name:'Ana'}))
  fireEvent(window,new StorageEvent('storage',{key:PROFILE_KEY}))
  expect(screen.getByRole('img',{name:'Ana avatar'})).toBeVisible()
})

it('preserves an unsaved name while accepting a photo saved in another tab',()=>{
  saveProfile({name:'Original'})
  render(<ProfileSettings/>)
  fireEvent.change(screen.getByRole('textbox',{name:'Display name'}),{target:{value:'My unsaved name'}})
  localStorage.setItem(PROFILE_KEY,JSON.stringify({name:'Other tab',photo:PHOTO}))
  fireEvent(window,new StorageEvent('storage',{key:PROFILE_KEY}))
  expect(screen.getByRole('textbox',{name:'Display name'})).toHaveValue('My unsaved name')
  expect(screen.getByRole('img',{name:'My unsaved name profile photo'})).toBeVisible()
  fireEvent.click(screen.getByRole('button',{name:'Save profile'}))
  expect(readProfile()).toEqual({name:'My unsaved name',photo:PHOTO})
  localStorage.setItem(PROFILE_KEY,JSON.stringify({name:'Latest saved name',photo:PHOTO}))
  fireEvent(window,new StorageEvent('storage',{key:PROFILE_KEY}))
  expect(screen.getByRole('textbox',{name:'Display name'})).toHaveValue('Latest saved name')
})

it('preserves an unsaved photo removal while synchronizing an untouched name',()=>{
  saveProfile({name:'Original',photo:PHOTO})
  render(<ProfileSettings/>)
  fireEvent.click(screen.getByRole('button',{name:'Remove photo'}))
  localStorage.setItem(PROFILE_KEY,JSON.stringify({name:'Updated elsewhere',photo:PHOTO}))
  fireEvent(window,new StorageEvent('storage',{key:PROFILE_KEY}))
  expect(screen.queryByRole('button',{name:'Remove photo'})).toBeNull()
  expect(screen.getByRole('textbox',{name:'Display name'})).toHaveValue('Updated elsewhere')
  fireEvent.click(screen.getByRole('button',{name:'Save profile'}))
  expect(readProfile()).toEqual({name:'Updated elsewhere',photo:''})
})
