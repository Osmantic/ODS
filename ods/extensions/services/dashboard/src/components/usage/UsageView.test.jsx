import {csvForRows,tokens} from './UsageView'
import UsageView from './UsageView'
import {render,screen,fireEvent,within} from '@testing-library/react'

function show(report) {
  return render(<UsageView report={{source:{status:'ok'},summary:{},...report}} readiness={{status:'ready'}} range={{start:'2026-05-01'}} />)
}

test.each(['All Providers','All Services','All Sources'])('keeps missing metadata rows when filtering %s as unknown',label=>{
  show({models:[{model:'Missing metadata',input_tokens:10}]})
  fireEvent.click(screen.getByRole('button',{name:'Models',exact:true}))
  fireEvent.click(screen.getByText('Filters'))
  fireEvent.change(screen.getByLabelText(label),{target:{value:'unknown'}})
  expect(screen.getByText('Missing metadata')).toBeVisible()
})

test.each([undefined,'new_source','priced_from_tokens'])('never displays an unsupported or missing cost as zero (%s)',cost_source=>{
  show({models:[{model:'No price',input_tokens:10,cost_source}]})
  fireEvent.click(screen.getByRole('button',{name:'Models',exact:true}))
  fireEvent.click(screen.getByText('No price'))
  expect(screen.getByText('Cost').nextElementSibling).toHaveTextContent('—')
})

test('shows unavailable request counters consistently in daily values, models and services',()=>{
  show({source:{status:'ok',local_runtime:{request_count_available:false}},summary:{requests:0,total_tokens:10},daily:[{date:'2026-05-01',input_tokens:10,requests:0}],models:[{model:'Local model',input_tokens:10,requests:0}],services:[{service:'Local service',input_tokens:10,requests:0}]})
  fireEvent.click(screen.getByText('View daily values'))
  const row=within(screen.getByRole('table')).getAllByRole('row')[1]
  expect(within(row).getAllByRole('cell').at(-1)).toHaveTextContent('—')
  fireEvent.click(screen.getByRole('button',{name:'Models',exact:true}))
  fireEvent.click(screen.getByText('Local model'))
  expect(screen.getAllByText('Requests').at(-1).nextElementSibling).toHaveTextContent('—')
  fireEvent.click(screen.getByRole('button',{name:'Services',exact:true}))
  expect(screen.getByText('Request count unavailable')).toBeVisible()
})

test('exports all token fields, escapes quotes and prevents spreadsheet formulas',()=>{
  const csv=csvForRows([{model:'=HYPERLINK("test")',service:'a,b',input_tokens:1,output_tokens:2,cache_read_tokens:3,cache_write_tokens:4}])
  expect(csv).toContain('cache_write_tokens')
  expect(csv).toContain('"\'=HYPERLINK(""test"")"')
  expect(csv).toContain('"a,b"')
  expect(csv).toContain('"1","2","3","4"')
})
test('includes both cache read and cache write in totals',()=>{
  expect(tokens({input_tokens:1,output_tokens:2,cache_read_tokens:3,cache_write_tokens:4})).toBe(10)
})
