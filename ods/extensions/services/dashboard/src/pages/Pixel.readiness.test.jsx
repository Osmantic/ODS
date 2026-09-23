import {act, fireEvent, screen, within} from '@testing-library/react'
import {render} from '../test/test-utils'
import Pixel from './Pixel'

const json = data => ({ok:true, json:async()=>data})
const failed = () => ({schemaVersion:1, state:'attention', routeAvailable:true,
  accessState:'failed', effectiveMode:'unknown', releaseState:'unverified',
  reasonCode:'access-inspection-failed', observedAt:new Date().toISOString()})

beforeEach(() => { localStorage.clear() })
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); vi.restoreAllMocks() })

function transport(getStatus) {
  vi.stubGlobal('fetch', vi.fn(async url => {
    if (url === '/api/pixel/status') return json(getStatus())
    if (url === '/api/pixel/chat/context') return json({schemaVersion:1,status:'missing',sessionRevision:null,
      context:null,model:null,compaction:{status:'idle',count:0},history:{revision:null,acknowledgedMessages:0}})
    return json({})
  }))
}

it('makes failed access conspicuous while retaining existing safe chat controls', async () => {
  transport(() => ({available:true, model:'pixel/default', runtime:{model:'Mac fixture',contextLength:65536,source:'local-switchboard'}, readiness:failed()}))
  render(<Pixel/>)
  const notice = await screen.findByRole('alert', {name:'Runtime readiness'})
  expect(notice).toHaveTextContent('host access inspection failed')
  expect(screen.getByText('Needs attention')).toBeVisible()
  expect(within(notice).getByRole('link', {name:'Access settings'})).toHaveAttribute('href','/settings?section=access')
  const composer = screen.getByPlaceholderText('Message Portal...')
  expect(composer).toBeEnabled()
  fireEvent.change(composer, {target:{value:'Explain this concept'}})
  expect(screen.getByTitle('Send')).toBeEnabled()
  expect(screen.queryByText('Ready')).toBeNull()
  expect(notice).not.toHaveTextContent(/admission|held/)
})

it('legacy available status stays usable but explicitly unverified', async () => {
  transport(() => ({available:true, detail:'Owner agent ready'}))
  render(<Pixel/>)
  const notice = await screen.findByRole('status', {name:'Runtime readiness'})
  expect(notice).toHaveTextContent('Host access and installed-release readiness are unverified')
  expect(screen.getByPlaceholderText('Message Portal...')).toBeEnabled()
  expect(screen.queryByText('Ready')).toBeNull()
  expect(screen.getByText('Available')).not.toHaveClass('text-emerald-400')
})

it('replaces previous access proof when a subsequent status lacks readiness', async () => {
  let status = {available:true, readiness:{...failed(),state:'unverified',accessState:'verified',
    effectiveMode:'sandboxed',reasonCode:'release-binding-unavailable'}}
  transport(() => status)
  vi.useFakeTimers()
  render(<Pixel/>)
  await act(async () => {})
  const notice = screen.getByRole('status', {name:'Runtime readiness'})
  expect(notice).toHaveTextContent('Host access is verified')
  status = {available:true}
  await act(async () => { await vi.advanceTimersByTimeAsync(3000) })
  expect(notice).toHaveTextContent('Host access and installed-release readiness are unverified')
  expect(notice).not.toHaveTextContent('Host access is verified')
})
