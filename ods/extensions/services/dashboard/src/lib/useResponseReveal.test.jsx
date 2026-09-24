import {act,renderHook} from '@testing-library/react'
import {useResponseReveal} from './useResponseReveal'

let now,frames,nextId
beforeEach(()=>{
  now=0;frames=new Map();nextId=0
  vi.spyOn(performance,'now').mockImplementation(()=>now)
  vi.stubGlobal('matchMedia',()=>({matches:false}))
  vi.stubGlobal('requestAnimationFrame',callback=>{frames.set(++nextId,callback);return nextId})
  vi.stubGlobal('cancelAnimationFrame',id=>frames.delete(id))
})
afterEach(()=>{vi.restoreAllMocks();vi.unstubAllGlobals()})
const frameAfter=elapsed=>{
  now+=elapsed
  const pending=[...frames.values()];frames.clear()
  act(()=>pending.forEach(callback=>callback(now)))
}

it.each([16,80,250,1000])('finishes a final burst on the first frame after its reveal duration at %ims intervals',interval=>{
  const source='abc🙂 '.repeat(200)
  const {result}=renderHook(()=>useResponseReveal(source,{animate:true}))
  let previous=''
  while(now<1200){
    frameAfter(interval)
    expect(result.current.startsWith(previous)).toBe(true)
    expect(source.startsWith(result.current)).toBe(true)
    expect(result.current).not.toMatch(/[\uD800-\uDBFF]$/)
    previous=result.current
  }
  expect(result.current).toBe(source)
  expect(frames.size).toBe(0)
})

it('catches up the first frame after a visible pause and preserves later streamed text',()=>{
  const source='First answer. '.repeat(100)
  const {result,rerender}=renderHook(({text,instant})=>useResponseReveal(text,{animate:true,instant}),{initialProps:{text:source,instant:false}})
  frameAfter(2000)
  expect(result.current).toBe(source)
  const continued=source+'Continued response. '.repeat(100)
  rerender({text:continued,instant:false})
  expect(result.current).toBe(source)
  frameAfter(16)
  expect(result.current.length).toBeGreaterThan(source.length)
  expect(result.current.length).toBeLessThan(continued.length)
  rerender({text:continued,instant:true})
  expect(result.current).toBe(continued)
  expect(frames.size).toBe(0)
})

it('flushes on hidden-tab transition and cancels pending frames',()=>{
  const source='Complete answer. '.repeat(100)
  const {result}=renderHook(()=>useResponseReveal(source,{animate:true}))
  frameAfter(16)
  expect(result.current).not.toBe(source)
  vi.spyOn(document,'visibilityState','get').mockReturnValue('hidden')
  act(()=>document.dispatchEvent(new Event('visibilitychange')))
  expect(result.current).toBe(source)
  expect(frames.size).toBe(0)
})
