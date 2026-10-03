import {cleanup, render, screen, fireEvent} from '@testing-library/react'
import UsageView from './UsageView'

function show() {
  render(<UsageView report={{source:{status:'ok'}, summary:{}, models:[
    {model:'Selected model', provider:'local', input_tokens:42},
    {model:'Other model', provider:'cloud', input_tokens:10},
  ]}} readiness={{status:'ready'}} range={{start:'2026-05-01'}}/>)
  fireEvent.click(screen.getByRole('button', {name:'Models', exact:true}))
  fireEvent.change(screen.getByLabelText('Search models'), {target:{value:'Selected'}})
}
beforeEach(() => {
  vi.useFakeTimers()
  vi.stubGlobal('URL', {createObjectURL:vi.fn(() => 'blob:usage-csv'), revokeObjectURL:vi.fn()})
})
afterEach(() => {cleanup(); vi.runOnlyPendingTimers(); vi.useRealTimers(); vi.restoreAllMocks(); vi.unstubAllGlobals()})

it('activates a connected download link and releases its URL after activation', () => {
  const clicked = []
  vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function () {
    clicked.push({connected:this.isConnected, href:this.href, download:this.download})
  })
  show()
  fireEvent.click(screen.getByRole('button', {name:'Export CSV'}))
  expect(clicked).toEqual([{connected:true, href:'blob:usage-csv', download:'ods-usage-by-model.csv'}])
  expect(document.querySelector('a[download]')).toBeNull()
  expect(URL.revokeObjectURL).not.toHaveBeenCalled()
  vi.advanceTimersByTime(1000)
  expect(URL.revokeObjectURL).toHaveBeenCalledExactlyOnceWith('blob:usage-csv')
  expect(URL.createObjectURL.mock.calls[0][0]).toBeInstanceOf(Blob)
})

it('reports allocation failure and allows a fresh export without changing filters', () => {
  URL.createObjectURL.mockImplementationOnce(() => {throw new Error('allocation denied')})
  vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
  show()
  fireEvent.click(screen.getByRole('button', {name:'Export CSV'}))
  expect(screen.getByRole('alert')).toHaveTextContent('CSV export could not be started')
  expect(screen.getByLabelText('Search models')).toHaveValue('Selected')
  fireEvent.click(screen.getByRole('button', {name:'Export CSV'}))
  expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  expect(URL.createObjectURL).toHaveBeenCalledTimes(2)
})

it('releases resources and reports failure if browser download activation throws', () => {
  vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {throw new Error('blocked')})
  show()
  fireEvent.click(screen.getByRole('button', {name:'Export CSV'}))
  expect(screen.getByRole('alert')).toHaveTextContent('CSV export could not be started')
  expect(document.querySelector('a[download]')).toBeNull()
  vi.advanceTimersByTime(1000)
  expect(URL.revokeObjectURL).toHaveBeenCalledExactlyOnceWith('blob:usage-csv')
})
