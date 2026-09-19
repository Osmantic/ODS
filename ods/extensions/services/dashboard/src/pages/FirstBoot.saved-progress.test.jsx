import {fireEvent, screen} from '@testing-library/react'
import {render} from '../test/test-utils'
import FirstBoot from './FirstBoot'

const key='ods-firstboot-progress'
beforeEach(() => {localStorage.clear();vi.stubGlobal('fetch', vi.fn(async () => ({ok:true,json:async () => ({ready:false})})))})
afterEach(() => {localStorage.clear();vi.unstubAllGlobals()})

it.each([5, -1, 1.5, '2', [], {}].map(step => [step]))('recovers an invalid saved step %j to usable navigation', step => {
  localStorage.setItem(key, JSON.stringify({step,deviceName:'my-ods',username:'alice',stack:'chat'}))
  render(<FirstBoot/>)
  expect(screen.getByRole('heading',{name:'Welcome to ODS.'})).toBeVisible()
  expect(screen.getByRole('textbox',{name:/Setup label/})).toHaveValue('my-ods')
  fireEvent.click(screen.getByRole('button',{name:'Continue'}))
  expect(screen.getByRole('textbox',{name:/^Username/})).toHaveValue('alice')
  expect(fetch.mock.calls.every(([,options]) => !options?.method || options.method==='GET')).toBe(true)
})

it.each([
  [{step:4,deviceName:{},username:'alice',stack:'chat'}, 'Welcome to ODS.'],
  [{step:4,deviceName:' '.repeat(32)+'ods',username:'alice',stack:'chat'}, 'Welcome to ODS.'],
  [{step:4,deviceName:'my-ods',username:' '.repeat(64)+'alice',stack:'chat'}, "Who's the first user?"],
  [{step:4,deviceName:'my-ods',username:[],stack:'chat'}, "Who's the first user?"],
  [{step:4,deviceName:'my-ods',username:'bad user',stack:'chat'}, "Who's the first user?"],
  [{step:4,deviceName:'my-ods',username:'alice',stack:'removed-stack'}, 'Pick your stack.'],
])('rewinds malformed details to their editable step', (saved, heading) => {
  localStorage.setItem(key,JSON.stringify(saved))
  render(<FirstBoot/>)
  expect(screen.getByRole('heading',{name:heading})).toBeVisible()
  expect(screen.queryByRole('button',{name:'Finish'})).toBeNull()
})

it('retains a valid confirmation snapshot and stack choice', () => {
  const saved={step:4,deviceName:'my-ods',username:'alice',stack:'chat-agents'}
  localStorage.setItem(key,JSON.stringify(saved))
  render(<FirstBoot/>)
  expect(screen.getByRole('heading',{name:'Ready?'})).toBeVisible()
  expect(screen.getByText('Chat + Agents')).toBeVisible()
  expect(JSON.parse(localStorage.getItem(key))).toEqual(saved)
})
