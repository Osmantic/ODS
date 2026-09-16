import {Blob as NodeBlob} from 'node:buffer'
import {fireEvent,render,screen} from '@testing-library/react'
import UsageView from './UsageView'
import fixture from '../../test/fixtures/usage-report.json'

const period = {start:'2026-01-01',end:'2026-01-31'}
const report = {period,source:{name:'token-spy',status:'ok',api_key:'not-exported',local_runtime:{status:'observed',included_in_totals:false,request_count_available:false,counters:[{runtime:'llama.cpp',model:'local',service:'llama-server',requests:0,input_tokens:1000,request_count_source:'unavailable',url:'http://private-host'}]}},summary:{total_tokens:10},models:Array.from({length:12},(_,i) => ({model:`Model ${i}`,provider:'Local',cost_source:'untracked',cost_usd:null,input_tokens:i,output_tokens:NaN,requests:null,api_key:'not-exported'})),daily:[{date:'2026-01-01',input_tokens:10}],prompt:'not-exported',services:[],sources:[]}
const props = {report,readiness:{status:'ready'},range:period}
beforeEach(() => {
  vi.stubGlobal('Blob',NodeBlob)
  vi.stubGlobal('URL',Object.assign(class extends URL {},{createObjectURL:vi.fn(() => 'blob:usage-report'),revokeObjectURL:vi.fn()}))
  vi.spyOn(HTMLAnchorElement.prototype,'click').mockImplementation(() => {})
})
afterEach(() => {vi.restoreAllMocks();vi.unstubAllGlobals();vi.useRealTimers()})

test('downloads the entire reported period with source distinctions and only declared aggregate fields', async () => {
  vi.useFakeTimers()
  render(<UsageView {...props}/>)
  fireEvent.click(screen.getByRole('button',{name:'Models',exact:true}))
  fireEvent.change(screen.getByRole('textbox',{name:'Search models'}),{target:{value:'Model 1'}})
  fireEvent.click(screen.getByRole('button',{name:'Download report JSON'}))
  const blob = URL.createObjectURL.mock.calls[0][0]
  const raw = await blob.text(), value = JSON.parse(raw)
  expect(blob.type).toBe('application/json')
  expect(value.kind).toBe('ods-usage-report')
  expect(value.period).toEqual(period)
  expect(value.timezone).toBe('UTC')
  expect(value.models).toHaveLength(12)
  expect(value.models[0]).toMatchObject({cost_source:'untracked',cost_usd:null,output_tokens:null,requests:null})
  expect(value.source.localRuntime).toMatchObject({includedInTotals:false,requestCountAvailable:false})
  expect(value.source.localRuntime.counters[0].input_tokens).toBe(1000)
  expect(value.summary.total_tokens).toBe(10)
  expect(raw).not.toContain('not-exported')
  expect(raw).not.toContain('private-host')
  expect(screen.getByRole('textbox',{name:'Search models'})).toHaveValue('Model 1')
  expect(HTMLAnchorElement.prototype.click.mock.instances[0].isConnected).toBe(false)
  vi.advanceTimersByTime(1000)
  expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:usage-report')
})

test('refuses stale periods and unavailable reports, then exports the latest successful refresh', async () => {
  const {rerender} = render(<UsageView {...props} loading/>)
  const button = screen.getByRole('button',{name:'Download report JSON'})
  expect(button).toBeDisabled()
  rerender(<UsageView {...props} range={{start:'2026-02-01',end:'2026-02-28'}}/>)
  expect(button).toBeDisabled()
  rerender(<UsageView {...props} report={{...report,source:{status:'unavailable'}}}/>)
  expect(button).toBeDisabled()
  fireEvent.click(button)
  expect(URL.createObjectURL).not.toHaveBeenCalled()
  rerender(<UsageView {...props} report={{...report,summary:{total_tokens:42}}}/>)
  fireEvent.click(button)
  expect(JSON.parse(await URL.createObjectURL.mock.calls[0][0].text()).summary.total_tokens).toBe(42)
})

test('releases failed activations and permits a subsequent download without changing filters', () => {
  vi.useFakeTimers()
  render(<UsageView {...props}/>)
  HTMLAnchorElement.prototype.click.mockImplementationOnce(() => {throw new Error('blocked')})
  fireEvent.click(screen.getByRole('button',{name:'Download report JSON'}))
  expect(screen.getByRole('alert')).toHaveTextContent('could not be started')
  vi.advanceTimersByTime(1000)
  expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:usage-report')
  fireEvent.click(screen.getByRole('button',{name:'Download report JSON'}))
  expect(screen.queryByRole('alert')).not.toBeInTheDocument()
})

test('preserves the actual Token Spy producer contract and separates unknown cost from local zero', async () => {
  const actual = {...fixture.report,source:{name:'token-spy',status:'ok'}}
  render(<UsageView {...props} report={actual} range={actual.period}/>)
  fireEvent.click(screen.getByRole('button',{name:'Download report JSON'}))
  const value = JSON.parse(await URL.createObjectURL.mock.calls[0][0].text())
  expect(value.summary.total_tokens).toBe(54)
  expect(value.sources.map(row => row.source)).toEqual(actual.sources.map(row => row.source))
  expect(value.sources.find(row => row.source === 'untracked').cost_usd).toBeNull()
  expect(value.models.find(row => row.cost_source === 'untracked').cost_usd).toBeNull()
  expect(value.models.find(row => row.cost_source === 'local_zero_cost').cost_usd).toBe(0)
  expect(value.models.find(row => row.cost_source === 'priced_from_tokens').cost_usd).toBe(0.125)
})

test('reports oversized snapshots before allocating a download URL', () => {
  render(<UsageView {...props} report={{...report,models:[{model:'x'.repeat(9*1024*1024)}]}}/>)
  fireEvent.click(screen.getByRole('button',{name:'Download report JSON'}))
  expect(screen.getByRole('alert')).toHaveTextContent('8 MiB')
  expect(URL.createObjectURL).not.toHaveBeenCalled()
})
