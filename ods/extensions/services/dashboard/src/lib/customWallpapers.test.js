import {prepareWallpaper, isCustomWallpaper, isStoredWallpaper, MAX_WALLPAPER_VIDEO_BYTES} from './customWallpapers'
const {HTMLMediaElement, HTMLVideoElement} = window
afterEach(()=>{vi.restoreAllMocks();vi.unstubAllGlobals()})
it('accepts only opaque custom IDs, not URLs or CSS',()=>{
  expect(isCustomWallpaper('custom-11111111-2222-4333-8444-555555555555')).toBe(true)
  for(const value of ['custom-evil','https://remote/image','url(test)',null]) expect(isCustomWallpaper(value)).toBe(false)
})

function mockVideo({error = false, width = 1920, duration = 3} = {}) {
  const createObjectURL = vi.fn(() => 'blob:qa-video'), revokeObjectURL = vi.fn()
  vi.stubGlobal('URL', Object.assign(class extends URL {}, {createObjectURL, revokeObjectURL}))
  vi.spyOn(HTMLMediaElement.prototype, 'load').mockImplementation(function () {
    if (this.getAttribute('src')) window.queueMicrotask(() => error ? this.onerror?.() : this.onloadeddata?.())
  })
  const pause = vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(() => {})
  vi.spyOn(HTMLVideoElement.prototype, 'videoWidth', 'get').mockReturnValue(width)
  vi.spyOn(HTMLVideoElement.prototype, 'videoHeight', 'get').mockReturnValue(1080)
  vi.spyOn(HTMLMediaElement.prototype, 'duration', 'get').mockReturnValue(duration)
  const drawImage = vi.fn()
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue({drawImage})
  vi.spyOn(HTMLCanvasElement.prototype, 'toDataURL').mockReturnValue('data:image/webp;base64,YQ==')
  return {createObjectURL, revokeObjectURL, pause, drawImage}
}

it.each(['video/mp4','video/webm'])('stores %s as a local blob with a static thumbnail', async type => {
  const mocks = mockVideo()
  const file = new File(['video'], 'Motion.mp4', {type})
  const row = await prepareWallpaper(file)
  expect(row).toMatchObject({kind:'video',name:'Motion',video:file,image:'data:image/webp;base64,YQ=='})
  expect(isStoredWallpaper(row)).toBe(true)
  expect(mocks.drawImage).toHaveBeenCalledWith(expect.any(HTMLVideoElement),0,0,1280,720)
  expect(mocks.revokeObjectURL).toHaveBeenCalledWith('blob:qa-video')
  expect(mocks.pause).toHaveBeenCalledOnce()
})

it('rejects corrupt, unbounded and oversized video without leaking URLs', async () => {
  const mocks = mockVideo({error:true})
  await expect(prepareWallpaper(new File(['bad'],'bad.mp4',{type:'video/mp4'}))).rejects.toThrow('could not be opened')
  expect(mocks.revokeObjectURL).toHaveBeenCalledOnce()
  await expect(prepareWallpaper({type:'video/mp4',size:MAX_WALLPAPER_VIDEO_BYTES+1})).rejects.toThrow('100 MB')
  expect(mocks.createObjectURL).toHaveBeenCalledOnce()
})

it('rejects video that exceeds the resolution limit', async () => {
  const mocks=mockVideo({width:10000})
  await expect(prepareWallpaper(new File(['v'],'big.webm',{type:'video/webm'}))).rejects.toThrow('4K')
  expect(mocks.revokeObjectURL).toHaveBeenCalledOnce()
})

it('rejects remote media and malformed stored records, retaining old image records', () => {
  const row={id:'custom-11111111-2222-4333-8444-555555555555',name:'Saved',image:'data:image/webp;base64,YQ=='}
  expect(isStoredWallpaper(row)).toBe(true)
  expect(isStoredWallpaper({...row,kind:'video',video:'https://example.com/movie.mp4'})).toBe(false)
  expect(isStoredWallpaper({...row,kind:'video',video:new Blob(['x'],{type:'text/html'})})).toBe(false)
  expect(isStoredWallpaper({...row,kind:'unknown'})).toBe(false)
})
it('rejects unsupported formats and oversized images before decoding',async()=>{
  const decode=vi.fn();vi.stubGlobal('createImageBitmap',decode)
  await expect(prepareWallpaper({type:'image/svg+xml',size:20})).rejects.toThrow('JPG')
  await expect(prepareWallpaper({type:'image/png',size:21*1024*1024})).rejects.toThrow('20 MB')
  expect(decode).not.toHaveBeenCalled()
})
it('resizes without cropping, strips source metadata, and releases decoded resources',async()=>{
  const bitmap={width:4000,height:2000,close:vi.fn()},drawImage=vi.fn()
  vi.stubGlobal('createImageBitmap',vi.fn(async()=>bitmap))
  vi.spyOn(HTMLCanvasElement.prototype,'getContext').mockReturnValue({drawImage})
  vi.spyOn(HTMLCanvasElement.prototype,'toDataURL').mockReturnValue('data:image/webp;base64,YQ==')
  const row=await prepareWallpaper({name:'Forest.jpg',type:'image/jpeg',size:2048})
  expect(row.name).toBe('Forest');expect(isCustomWallpaper(row.id)).toBe(true)
  expect(drawImage).toHaveBeenCalledWith(bitmap,0,0,2560,1280)
  expect(bitmap.close).toHaveBeenCalledOnce()
})
