import {render, screen, fireEvent, cleanup} from '@testing-library/react'
import {afterEach, expect, it, vi} from 'vitest'
import FittedLibraryPage, {fittedPageSize, fittedPageNumbers} from './FittedLibraryPage'

afterEach(() => {cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals()})
it('keeps large catalogs to seven pagination slots with reachable neighboring pages', () => {
  expect(fittedPageNumbers(1, 3)).toEqual([1, 2, 3])
  expect(fittedPageNumbers(1, 18)).toEqual([1, 2, 3, 4, 5, 'gap-18', 18])
  expect(fittedPageNumbers(9, 18)).toEqual([1, 'gap-8', 8, 9, 10, 'gap-18', 18])
  expect(fittedPageNumbers(18, 18)).toEqual([1, 'gap-14', 14, 15, 16, 17, 18])
})
it('reserves the footer and keeps at least one item in a short panel', () => {
  expect(fittedPageSize(624, 144)).toBe(4)
  expect(fittedPageSize(50, 144)).toBe(1)
})

it('keeps a browsable group of models even when the panel is short',()=>{
  vi.stubGlobal('ResizeObserver',class {observe(){} disconnect(){}})
  vi.spyOn(HTMLElement.prototype,'getBoundingClientRect').mockReturnValue({top:0,width:440,height:200})
  vi.spyOn(HTMLElement.prototype,'clientHeight','get').mockReturnValue(200)
  render(<div className="portal-panel-content"><FittedLibraryPage minimumItems={6} label="Models" items={Array.from({length:12},(_,i)=>i)}>{items=>items.map(i=><article className="model-entry" key={i}>Model {i}</article>)}</FittedLibraryPage></div>)
  expect(screen.getAllByRole('article')).toHaveLength(6)
  fireEvent.click(screen.getByRole('button',{name:'Page 2'}))
  expect(screen.getByText('Model 11')).toBeVisible()
})
it('fits the actual utility panel and paginates without dropping entries', () => {
  vi.stubGlobal('ResizeObserver', class {observe(){} disconnect(){}})
  vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function () {
    return {top: this.classList.contains('fitted-library-page') ? 100 : 0, width: 420, height: this.classList.contains('extension-entry') ? 144 : 742}
  })
  vi.spyOn(HTMLElement.prototype, 'clientHeight', 'get').mockReturnValue(742)
  render(<div className="portal-panel-content"><FittedLibraryPage items={Array.from({length:10}, (_, i) => i)} label="Extensions">{items => items.map(i => <article className="extension-entry" key={i}>Item {i}</article>)}</FittedLibraryPage></div>)
  expect(screen.getByText('Item 3')).toBeVisible()
  expect(screen.queryByText('Item 4')).toBeNull()
  fireEvent.click(screen.getByRole('button', {name:'Page 3'}))
  expect(screen.getByText('Item 9')).toBeVisible()
  expect(screen.getByText('9–10 of 10')).toBeVisible()
})
