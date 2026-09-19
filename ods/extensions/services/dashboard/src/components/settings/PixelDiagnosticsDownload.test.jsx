import {render,screen,fireEvent,waitFor} from '@testing-library/react'
import {MemoryRouter} from 'react-router-dom'
import PixelDiagnostics from './PixelDiagnostics'

let blobs,clicks
beforeEach(()=>{
  blobs=[];clicks=[]
  vi.spyOn(URL,'createObjectURL').mockImplementation(blob=>{blobs.push(blob);return 'blob:summary'})
  vi.spyOn(URL,'revokeObjectURL').mockImplementation(()=>{})
  vi.spyOn(HTMLAnchorElement.prototype,'click').mockImplementation(function(){clicks.push(this.download)})
  vi.stubGlobal('fetch',vi.fn(async path=>({ok:true,json:async()=>path==='/api/status' ? {inference:{loadedModel:'private-model-name',contextSize:32768},secret:'never-export'} : path.endsWith('access-mode') ? {available:true,configured_mode:'full-access',effective_mode:'sandboxed',runtime_verified:true,privateKey:'never-export'} : {available:true,detail:'private-host/path token=never-export',runtime:{model:'private-model-name',contextLength:8192}}})))
})
afterEach(()=>{vi.unstubAllGlobals();vi.restoreAllMocks()})
const show=()=>render(<MemoryRouter><PixelDiagnostics/></MemoryRouter>)
it('downloads only an explicit status allowlist from a completed diagnostic read',async()=>{
  show()
  const button=screen.getByRole('button',{name:'Download check summary'})
  expect(button).toBeDisabled()
  await waitFor(()=>expect(button).toBeEnabled())
  fireEvent.click(button)
  expect(fetch).toHaveBeenCalledTimes(3)
  const text=await new Promise(resolve=>{const reader=new globalThis.FileReader();reader.onload=()=>resolve(reader.result);reader.readAsText(blobs[0])})
  const report=JSON.parse(text)
  expect(report.kind).toBe('ods-pixel-diagnostic-summary')
  expect(report.checks.agent).toEqual({state:'Ready',ok:true,contextWindow:(8192).toLocaleString()+' tokens'})
  expect(report.checks.access).toEqual({state:'Verified',ok:true,configuredMode:'Full Access',effectiveMode:'Safer mode'})
  expect(text).not.toMatch(/private-model|private-host|never-export|privateKey|detail/)
  expect(clicks[0]).toMatch(/^ods-pixel-diagnostics-\d{4}-\d{2}-\d{2}\.json$/)
  expect(blobs[0].type).toBe('application/json')
  expect(document.querySelector('a[download]')).toBeNull()
  await waitFor(()=>expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:summary'),{timeout:2000})
})
it('disables stale report export during refresh and reports download failures',async()=>{
  show();await waitFor(()=>expect(screen.getByRole('button',{name:'Download check summary'})).toBeEnabled())
  URL.createObjectURL.mockImplementation(()=>{throw new Error('Unavailable')})
  fireEvent.click(screen.getByRole('button',{name:'Download check summary'}))
  expect(screen.getByRole('alert')).toHaveTextContent('could not be downloaded')
  fetch.mockImplementation(()=>new Promise(()=>{}))
  fireEvent.click(screen.getByRole('button',{name:'Refresh checks'}))
  expect(screen.getByRole('button',{name:'Download check summary'})).toBeDisabled()
  expect(clicks).toHaveLength(0)
})
