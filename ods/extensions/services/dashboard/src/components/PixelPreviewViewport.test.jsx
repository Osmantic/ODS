import {render,screen} from '@testing-library/react'
import PixelPreviewViewport from './PixelPreviewViewport'

it('fills the preview without viewport controls and retains iframe isolation',()=>{
 const access={frameUrl:'/pixel-preview/test/',sandbox:'allow-scripts',route:'owner'}
 const {container,rerender}=render(<PixelPreviewViewport access={access} title="My site" compact/>)
 const frame=screen.getByTitle('My site')
 expect(frame).toHaveAttribute('sandbox','allow-scripts')
 expect(frame).toHaveAttribute('referrerpolicy','no-referrer')
 expect(frame).toHaveStyle({width:'100%',height:'100%'})
 expect(screen.queryByText('Viewport')).toBeNull()
 expect(screen.queryByRole('combobox')).toBeNull()
 rerender(<PixelPreviewViewport access={access} title="My site" hidden compact/>)
 expect(frame).not.toBeVisible()
 expect(container.querySelector('iframe')).toBe(frame)
})
