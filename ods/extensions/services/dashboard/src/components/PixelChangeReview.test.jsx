import {render, screen, fireEvent, waitFor} from '@testing-library/react'
import PixelFileChanges from './PixelFileChanges'

const changes = [
  {path:'src/app.js',change:'modified',additions:1,deletions:0,truncated:false,diff:[{type:'add',text:'console.log(42)',oldLine:null,newLine:1}]},
  {path:'src/new.js',change:'created',additions:1,deletions:0,truncated:true,diff:[{type:'add',text:'export default 1',oldLine:null,newLine:1}]},
  {path:'old.css',change:'deleted',additions:0,deletions:1,truncated:false,diff:[{type:'remove',text:'.old {}',oldLine:1,newLine:null}]},
]
it('combines changed-file and operation filters while preserving the full change set', () => {
  const original = JSON.stringify(changes)
  render(<PixelFileChanges changes={changes}/>)
  fireEvent.change(screen.getByLabelText('Filter changed files'), {target:{value:'src/'}})
  expect(screen.getByText('Showing 2 of 3 changed files')).toBeVisible()
  fireEvent.change(screen.getByLabelText('Change kind'), {target:{value:'created'}})
  expect(screen.queryByText('Edited src/app.js')).toBeNull()
  expect(screen.getByText('Created src/new.js')).toBeVisible()
  expect(screen.getByText('Showing 1 of 3 changed files')).toBeVisible()
  fireEvent.click(screen.getByRole('button', {name:'Clear change filters'}))
  expect(screen.getByText('Deleted old.css')).toBeVisible()
  expect(JSON.stringify(changes)).toBe(original)
})
it('expands/collapses visible changes, keeps truncation warnings and preserves preview identity', async () => {
  const preview = vi.fn()
  const {container} = render(<PixelFileChanges changes={changes} onPreview={preview}/>)
  fireEvent.click(screen.getByRole('button', {name:'Expand all changes'}))
  await waitFor(() => expect(container.querySelectorAll('details[open]')).toHaveLength(3))
  expect(screen.getByText(/Only part of this diff is displayed/)).toBeVisible()
  fireEvent.click(screen.getByRole('button', {name:'Preview src/app.js'}))
  expect(preview).toHaveBeenCalledWith(changes[0])
  expect(screen.queryByRole('button', {name:'Preview old.css'})).toBeNull()
  fireEvent.click(screen.getByRole('button', {name:'Collapse all changes'}))
  await waitFor(() => expect(container.querySelectorAll('details[open]')).toHaveLength(0))
})
it('shows recoverable empty filter results and keeps a single-file diff usable', () => {
  const {rerender} = render(<PixelFileChanges changes={changes}/>)
  fireEvent.change(screen.getByLabelText('Change kind'), {target:{value:'published'}})
  expect(screen.getByText('No changed files match these filters.')).toBeVisible()
  rerender(<PixelFileChanges changes={[changes[0]]}/>)
  expect(screen.getByLabelText('Change kind')).toHaveValue('published')
  fireEvent.click(screen.getByRole('button', {name:'Clear change filters'}))
  expect(screen.getByText('Edited src/app.js')).toBeVisible()
  expect(screen.queryByLabelText('Filter changed files')).toBeNull()
})
