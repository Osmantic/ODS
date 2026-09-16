import {act,fireEvent,render,screen,waitFor} from '@testing-library/react'
import {unzipSync} from 'fflate'
import PixelTaskFiles from './PixelTaskFiles'
import {loadSnapshotFiles,loadArtifactBytes} from '../lib/pixelArtifacts'
vi.mock('../lib/pixelArtifacts',()=>({loadSnapshotFiles:vi.fn(),loadArtifactBytes:vi.fn()}))
vi.mock('./PixelPreviewSource',()=>({default:()=>null}))
const preview={siteId:'site-'+ 'a'.repeat(24),sha256:'b'.repeat(64),files:2,bytes:12}
const files=[{path:'index.html',bytes:5},{path:'assets/site.css',bytes:7}]
let blob
beforeEach(()=>{
  loadSnapshotFiles.mockResolvedValue(files)
  loadArtifactBytes.mockImplementation(async(_preview,file)=>new TextEncoder().encode(file.path==='index.html'?'hello':'a{b:c;}').buffer)
  vi.spyOn(URL,'createObjectURL').mockImplementation(value=>{blob=value;return 'blob:archive'})
  vi.spyOn(URL,'revokeObjectURL').mockImplementation(()=>{})
  vi.spyOn(HTMLAnchorElement.prototype,'click').mockImplementation(()=>{})
})
afterEach(()=>{vi.restoreAllMocks();vi.clearAllMocks();vi.useRealTimers()})
it('downloads all verified publication paths and original bytes as a ZIP',async()=>{
  render(<PixelTaskFiles preview={preview}/>);await screen.findByText('index.html')
  fireEvent.click(screen.getByRole('button',{name:'Download publication ZIP'}))
  await screen.findByText('Publication download started.')
  const data=await new Promise((resolve,reject)=>{const reader=new globalThis.FileReader();reader.onload=()=>resolve(reader.result);reader.onerror=reject;reader.readAsArrayBuffer(blob)})
  const archive=unzipSync(new Uint8Array(data))
  expect(Object.keys(archive)).toEqual(files.map(file=>file.path))
  expect(new TextDecoder().decode(archive['assets/site.css'])).toBe('a{b:c;}')
  expect(loadArtifactBytes).toHaveBeenCalledTimes(2)
  expect(document.querySelector('a[download]')).toBeNull()
})
it('never downloads a partial archive if verification fails',async()=>{
  loadArtifactBytes.mockRejectedValue(new Error('digest mismatch'))
  render(<PixelTaskFiles preview={preview}/>);await screen.findByText('index.html')
  fireEvent.click(screen.getByRole('button',{name:'Download publication ZIP'}))
  expect(await screen.findByRole('alert')).toHaveTextContent('could not be verified')
  expect(URL.createObjectURL).not.toHaveBeenCalled()
})
it('cancels pending verification and ignores its late completion',async()=>{
  let resolve,signal
  loadArtifactBytes.mockImplementation((_p,_f,s)=>{signal=s;return new Promise(r=>{resolve=r})})
  render(<PixelTaskFiles preview={preview}/>);await screen.findByText('index.html')
  fireEvent.click(screen.getByRole('button',{name:'Download publication ZIP'}))
  await waitFor(()=>expect(signal).toBeTruthy())
  fireEvent.click(screen.getByRole('button',{name:'Cancel ZIP download'}))
  await act(async()=>resolve(new Uint8Array(5).buffer))
  expect(signal.aborted).toBe(true)
  expect(URL.createObjectURL).not.toHaveBeenCalled()
  expect(loadArtifactBytes).toHaveBeenCalledTimes(1)
})

it('releases the UI when a verifier never settles',async()=>{
  vi.useFakeTimers()
  loadArtifactBytes.mockImplementation(()=>new Promise(()=>{}))
  const view=render(<PixelTaskFiles preview={preview}/>)
  await act(async()=>{})
  fireEvent.click(screen.getByRole('button',{name:'Download publication ZIP'}))
  await act(async()=>{await vi.advanceTimersByTimeAsync(30000)})
  expect(screen.getByRole('alert')).toHaveTextContent('could not be verified')
  expect(screen.getByRole('button',{name:'Download publication ZIP'})).toBeEnabled()
  expect(URL.createObjectURL).not.toHaveBeenCalled()
  view.unmount()
})
