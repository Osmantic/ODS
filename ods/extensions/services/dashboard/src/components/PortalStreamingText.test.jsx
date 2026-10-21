import {act,render,screen} from '@testing-library/react'
import PortalStreamingText from './PortalStreamingText'
import {EMBED_NOTE,EMBED_ONLY_NOTE} from '../lib/embedDisplay'

beforeEach(()=>{
  vi.useFakeTimers()
  vi.stubGlobal('matchMedia',()=>({matches:false,addEventListener:vi.fn(),removeEventListener:vi.fn()}))
})
afterEach(()=>{vi.useRealTimers();vi.unstubAllGlobals()})
const advance=ms=>act(()=>vi.advanceTimersByTime(ms))

it('reveals a final response received in one batch rather than displaying it instantly',()=>{
  const text='Uma resposta completa recebida de uma vez, com conteúdo real e sem perder nenhuma palavra.'
  const {container}=render(<PortalStreamingText animate>{text}</PortalStreamingText>)
  expect(container.textContent).not.toBe(text)
  advance(160)
  expect(container.textContent.length).toBeGreaterThan(0)
  expect(container.textContent.length).toBeLessThan(text.length)
  advance(2000)
  expect(container.textContent).toBe(text)
  expect(container.firstChild).toHaveAttribute('aria-busy','false')
})
it('finishes revealing after transport completion and retains earlier DOM nodes',()=>{
  const {container,rerender}=render(<PortalStreamingText active>Hello</PortalStreamingText>)
  advance(160)
  const first=container.querySelector('.portal-stream-reveal')
  rerender(<PortalStreamingText active>{'Hello world 🙂'}</PortalStreamingText>)
  advance(32)
  rerender(<PortalStreamingText>{'Hello world 🙂'}</PortalStreamingText>)
  advance(300)
  expect(container.textContent).toBe('Hello world 🙂')
  expect(container.querySelector('.portal-stream-reveal')).toBe(first)
  expect(container.firstChild).toHaveAttribute('aria-busy','false')
})
it('does not replay stored answers and preserves complete Markdown code and links',()=>{
  const {container}=render(<PortalStreamingText>{'Answer [source](https://example.com).\n\n```js\nconst n = 1;\n```'}</PortalStreamingText>)
  expect(screen.getByRole('link')).toHaveAttribute('href','https://example.com')
  expect(container.querySelector('code')).toHaveTextContent('const n = 1;')
  expect(container.firstChild).toHaveAttribute('aria-busy','false')
})
it('shows the entire answer immediately with reduced motion and on stop',()=>{
  vi.stubGlobal('matchMedia',()=>({matches:true,addEventListener:vi.fn(),removeEventListener:vi.fn()}))
  const {container,rerender}=render(<PortalStreamingText active>Accessible complete text</PortalStreamingText>)
  expect(container).toHaveTextContent('Accessible complete text')
  rerender(<PortalStreamingText instant>Preserved partial response after stop</PortalStreamingText>)
  expect(container).toHaveTextContent('Preserved partial response after stop')
})
it('catches up a long burst within a bounded interval and cancels frames on unmount',()=>{
  const text='Long answer. '.repeat(1500).trimEnd()
  const {container,unmount}=render(<PortalStreamingText animate>{text}</PortalStreamingText>)
  advance(1600)
  expect(container.textContent).toBe(text)
  unmount()
  expect(vi.getTimerCount()).toBe(0)
})
it('does not replay delivered prose when a publication receipt is stripped',()=>{
  const {container,rerender}=render(<PortalStreamingText animate>{'The page is ready.\n\nPublished from your workspace.'}</PortalStreamingText>)
  advance(1500)
  const first=container.querySelector('.portal-stream-reveal')
  rerender(<PortalStreamingText animate>The page is ready.</PortalStreamingText>)
  expect(container.textContent).toBe('The page is ready.')
  expect(container.querySelector('.portal-stream-reveal')).toBe(first)
  expect(container.firstChild).toHaveAttribute('aria-busy','false')
})
it('mutes only paragraphs that are exactly a Portal note about an embed it cannot display',()=>{
  const source=`Before\n\n${EMBED_NOTE}\n\n- Item\n\n  ${EMBED_ONLY_NOTE}\n\nThe model wrote ${EMBED_NOTE} inline.\n\n\`\`\`\n${EMBED_NOTE}\n\`\`\``
  const {container}=render(<PortalStreamingText>{source}</PortalStreamingText>)
  const muted=[...container.querySelectorAll('.portal-embed-note')].map(element=>[element.tagName,element.textContent])
  expect(muted).toEqual([['P',EMBED_NOTE],['P',EMBED_ONLY_NOTE]])
  expect(screen.getByText('Before').closest('p')).not.toHaveClass('portal-embed-note')
  expect(container.querySelector('pre code')).toHaveTextContent(EMBED_NOTE)
})

it('renders a document download image label as text before a receipt arrives',()=>{
  const href='https://untrusted.example/api/download?siteId=invented'
  const {container}=render(<PortalStreamingText active instant>{`[![Download report.csv](${href})](${href})`}</PortalStreamingText>)
  expect(screen.getByRole('link',{name:/Download report.csv/})).toHaveAttribute('href',href)
  expect(container.querySelector('img')).toBeNull()
  expect(screen.queryByRole('button',{name:/Download verified/})).toBeNull()
})
