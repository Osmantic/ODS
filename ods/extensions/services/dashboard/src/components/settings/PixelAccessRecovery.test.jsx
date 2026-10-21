import {act, cleanup, fireEvent, render, screen} from '@testing-library/react'
import {afterEach, beforeEach, expect, it, vi} from 'vitest'
import PixelAccessCard from './PixelAccessCard'
import { PIXEL_RECOVERY_REQUIRED } from '../../lib/pixelReadiness'

const verified = {available:true,surface:'wsl-systemd',configured_mode:'full-access',effective_mode:'full-access',runtime_verified:true,revision:'a'.repeat(64),busy:false,pending:false}
const unavailable = {...verified,available:false,surface:'linux',runtime_verified:false,effective_mode:'unknown'}
const response = value => ({ok:true,json:async()=>value})
const deferred = () => {let resolve;const promise = new Promise(done=>{resolve=done});return {promise,resolve}}
const tick = ms => act(async()=>{await vi.advanceTimersByTimeAsync(ms)})
const effective = () => screen.getByText('Effective').nextElementSibling
let visibility
beforeEach(()=>{vi.useFakeTimers();visibility='visible';vi.spyOn(document,'visibilityState','get').mockImplementation(()=>visibility)})
afterEach(()=>{cleanup();vi.useRealTimers();vi.restoreAllMocks();vi.unstubAllGlobals()})

it('keeps verified Sandbox distinct from an interrupted chat gate and polls without releasing it',async()=>{
 const sandbox={...verified,configured_mode:'sandboxed',effective_mode:'sandboxed'}
 const fetch=vi.fn().mockResolvedValueOnce(response({...sandbox,reason:'chat-recovery-required'})).mockResolvedValue(response(sandbox));vi.stubGlobal('fetch',fetch)
 render(<PixelAccessCard/>);await tick(0)
 expect(effective()).toHaveTextContent('Sandbox')
 expect(screen.getByRole('alert')).toHaveTextContent('new messages are held')
 expect(screen.getByRole('button',{name:'Verify Sandbox'})).toBeEnabled()
 expect(screen.getByRole('button',{name:'Enable Full Access'})).toBeDisabled()
 await tick(5000)
 expect(screen.queryByRole('alert')).toBeNull()
 expect(effective()).toHaveTextContent('Sandbox')
 await tick(15000);expect(fetch).toHaveBeenCalledTimes(2)
 expect(fetch.mock.calls.every(call=>call[1]?.method!=='POST')).toBe(true)
})

it('recovers an interrupted chat only after explicit Verify Sandbox with a refreshed revision',async()=>{
 const sandbox={...verified,configured_mode:'sandboxed',effective_mode:'sandboxed'}
 const fetch=vi.fn().mockResolvedValueOnce(response({...sandbox,reason:'chat-recovery-required'}))
  .mockResolvedValueOnce(response({...sandbox,reason:'chat-recovery-required',revision:'b'.repeat(64)}))
  .mockResolvedValue(response(sandbox));vi.stubGlobal('fetch',fetch)
 render(<PixelAccessCard/>);await tick(0)
 fireEvent.click(screen.getByRole('button',{name:'Verify Sandbox'}));await tick(0)
 const posts=fetch.mock.calls.filter(call=>call[1]?.method==='POST')
 expect(posts).toHaveLength(1)
 expect(JSON.parse(posts[0][1].body)).toEqual({mode:'sandboxed',revision:'b'.repeat(64),confirmed:false})
 expect(screen.queryByRole('alert')).toBeNull()
 expect(effective()).toHaveTextContent('Sandbox')
 await tick(15000);expect(fetch).toHaveBeenCalledTimes(3)
})

