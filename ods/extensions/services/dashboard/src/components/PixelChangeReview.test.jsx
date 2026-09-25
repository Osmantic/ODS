import {act, render, screen, fireEvent, within} from '@testing-library/react'
import PixelFileChanges from './PixelFileChanges'

const changes = [
  {path:'src/app.js',change:'modified',additions:1,deletions:0,truncated:false,diff:[{type:'add',text:'console.log(42)',oldLine:null,newLine:1}]},
  {path:'src/new.js',change:'created',additions:1,deletions:0,truncated:true,diff:[{type:'add',text:'export default 1',oldLine:null,newLine:1}]},
  {path:'old.css',change:'deleted',additions:0,deletions:1,truncated:false,diff:[{type:'remove',text:'.old {}',oldLine:1,newLine:null}]},
]
afterEach(() => vi.unstubAllGlobals())
it('filters folder navigation while preserving the selected diff and original changes', () => {
  const original = JSON.stringify(changes)
  render(<PixelFileChanges changes={changes}/>)
  const tree = screen.getByRole('navigation',{name:'Changed files'})
  fireEvent.change(screen.getByLabelText('Filter changed files'), {target:{value:'src/new'}})
  expect(within(tree).queryByRole('button',{name:'Open src/app.js'})).toBeNull()
  expect(within(tree).getByRole('button',{name:'Folder src'})).toHaveAttribute('aria-expanded','true')
  expect(within(tree).getByRole('button',{name:'Open src/new.js'})).toBeVisible()
  expect(screen.getByRole('region',{name:'Changes to src/app.js'})).toBeVisible()
  expect(screen.queryByLabelText('Change kind')).toBeNull()
  expect(screen.queryByRole('button',{name:'Expand all changes'})).toBeNull()
  fireEvent.change(screen.getByLabelText('Filter changed files'), {target:{value:''}})
  expect(within(tree).getByRole('button',{name:'Open old.css'})).toBeVisible()
  expect(JSON.stringify(changes)).toBe(original)
})
it('selects one real diff, retains truncation warnings and opens the original file', () => {
  const onOpenFile = vi.fn(), onSelectFile = vi.fn()
  render(<PixelFileChanges changes={changes} onOpenFile={onOpenFile} onSelectFile={onSelectFile}/>)
  fireEvent.click(screen.getByRole('button',{name:'Open src/new.js'}))
  expect(onSelectFile).toHaveBeenCalledWith('src/new.js',changes[1])
  expect(screen.queryByRole('region',{name:'Changes to src/app.js'})).toBeNull()
  expect(screen.getByRole('button',{name:'Open src/new.js'})).toHaveAttribute('aria-current','true')
  expect(screen.getByText(/Only part of this diff is displayed/)).toBeVisible()
  fireEvent.click(screen.getByRole('button',{name:'Open file src/new.js'}))
  expect(onOpenFile).toHaveBeenCalledWith(changes[1])
  fireEvent.click(screen.getByRole('button',{name:'Open old.css'}))
  expect(screen.getByRole('region',{name:'Changes to old.css'})).toBeVisible()
  expect(screen.queryByRole('button',{name:'Open file old.css'})).toBeNull()
})
it('recovers from empty filters and refreshed changes', () => {
  const {rerender} = render(<PixelFileChanges changes={changes} selectedPath="src/new.js"/>)
  fireEvent.change(screen.getByLabelText('Filter changed files'), {target:{value:'missing'}})
  expect(screen.getByText('No files match this filter.')).toBeVisible()
  expect(screen.getByRole('region',{name:'Changes to src/new.js'})).toBeVisible()
  rerender(<PixelFileChanges changes={[changes[0]]} selectedPath="src/new.js"/>)
  expect(screen.getByRole('region',{name:'Changes to src/app.js'})).toBeVisible()
  fireEvent.change(screen.getByLabelText('Filter changed files'), {target:{value:''}})
  expect(screen.getByRole('button',{name:'Open src/app.js'})).toHaveAttribute('aria-current','true')
})
it('collapses folders independently and hides or reopens the navigation', () => {
  render(<PixelFileChanges changes={changes}/>)
  const folder = screen.getByRole('button',{name:'Folder src'})
  fireEvent.click(folder)
  expect(folder).toHaveAttribute('aria-expanded','false')
  expect(screen.queryByRole('button',{name:'Open src/app.js'})).toBeNull()
  expect(screen.getByRole('region',{name:'Changes to src/app.js'})).toBeVisible()
  fireEvent.click(folder)
  expect(screen.getByRole('button',{name:'Open src/app.js'})).toBeVisible()
  fireEvent.click(screen.getByRole('button',{name:'Toggle changed files'}))
  expect(screen.queryByRole('navigation')).toBeNull()
  expect(screen.getByRole('button',{name:'Toggle changed files'})).toHaveAttribute('aria-expanded','false')
  fireEvent.click(screen.getByRole('button',{name:'Toggle changed files'}))
  expect(screen.getByRole('navigation')).toBeVisible()
})
it('compacts single-child folder chains and honors external selection', () => {
  const nested = {...changes[0],path:'ods/extensions/plugin/activity.js'}
  const {rerender} = render(<PixelFileChanges changes={[nested,changes[2]]} selectedPath="old.css"/>)
  expect(screen.getByRole('button',{name:'Folder ods/extensions/plugin'})).toHaveTextContent('ods/extensions/plugin')
  expect(screen.getByRole('region',{name:'Changes to old.css'})).toBeVisible()
  rerender(<PixelFileChanges changes={[nested,changes[2]]} selectedPath={nested.path}/>)
  expect(screen.getByRole('region',{name:`Changes to ${nested.path}`})).toBeVisible()
})
it('keeps narrow panels readable and closes the file drawer after selection', () => {
  let resize
  vi.stubGlobal('ResizeObserver',class {
    constructor(callback) { resize = callback }
    observe() {}
    disconnect() {}
  })
  render(<PixelFileChanges changes={changes}/>)
  act(() => resize([{contentRect:{width:400}}]))
  expect(screen.queryByRole('navigation')).toBeNull()
  expect(screen.getByRole('region',{name:'Changes to src/app.js'})).toBeVisible()
  fireEvent.click(screen.getByRole('button',{name:'Toggle changed files'}))
  expect(screen.getByRole('navigation',{name:'Changed files'})).toBeVisible()
  fireEvent.click(screen.getByRole('button',{name:'Open src/new.js'}))
  expect(screen.queryByRole('navigation')).toBeNull()
  expect(screen.getByRole('region',{name:'Changes to src/new.js'})).toBeVisible()
})
