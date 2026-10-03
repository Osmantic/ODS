import {fireEvent,render,screen,within} from '@testing-library/react'
import PortalFileTree from './PortalFileTree'

const files=[{path:'index.html',change:'published'},{path:'src/main.js',change:'modified'},{path:'assets/icons/logo.svg',change:'created'}]

it('shows the real project root as independently expandable folder levels and keeps relative file actions',()=>{
  const select=vi.fn()
  render(<PortalFileTree files={files} rootPath="Playground/BubbleNet" onSelectFile={select}/>)
  const playground=screen.getByRole('button',{name:'Folder Playground'})
  const project=screen.getByRole('button',{name:'Folder Playground/BubbleNet'})
  expect(playground).toHaveTextContent(/^Playground$/)
  expect(project).toHaveTextContent(/^BubbleNet$/)
  expect(within(playground.closest('li')).getByRole('button',{name:'Folder Playground/BubbleNet'})).toBe(project)
  expect(screen.getByRole('button',{name:'Folder Playground/BubbleNet/assets/icons'})).toHaveTextContent('assets/icons')
  fireEvent.click(project)
  expect(project).toHaveAttribute('aria-expanded','false')
  expect(playground).toHaveAttribute('aria-expanded','true')
  expect(screen.queryByRole('button',{name:'Open index.html'})).toBeNull()
  fireEvent.click(project)
  fireEvent.click(screen.getByRole('button',{name:'Open src/main.js'}))
  expect(select).toHaveBeenCalledWith('src/main.js',files[1])
  expect(files[1].path).toBe('src/main.js')
})

it('opens selected ancestors and supports filtering across the displayed root without changing file paths',()=>{
  const {rerender}=render(<PortalFileTree files={files} rootPath="Playground/BubbleNet" selectedPath="index.html"/>)
  fireEvent.click(screen.getByRole('button',{name:'Folder Playground'}))
  expect(screen.queryByRole('button',{name:'Open src/main.js'})).toBeNull()
  rerender(<PortalFileTree files={files} rootPath="Playground/BubbleNet" selectedPath="src/main.js"/>)
  expect(screen.getByRole('button',{name:'Folder Playground'})).toHaveAttribute('aria-expanded','true')
  expect(screen.getByRole('button',{name:'Open src/main.js'})).toHaveAttribute('aria-current','true')
  fireEvent.change(screen.getByRole('searchbox'),{target:{value:'BubbleNet/src'}})
  expect(screen.getByRole('button',{name:'Open src/main.js'})).toBeVisible()
  expect(screen.queryByRole('button',{name:'Open index.html'})).toBeNull()
  expect(screen.getByRole('button',{name:'Folder Playground/BubbleNet'})).toBeDisabled()
})

it('uses the actual legacy location without inventing a Playground parent',()=>{
  const {rerender}=render(<PortalFileTree files={files} rootPath="pacman-game"/>)
  expect(screen.getByRole('button',{name:'Folder pacman-game'})).toHaveTextContent(/^pacman-game$/)
  expect(screen.queryByRole('button',{name:'Folder Playground'})).toBeNull()
  rerender(<PortalFileTree files={files} rootPath={'work\\older-demo'}/>)
  expect(screen.getByRole('button',{name:'Folder work'})).toHaveTextContent(/^work$/)
  expect(screen.getByRole('button',{name:'Folder work/older-demo'})).toHaveTextContent(/^older-demo$/)
  expect(screen.queryByRole('button',{name:'Folder Playground'})).toBeNull()
})