it('rechecks unavailable idle status automatically and replaces the fallback platform with fresh WSL proof',async()=>{
 const fetch=vi.fn().mockResolvedValueOnce(response(unavailable)).mockResolvedValue(response(verified));vi.stubGlobal('fetch',fetch)
 render(<PixelAccessCard/>);await tick(0)
 expect(effective()).toHaveTextContent('Not verified')
 expect(screen.getByText('Agent runtime').nextElementSibling).not.toHaveTextContent('Linux')
 await tick(5000)
 expect(effective()).toHaveTextContent('Full Access')
 expect(screen.getByText('Agent runtime').nextElementSibling).toHaveTextContent('WSL')
 await tick(90000);expect(fetch).toHaveBeenCalledTimes(2)
 expect(fetch.mock.calls.every(call=>call[1]?.method!=='POST')).toBe(true)
})
it('rechecks an available but unverified idle runtime',async()=>{
 const fetch=vi.fn().mockResolvedValueOnce(response({...unavailable,available:true})).mockResolvedValue(response(verified));vi.stubGlobal('fetch',fetch)
 render(<PixelAccessCard/>);await tick(0);await tick(5000);expect(effective()).toHaveTextContent('Full Access');expect(fetch).toHaveBeenCalledTimes(2)
})
it('backs off failed inspections and recovers without retrying a permission mutation',async()=>{
 const fetch=vi.fn().mockRejectedValue(new Error('offline'));vi.stubGlobal('fetch',fetch)
 render(<PixelAccessCard/>);await tick(0);expect(fetch).toHaveBeenCalledTimes(1)
 await tick(5000);expect(fetch).toHaveBeenCalledTimes(2)
 await tick(9999);expect(fetch).toHaveBeenCalledTimes(2)
 await tick(1);expect(fetch).toHaveBeenCalledTimes(3)
 await tick(20000);expect(fetch).toHaveBeenCalledTimes(4)
 await tick(30000);expect(fetch).toHaveBeenCalledTimes(5)
 fetch.mockResolvedValue(response(verified));await tick(30000)
 expect(effective()).toHaveTextContent('Full Access');expect(screen.queryByRole('alert')).toBeNull()
 expect(fetch.mock.calls.every(call=>call[1]?.method!=='POST')).toBe(true)
})
it('pauses recovery while hidden and coalesces visible/focus/online into one fresh read',async()=>{
 const slow=deferred();const fetch=vi.fn().mockResolvedValueOnce(response(unavailable)).mockReturnValueOnce(slow.promise);vi.stubGlobal('fetch',fetch)
 render(<PixelAccessCard/>);await tick(0)
 visibility='hidden';fireEvent(document,new Event('visibilitychange'));fireEvent(window,new Event(PIXEL_RECOVERY_REQUIRED));await tick(120000);expect(fetch).toHaveBeenCalledTimes(1)
 visibility='visible';fireEvent(document,new Event('visibilitychange'));fireEvent.focus(window);fireEvent(window,new Event('online'));await tick(0)
 expect(fetch).toHaveBeenCalledTimes(2);await act(async()=>slow.resolve(response(verified)));expect(effective()).toHaveTextContent('Full Access')
})
it.each(['focus','online',PIXEL_RECOVERY_REQUIRED])('refreshes a previously verified status on %s, keeping proof withdrawn until the read finishes',async(event)=>{
 const slow=deferred();const fetch=vi.fn().mockResolvedValueOnce(response(verified)).mockReturnValueOnce(slow.promise);vi.stubGlobal('fetch',fetch)
 render(<PixelAccessCard/>);await tick(0);fireEvent(window,new Event(event));await tick(0)
 expect(fetch).toHaveBeenCalledTimes(2);expect(effective()).toHaveTextContent('Not verified')
 await act(async()=>slow.resolve(response({...verified,configured_mode:'sandboxed',effective_mode:'sandboxed'})))
 expect(effective()).toHaveTextContent('Sandbox')
})
it.each(['headers','body'])('times out a hung %s read, retries, and ignores its eventual old reply',async(stage)=>{
 const slow=deferred();const fetch=vi.fn().mockImplementationOnce(()=>stage==='headers'?slow.promise:{ok:true,json:()=>slow.promise}).mockResolvedValue(response(verified));vi.stubGlobal('fetch',fetch)
 render(<PixelAccessCard/>);await tick(0);await tick(45000)
 expect(fetch.mock.calls[0][1].signal.aborted).toBe(true);expect(screen.getByRole('alert')).toHaveTextContent('could not be checked')
 await tick(5000);expect(effective()).toHaveTextContent('Full Access')
 await act(async()=>slow.resolve(stage==='headers'?response(unavailable):unavailable));expect(effective()).toHaveTextContent('Full Access')
})
it('aborts inspection and removes wake-up listeners on unmount',async()=>{
 const slow=deferred();const fetch=vi.fn().mockReturnValue(slow.promise);vi.stubGlobal('fetch',fetch)
 const view=render(<PixelAccessCard/>);await tick(0);view.unmount()
 expect(fetch.mock.calls[0][1].signal.aborted).toBe(true)
 fireEvent.focus(window);fireEvent(window,new Event('online'));fireEvent(window,new Event(PIXEL_RECOVERY_REQUIRED));await tick(120000);expect(fetch).toHaveBeenCalledTimes(1)
 await act(async()=>slow.resolve(response(verified)));expect(screen.queryByText('Full Access')).toBeNull()
})
it('superseded automatic inspection cannot replace a newer permission mutation receipt',async()=>{
 const slow=deferred();const sandbox={...verified,configured_mode:'sandboxed',effective_mode:'sandboxed'}
 const fetch=vi.fn().mockResolvedValueOnce(response(unavailable)).mockReturnValueOnce(slow.promise).mockResolvedValueOnce(response(verified)).mockResolvedValueOnce(response(verified)).mockResolvedValueOnce(response(sandbox));vi.stubGlobal('fetch',fetch)
 render(<PixelAccessCard/>);await tick(0);await tick(5000)
 fireEvent.click(screen.getByRole('button',{name:'Refresh status'}));await tick(0)
 fireEvent.click(screen.getByRole('button',{name:'Restore Sandbox'}));await tick(0)
 expect(effective()).toHaveTextContent('Sandbox')
 await act(async()=>slow.resolve(response(unavailable)));expect(effective()).toHaveTextContent('Sandbox')
 expect(fetch.mock.calls.filter(call=>call[1]?.method==='POST')).toHaveLength(1)
})
it('wake-up events during an explicit change never race the POST with an automatic read',async()=>{
 const post=deferred();const fetch=vi.fn().mockImplementation((_url,opts)=>opts?.method==='POST'?post.promise:Promise.resolve(response(verified)));vi.stubGlobal('fetch',fetch)
 render(<PixelAccessCard/>);await tick(0);fireEvent.click(screen.getByRole('button',{name:'Restore Sandbox'}));await tick(0)
 expect(fetch).toHaveBeenCalledTimes(3);fireEvent.focus(window);fireEvent(window,new Event('online'));fireEvent(window,new Event(PIXEL_RECOVERY_REQUIRED));await tick(10000);expect(fetch).toHaveBeenCalledTimes(3)
 await act(async()=>post.resolve(response({...verified,configured_mode:'sandboxed',effective_mode:'sandboxed'})));expect(effective()).toHaveTextContent('Sandbox')
})

