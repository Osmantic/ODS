import { render, screen, within, cleanup, fireEvent } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { Cpu } from 'lucide-react'
import CompactDashboard from './CompactDashboard'
afterEach(() => {cleanup();vi.restoreAllMocks();vi.unstubAllGlobals()})
it('fills available height beyond twelve services and reduces the page count', () => {
  vi.stubGlobal('ResizeObserver',class {observe(){} disconnect(){}})
  vi.spyOn(HTMLElement.prototype,'getBoundingClientRect').mockImplementation(function () {
    return {top:this.classList.contains('dashboard-service-list') ? 80 : 0,height:this.tagName === 'SUMMARY' ? 36 : 700}
  })
  vi.spyOn(HTMLElement.prototype,'clientHeight','get').mockReturnValue(700)
  render(<div className="portal-panel-content"><CompactDashboard health={{text:'Online'}} metrics={[]} services={Array.from({length:27},(_,index) => ({id:`s-${index}`,name:`Service ${index}`,status:'healthy'}))}/></div>)
  expect(screen.getByText('Service 14')).toBeVisible()
  expect(screen.queryByText('Service 15')).toBeNull()
  expect(screen.queryByRole('button',{name:'Page 3',exact:true})).toBeNull()
  fireEvent.click(screen.getByRole('button',{name:'Page 2',exact:true}))
  expect(screen.getByText('Service 26')).toBeVisible()
})
it('paginates services and replaces the visible page with standard status dots', () => {
  const {container} = render(<CompactDashboard health={{text:'Online'}} metrics={[]} services={Array.from({length:14},(_,index) => ({id:`service-${index}`,name:`Service ${index}`,status:'healthy'}))}/>)
  expect(screen.getByText('Service 0')).toBeVisible()
  expect(screen.queryByText('Service 6')).toBeNull()
  fireEvent.click(screen.getByRole('button',{name:'Page 2',exact:true}))
  expect(screen.getByText('Service 6')).toBeVisible()
  expect(screen.queryByText('Service 0')).toBeNull()
  expect(screen.getByRole('button',{name:'Page 2',exact:true})).toHaveAttribute('aria-current','page')
  expect(container.querySelector('feTurbulence')).toBeNull()
})
it('uses real percentages, neutral meters and readable status labels', () => {
  render(<CompactDashboard health={{text:'1/2 core services online.'}} services={[{id:'one',name:'First',required:true,status:'healthy'},{id:'two',name:'Second',required:true,status:'down'},{id:'three',name:'Third',status:'not_deployed'}]} metrics={[{icon:Cpu,label:'CPU',value:'25%',percent:25},{icon:Cpu,label:'Temperature',value:'—'}]}/>)
  expect(screen.getByRole('progressbar',{name:'Services online'})).toHaveAttribute('aria-valuenow','50')
  expect(screen.getByRole('progressbar',{name:'CPU utilization'})).toHaveAttribute('aria-valuenow','25')
  expect(screen.queryByRole('progressbar',{name:'Temperature utilization'})).toBeNull()
  expect(screen.getByText('not deployed',{selector:'.dashboard-status-badge'})).toBeVisible()
  const row = screen.getByText('First').closest('details')
  expect(within(row).getByText('healthy',{selector:'.dashboard-status-badge'})).toBeVisible()
})
it('does not fabricate a percentage without telemetry', () => {
  render(<CompactDashboard health={{text:'Waiting for telemetry…'}} services={[]} metrics={[]}/>)
  expect(screen.queryByRole('progressbar')).toBeNull()
  expect(screen.getByText('No service telemetry available.')).toBeVisible()
})
