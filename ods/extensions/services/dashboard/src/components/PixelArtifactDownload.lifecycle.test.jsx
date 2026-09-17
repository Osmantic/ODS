import {act, fireEvent, render, screen} from '@testing-library/react'
import {createHash} from 'node:crypto'
import PixelArtifactDownload from './PixelArtifactDownload'

const bytes = new TextEncoder().encode('verified artifact')
const digest = createHash('sha256').update(bytes).digest('hex')
const preview = {siteId:'site-'+'a'.repeat(24)}
const file = {path:'index.html', bytes:bytes.byteLength, sha256:digest}

beforeEach(() => {
  vi.useFakeTimers()
  vi.stubGlobal('fetch',vi.fn(async () => ({ok:true,arrayBuffer:async () => bytes.buffer})))
  vi.spyOn(URL,'createObjectURL').mockReturnValue('blob:artifact')
  vi.spyOn(URL,'revokeObjectURL').mockImplementation(() => {})
  vi.spyOn(HTMLAnchorElement.prototype,'click').mockImplementation(() => {})
})
afterEach(() => {vi.useRealTimers();vi.restoreAllMocks();vi.unstubAllGlobals()})

it('expires a pending body, permits retry and ignores late verification',async () => {
  let finish
  fetch.mockResolvedValueOnce({ok:true,arrayBuffer:() => new Promise(resolve => {finish=resolve})})
  render(<PixelArtifactDownload preview={preview} file={file}/>)
  const button = screen.getByRole('button',{name:'Download index.html'})
  await act(async () => {fireEvent.click(button)})
  expect(fetch).toHaveBeenCalledOnce()
  expect(finish).toBeTypeOf('function')
  await act(async () => {await vi.advanceTimersByTimeAsync(12000)})
  expect(button).toBeEnabled()
  expect(screen.getByRole('alert')).toHaveTextContent('Download could not be verified')
  expect(URL.createObjectURL).not.toHaveBeenCalled()
  await act(async () => {fireEvent.click(button)})
  expect(screen.getByRole('status')).toHaveTextContent('Verified download started')
  await act(async () => {finish(bytes.buffer)})
  expect(HTMLAnchorElement.prototype.click).toHaveBeenCalledOnce()
  expect(screen.queryByRole('alert')).toBeNull()
})

it.each(['creation','attachment'])('releases verified bytes when link %s fails',async stage => {
  render(<PixelArtifactDownload preview={preview} file={file}/>)
  if (stage === 'attachment') {
    vi.spyOn(document.body,'append').mockImplementation(() => {throw new Error('Attachment refused')})
  } else {
    const create = document.createElement.bind(document)
    vi.spyOn(document,'createElement').mockImplementation((tag,...args) => {
      if(tag === 'a') throw new Error('Creation refused')
      return create(tag,...args)
    })
  }
  await act(async () => {fireEvent.click(screen.getByRole('button',{name:'Download index.html'}))})
  expect(screen.getByRole('alert')).toHaveTextContent('Download could not be verified')
  await act(async () => {await vi.advanceTimersByTimeAsync(1000)})
  expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:artifact')
  expect(document.querySelector('a[download]')).toBeNull()
})