it('supersedes a pre-hold read on the recovery signal and ignores its eventual stale Sandbox receipt',async()=>{
 const slow=deferred(),sandbox={...verified,configured_mode:'sandboxed',effective_mode:'sandboxed'}
 const fetch=vi.fn().mockResolvedValueOnce(response(sandbox)).mockReturnValueOnce(slow.promise)
  .mockResolvedValue(response({...sandbox,reason:'chat-recovery-required'}));vi.stubGlobal('fetch',fetch)
 render(<PixelAccessCard/>);await tick(0)
 fireEvent.focus(window);await tick(0);expect(fetch).toHaveBeenCalledTimes(2)
 fireEvent(window,new Event(PIXEL_RECOVERY_REQUIRED));await tick(0)
 expect(fetch).toHaveBeenCalledTimes(3);expect(fetch.mock.calls[1][1].signal.aborted).toBe(true)
 expect(screen.getByRole('alert')).toHaveTextContent('new messages are held')
 await act(async()=>slow.resolve(response(sandbox)))
 expect(screen.getByRole('button',{name:'Enable Full Access'})).toBeDisabled()
 expect(screen.getByRole('alert')).toHaveTextContent('new messages are held')
 expect(fetch.mock.calls.every(([,options])=>options?.method!=='POST')).toBe(true)
})
it('does not poll a mounted but hidden settings section, and refreshes when opened',async()=>{
 const fetch=vi.fn().mockResolvedValue(response(verified));vi.stubGlobal('fetch',fetch)
 const view=render(<PixelAccessCard active={false}/>);await tick(90000);fireEvent.focus(window);fireEvent(window,new Event(PIXEL_RECOVERY_REQUIRED));expect(fetch).not.toHaveBeenCalled()
 view.rerender(<PixelAccessCard active/>);await tick(0);expect(fetch).toHaveBeenCalledTimes(1)
 view.rerender(<PixelAccessCard active={false}/>);await tick(90000);fireEvent(window,new Event('online'));expect(fetch).toHaveBeenCalledTimes(1)
})
it('does not restart inspections when a mutation fails after leaving settings',async()=>{
 const post=deferred();const fetch=vi.fn().mockImplementation((_url,opts)=>opts?.method==='POST'?post.promise:Promise.resolve(response(verified)));vi.stubGlobal('fetch',fetch)
 const view=render(<PixelAccessCard/>);await tick(0);fireEvent.click(screen.getByRole('button',{name:'Restore Sandbox'}));await tick(0)
 view.unmount();await act(async()=>post.resolve({ok:false}));await tick(90000);expect(fetch).toHaveBeenCalledTimes(3)
})
it('allows a fresh read when returning after a hidden-section mutation finished',async()=>{
 const post=deferred();const fetch=vi.fn().mockImplementation((_url,opts)=>opts?.method==='POST'?post.promise:Promise.resolve(response(verified)));vi.stubGlobal('fetch',fetch)
 const view=render(<PixelAccessCard/>);await tick(0);fireEvent.click(screen.getByRole('button',{name:'Restore Sandbox'}));await tick(0)
 view.rerender(<PixelAccessCard active={false}/>);await act(async()=>post.resolve({ok:false}));expect(fetch).toHaveBeenCalledTimes(3)
 view.rerender(<PixelAccessCard active/>);await tick(0);expect(fetch).toHaveBeenCalledTimes(4)
 expect(screen.getByRole('button',{name:'Restore Sandbox'})).toBeEnabled()
})
it.each([{runtime_verified:'true'},{available:false},{pending:true}])('does not claim effective access from contradictory or untyped proof %j',async(extra)=>{
 vi.stubGlobal('fetch',vi.fn().mockResolvedValue(response({...verified,...extra})))
 render(<PixelAccessCard/>);await tick(0);expect(effective()).toHaveTextContent('Not verified')
})
it.each(['success','failure'])('reopening while a POST is pending requires a fresh read after %s, without adopting an earlier receipt',async(outcome)=>{
 const post=deferred(),fresh=deferred();let reads=0
 const fetch=vi.fn().mockImplementation((_url,opts)=>opts?.method==='POST'?post.promise:++reads<=2?Promise.resolve(response(verified)):fresh.promise)
 vi.stubGlobal('fetch',fetch)
 const view=render(<PixelAccessCard/>);await tick(0);fireEvent.click(screen.getByRole('button',{name:'Restore Sandbox'}));await tick(0)
 view.rerender(<PixelAccessCard active={false}/>);view.rerender(<PixelAccessCard active/>);await tick(0)
 expect(fetch).toHaveBeenCalledTimes(3)
 await act(async()=>post.resolve(outcome==='success'?response({...verified,configured_mode:'sandboxed',effective_mode:'sandboxed'}):{ok:false}))
 expect(fetch).toHaveBeenCalledTimes(4);expect(effective()).toHaveTextContent('Not verified')
 await act(async()=>fresh.resolve(response(unavailable)))
 expect(effective()).toHaveTextContent('Not verified')
 expect(fetch.mock.calls.filter(call=>call[1]?.method==='POST')).toHaveLength(1)
 if(outcome==='failure')expect(screen.getByRole('alert')).toHaveTextContent('The change was not verified')
})
it('reopening during the pre-change read cancels that intent and inspects without sending a POST',async()=>{
 const preflight=deferred();const fetch=vi.fn().mockResolvedValueOnce(response(verified)).mockReturnValueOnce(preflight.promise).mockResolvedValue(response(verified));vi.stubGlobal('fetch',fetch)
 const view=render(<PixelAccessCard/>);await tick(0);fireEvent.click(screen.getByRole('button',{name:'Restore Sandbox'}));await tick(0)
 view.rerender(<PixelAccessCard active={false}/>);view.rerender(<PixelAccessCard active/>);await tick(0)
 expect(fetch).toHaveBeenCalledTimes(3);expect(effective()).toHaveTextContent('Full Access')
 await act(async()=>preflight.resolve(response(verified)));expect(fetch.mock.calls.every(call=>call[1]?.method!=='POST')).toBe(true)
 expect(screen.queryByText(/No change was requested/)).toBeNull()
})
