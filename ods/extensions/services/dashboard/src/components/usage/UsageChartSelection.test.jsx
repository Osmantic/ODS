import {fireEvent, render, screen, within} from '@testing-library/react'
import UsageView from './UsageView'

const report = {source:{status:'ok'},summary:{total_tokens:30},daily:[{date:'2026-01-01',input_tokens:10,requests:1},{date:'2026-01-02',input_tokens:20,requests:2}]}
const props = {report,readiness:{status:'ready'},range:{start:'2026-01-01'}}
const chart = () => within(screen.getByRole('region', {name:'Tokens per day'}))

test('keeps a tapped day readable after pointer departure and supports explicit clearing', () => {
  render(<UsageView {...props}/>)
  const first = chart().getByRole('button', {name:/Jan 1:/})
  fireEvent.click(first)
  fireEvent.mouseLeave(first)
  fireEvent.blur(first)
  expect(first).toHaveAttribute('aria-pressed','true')
  expect(chart().getByRole('status')).toHaveTextContent('Jan 1 · Input: 10')
  fireEvent.mouseEnter(chart().getByRole('button', {name:/Jan 2:/}))
  expect(chart().getByRole('status')).toHaveTextContent('Jan 1 · Input: 10')
  fireEvent.click(chart().getByRole('button', {name:'Clear selected day'}))
  expect(first).toHaveAttribute('aria-pressed','false')
  expect(chart().getByRole('status')).toHaveTextContent('Hover or focus')
})

test.each(['Enter',' '])('supports %s activation and Escape dismissal without changing telemetry', key => {
  render(<UsageView {...props}/>)
  const second = chart().getByRole('button', {name:/Jan 2:/})
  fireEvent.keyDown(second, {key})
  fireEvent.blur(second)
  expect(second).toHaveAttribute('aria-pressed','true')
  expect(chart().getByRole('status')).toHaveTextContent('Input: 20')
  fireEvent.keyDown(second, {key:'Escape'})
  expect(chart().queryByRole('button', {name:'Clear selected day'})).not.toBeInTheDocument()
  expect(report.daily[1].input_tokens).toBe(20)
})

test('refreshes a pinned day from current data and clears it when that day disappears', () => {
  const {rerender} = render(<UsageView {...props}/>)
  fireEvent.click(chart().getByRole('button', {name:/Jan 1:/}))
  rerender(<UsageView {...props} report={{...report,daily:[{...report.daily[0],input_tokens:99}]}}/>)
  expect(chart().getByRole('status')).toHaveTextContent('Input: 99')
  rerender(<UsageView {...props} report={{...report,daily:[report.daily[1]]}}/>)
  expect(chart().queryByRole('button', {name:'Clear selected day'})).not.toBeInTheDocument()
  expect(chart().getByRole('status')).not.toHaveTextContent('Jan 1')
})
