
import {render,screen,fireEvent,waitFor,act} from '@testing-library/react'
import {OperationsApprovalCard} from './Pixel'
const jobId='ops-1788127319657-f3262c99a419'
const planHash='e'.repeat(64)
const command='/opt/ods/bin/ods-pixel-approve '+jobId+' '+planHash+' --confirm'
const content='Pixel prepared a protected ODS host command plan, but external approval is required. No command was executed. Job: '+jobId+'. Plan SHA-256: '+planHash+'.'
const odsContent='ODS prepared a protected ODS host command plan, but external approval is required. No command was executed. Job: '+jobId+'. Plan SHA-256: '+planHash+'.'
beforeEach(() => {
  vi.stubGlobal('fetch',vi.fn(async () => ({ok:true,json:async () => ({
    schemaVersion:1,kind:'ods-pixel-operations-status',jobId,planHash,status:'awaiting-approval',
    riskTier:'break-glass',approvalRequired:true,updatedAt:'2026-09-03T02:00:00Z',approvalCommand:command,
  })})))
})
afterEach(() => {vi.unstubAllGlobals();vi.restoreAllMocks()})
test.each(['missing','denied'])('provides the verified command for manual copy when clipboard is %s',async mode => {
  const previous=Object.getOwnPropertyDescriptor(navigator,'clipboard')
  Object.defineProperty(navigator,'clipboard',{configurable:true,value:mode === 'missing' ? undefined : {writeText:vi.fn().mockRejectedValue(new Error('denied'))}})
  try {
    render(<OperationsApprovalCard content={content}/>)
    fireEvent.click(await screen.findByRole('button',{name:'Copy secure approval command'}))
    expect(await screen.findByRole('alert')).toHaveTextContent('Clipboard access failed')
    expect(screen.getByRole('textbox',{name:'Secure approval command'})).toHaveValue(command)
    expect(screen.queryByText('Copied')).toBeNull()
    expect(fetch.mock.calls.every(([,options]) => !options?.method || options.method === 'GET')).toBe(true)
  } finally {if(previous) Object.defineProperty(navigator,'clipboard',previous);else delete navigator.clipboard}
})
test('waits for clipboard confirmation and prevents repeated writes',async () => {
  let resolve
  const writeText=vi.fn(() => new Promise(done => {resolve=done}))
  const previous=Object.getOwnPropertyDescriptor(navigator,'clipboard')
  Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText}})
  try {
    render(<OperationsApprovalCard content={content}/>)
    const copy=await screen.findByRole('button',{name:'Copy secure approval command'})
    fireEvent.click(copy);fireEvent.click(copy)
    expect(copy).toBeDisabled()
    expect(writeText).toHaveBeenCalledTimes(1)
    expect(screen.queryByText('Copied')).toBeNull()
    await act(async () => resolve())
    await waitFor(() => expect(screen.getByRole('button',{name:'Copied'})).toBeEnabled())
  } finally {if(previous) Object.defineProperty(navigator,'clipboard',previous);else delete navigator.clipboard}
})
test('renders the same approval action for a new ODS receipt', async () => {
  render(<OperationsApprovalCard content={odsContent}/>)
  expect(await screen.findByRole('button',{name:'Copy secure approval command'})).toBeEnabled()
  expect(fetch).toHaveBeenCalledWith(
    `/api/pixel/ops/${jobId}?plan_hash=${planHash}`,
    expect.objectContaining({signal: expect.any(AbortSignal)}),
  )
})
