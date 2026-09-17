import {act,fireEvent,render,screen} from '@testing-library/react'
import PixelFileChanges from './PixelFileChanges'

const changes=[{path:'src/app.js',change:'modified',additions:1,deletions:0,diff:[{type:'add',text:'export const ready = true',oldLine:null,newLine:1}]}]
let observed
beforeEach(()=>{
  observed=new Map()
  vi.stubGlobal('ResizeObserver',class {
    constructor(callback){this.callback=callback}
    observe(element){observed.set(element,this.callback)}
    disconnect(){}
  })
  vi.stubGlobal('PointerEvent',class extends MouseEvent {
    constructor(type,options){super(type,options);this.pointerId=options.pointerId;this.pointerType=options.pointerType || 'mouse'}
  })
})
afterEach(()=>vi.unstubAllGlobals())
function resize(container,width) {
  act(()=>observed.get(container)([{contentRect:{width}}]))
}
function capture(handle) {
  let captured=null
  handle.setPointerCapture=vi.fn(id=>{captured=id})
  handle.hasPointerCapture=id=>captured===id
  handle.releasePointerCapture=vi.fn(()=>{captured=null})
}

it('drags the actual review tree with mouse or touch and stops on cancel',()=>{
  render(<PixelFileChanges changes={changes}/>)
  const panel=screen.getByLabelText('File changes')
  resize(panel,800)
  const separator=screen.getByRole('separator',{name:'Resize changed file list'})
  capture(separator)
  fireEvent.pointerDown(separator,{pointerId:3,clientX:576,button:0,pointerType:'touch'})
  fireEvent.pointerMove(separator,{pointerId:3,clientX:500})
  expect(separator).toHaveAttribute('aria-valuenow','300')
  expect(panel.style.getPropertyValue('--portal-file-tree-width')).toBe('300px')
  expect(separator.setPointerCapture).toHaveBeenCalledWith(3)
  fireEvent.pointerMove(separator,{pointerId:4,clientX:0})
  expect(separator).toHaveAttribute('aria-valuenow','300')
  fireEvent.pointerCancel(separator,{pointerId:3})
  fireEvent.pointerMove(separator,{pointerId:3,clientX:400})
  expect(separator).toHaveAttribute('aria-valuenow','300')
  expect(separator.releasePointerCapture).toHaveBeenCalledWith(3)
  fireEvent.pointerDown(separator,{pointerId:5,clientX:500,button:0})
  fireEvent.pointerMove(separator,{pointerId:5,clientX:560})
  fireEvent.pointerUp(separator,{pointerId:5})
  expect(separator).toHaveAttribute('aria-valuenow','240')
  expect(screen.getByLabelText('Diff for src/app.js')).toHaveTextContent('export const ready = true')
})

it('provides keyboard bounds that preserve the code area when the container shrinks',()=>{
  render(<PixelFileChanges changes={changes}/>)
  const panel=screen.getByLabelText('File changes')
  resize(panel,800)
  const separator=screen.getByRole('separator')
  fireEvent.keyDown(separator,{key:'End'})
  expect(separator).toHaveAttribute('aria-valuenow','520')
  expect(separator).toHaveAttribute('aria-valuemax','520')
  fireEvent.keyDown(separator,{key:'ArrowLeft'})
  expect(separator).toHaveAttribute('aria-valuenow','520')
  resize(panel,640)
  expect(separator).toHaveAttribute('aria-valuenow','360')
  resize(panel,800)
  expect(separator).toHaveAttribute('aria-valuenow','520')
  fireEvent.keyDown(separator,{key:'Home'})
  fireEvent.keyDown(separator,{key:'ArrowRight'})
  expect(separator).toHaveAttribute('aria-valuenow','180')
  fireEvent.keyDown(separator,{key:'ArrowLeft',shiftKey:true})
  expect(separator).toHaveAttribute('aria-valuenow','220')
  fireEvent.click(screen.getByRole('button',{name:'Toggle changed files'}))
  expect(screen.queryByRole('separator')).toBeNull()
  fireEvent.click(screen.getByRole('button',{name:'Toggle changed files'}))
  expect(screen.getByRole('separator')).toHaveAttribute('aria-valuenow','220')
})

it('releases an active drag and uses a selectable drawer on narrow panels',()=>{
  render(<PixelFileChanges changes={changes}/>)
  const panel=screen.getByLabelText('File changes')
  resize(panel,800)
  const separator=screen.getByRole('separator')
  capture(separator)
  fireEvent.pointerDown(separator,{pointerId:7,clientX:576,button:0})
  resize(panel,400)
  expect(separator.releasePointerCapture).toHaveBeenCalledWith(7)
  expect(screen.queryByRole('separator')).toBeNull()
  expect(panel).toHaveClass('is-narrow')
  expect(screen.queryByRole('navigation')).toBeNull()
  fireEvent.click(screen.getByRole('button',{name:'Toggle changed files'}))
  expect(screen.getByRole('navigation',{name:'Changed files'})).toBeVisible()
  expect(screen.queryByRole('separator')).toBeNull()
  fireEvent.click(screen.getByRole('button',{name:'Open src/app.js'}))
  expect(screen.queryByRole('navigation')).toBeNull()
  resize(panel,800)
  fireEvent.click(screen.getByRole('button',{name:'Toggle changed files'}))
  expect(screen.getByRole('separator')).toHaveAttribute('aria-valuenow','224')
})
