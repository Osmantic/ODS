import { useEffect } from 'react'
import { act, fireEvent, render, screen, waitFor } from '../../test/test-utils'
import PixelRuntimeSettings from './PixelRuntimeSettings'
import PixelProviderRuntime from './PixelProviderRuntime'
import { runtimeDoc as settingsRuntime } from './pixelRuntimeStatusFixtures'
import { runtimeDoc as providerRuntime } from './pixelProviderRuntimeFixtures'

const identity = vi.hoisted(() => ({displayName:'Nova'}))
vi.mock('../../contexts/PortalIdentityContext', () => ({usePortalIdentity:()=>identity}))
vi.mock('../PixelAdviceRuntime', () => ({default:function ReadyWorker({onReadyChange}) {
  useEffect(()=>onReadyChange(true),[onReadyChange])
  return null
}}))

const response = value => ({ok:true,status:200,json:async()=>value})
const deferred = () => {let resolve;const promise=new Promise(done=>{resolve=done});return {promise,resolve}}
afterEach(()=>{vi.unstubAllGlobals();identity.displayName='Nova'})

it('uses the saved identity for preference controls, runtime inspection and confirmation without changing API paths',async()=>{
  const read=deferred()
  const fetch=vi.fn(url=>url==='/api/pixel/settings'
    ? Promise.resolve(response({configuration:{schemaVersion:1,revision:3,preferences:{}},runtime:{status:'not-inspected',reason:'runtime-status-separate'}}))
    : read.promise)
  vi.stubGlobal('fetch',fetch)
  render(<PixelRuntimeSettings/>)
  expect(screen.getByRole('heading',{name:'Nova runtime settings'})).toBeVisible()
  expect(screen.getByRole('button',{name:'Save Nova preferences'})).toBeVisible()
  expect(screen.getByText('Inspecting current Nova runtime…')).toBeVisible()
  await act(async()=>read.resolve(response(settingsRuntime())))
  const apply=screen.getByRole('button',{name:'Apply saved Nova preferences'})
  await waitFor(()=>expect(apply).toBeEnabled())
  fireEvent.click(apply)
  expect(screen.getByRole('dialog')).toHaveAccessibleDescription(/Nova will restart only when idle/)
  expect(document.body.textContent).not.toContain('Pixel')
  expect([...new Set(fetch.mock.calls.map(([url])=>url))].sort()).toEqual(['/api/pixel/settings','/api/pixel/settings/runtime'])
  expect(fetch.mock.calls.every(([,options])=>options.method==='GET')).toBe(true)
})

it('uses the saved identity for provider status, confirmation and pending stage without submitting twice',async()=>{
  const read=deferred(),write=deferred()
  const fetch=vi.fn((_url,options)=>options.method==='POST'?write.promise:read.promise)
  vi.stubGlobal('fetch',fetch)
  render(<PixelProviderRuntime savedRevision={3} saving={false} blocked={false} routingEnabled allowCloud={false} onBusyChange={vi.fn()}/>)
  expect(screen.getByText('Inspecting current Nova runtime…')).toBeVisible()
  await act(async()=>read.resolve(response(providerRuntime('applied'))))
  expect(screen.getByText('Saved provider revision 3 is registered in the current Nova runtime.')).toBeVisible()
  fireEvent.click(screen.getByRole('button',{name:'Deactivate managed providers'}))
  expect(screen.getByRole('dialog')).toHaveAccessibleDescription(/Nova may restart when idle/)
  fireEvent.click(screen.getByRole('button',{name:'Confirm and deactivate'}))
  expect(screen.getByText('Waiting for the provider controller. Nova may restart…')).toBeVisible()
  expect(document.body.textContent).not.toContain('Pixel')
  expect(fetch.mock.calls.every(([url])=>url==='/api/pixel/providers/runtime')).toBe(true)
  expect(fetch.mock.calls.filter(([,options])=>options.method==='POST')).toHaveLength(1)
})
