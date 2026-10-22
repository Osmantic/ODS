import {act,render,screen} from '@testing-library/react'
import PortalStreamingText from './PortalStreamingText'
import {EMBED_NOTE,EMBED_ONLY_NOTE} from '../lib/embedDisplay'

const mediaReceipt=(digit='a')=>({schemaVersion:1,kind:'ods-pixel-workspace-artifact',relativePath:'report.csv',
  siteId:`site-${digit.repeat(24)}`,sha256:digit.repeat(64),file:{path:'report.csv',bytes:37,sha256:'c'.repeat(64)}})

it.each([
  'MEDIA:report.csv',
  'Created report.csv.\n\nMEDIA:report.csv\n\nSum: 225.',
  'Created report.csv.\nMEDIA:report.csv\nSum: 225.',
  'Created report.csv.\r\nMEDIA:report.csv\r\nSum: 225.',
  'Created **report.csv**.\nMEDIA:report.csv\nSum: 225.',
  'Created &amp; verified.\nMEDIA:report.csv\nSum: 225.',
])('hides a delivered MEDIA line while preserving the rest of the answer: %s',source=>{
  const receipts=Object.freeze([Object.freeze(mediaReceipt())])
  const before=JSON.stringify(receipts)
  const {container}=render(<PortalStreamingText artifacts={receipts}>{source}</PortalStreamingText>)
  expect(container.textContent).not.toContain('MEDIA:report.csv')
  if(source.includes('Created')) expect(container).toHaveTextContent('Created')
  if(source.includes('Sum:')) expect(container).toHaveTextContent('Sum: 225.')
  expect(container.querySelector('a,img,button')).toBeNull()
  expect(JSON.stringify(receipts)).toBe(before)
})

it.each([
  'See MEDIA:report.csv for details.',
  ' MEDIA:report.csv', '  MEDIA:report.csv', '   MEDIA:report.csv', '    MEDIA:report.csv',
  '> MEDIA:report.csv', '> Example:\nMEDIA:report.csv',
  '- MEDIA:report.csv', '- Example:\nMEDIA:report.csv',
  '`MEDIA:report.csv`', '`example\nMEDIA:report.csv\nend`',
  '```text\nMEDIA:report.csv\n```', '~~~\nMEDIA:report.csv\n~~~',
  '[MEDIA:report.csv](https://example.com/reference)',
  '<div>\nMEDIA:report.csv\n</div>', '<span>\nMEDIA:report.csv\n</span>',
  'MEDIA\\:report.csv', 'MEDIA&#58;report.csv',
  'MEDIA:report.csv extra', 'MEDIA:report.csv ', 'MEDIA:report.csv  \nNext line',
])('preserves examples and non-directive Markdown: %s',source=>{
  const {container}=render(<PortalStreamingText artifacts={[mediaReceipt()]}>{source}</PortalStreamingText>)
  expect(container.textContent).toContain('MEDIA:report.csv')
})

it.each(['MEDIA:unknown.csv','MEDIA:../report.csv','MEDIA:https://example.com/report.csv','MEDIA: report.csv','media:report.csv'])('preserves an unmatched or malformed marker: %s',source=>{
  const {container}=render(<PortalStreamingText artifacts={[mediaReceipt()]}>{source}</PortalStreamingText>)
  expect(container).toHaveTextContent(source)
})

it.each([null,[],[{file:{path:'report.csv'}}],[{...mediaReceipt(),sha256:'invalid'}],[mediaReceipt(),mediaReceipt('b')]])('preserves a marker without a unique validated receipt: %j',artifacts=>{
  const {container}=render(<PortalStreamingText artifacts={artifacts}>MEDIA:report.csv</PortalStreamingText>)
  expect(container).toHaveTextContent('MEDIA:report.csv')
})

it('changes presentation only for a completed response with a valid receipt',()=>{
  const source='Created.\n\nMEDIA:report.csv'
  const artifacts=[mediaReceipt()]
  const {container,rerender}=render(<PortalStreamingText active instant artifacts={artifacts}>{source}</PortalStreamingText>)
  expect(container).toHaveTextContent('MEDIA:report.csv')
  rerender(<PortalStreamingText artifacts={artifacts}>{source}</PortalStreamingText>)
  expect(container.textContent).not.toContain('MEDIA:report.csv')
  rerender(<PortalStreamingText active instant artifacts={artifacts}>{source}</PortalStreamingText>)
  expect(container).toHaveTextContent('MEDIA:report.csv')
  rerender(<PortalStreamingText artifacts={artifacts}>{source}</PortalStreamingText>)
  expect(container.textContent).not.toContain('MEDIA:report.csv')
  rerender(<PortalStreamingText artifacts={[{...mediaReceipt(),schemaVersion:2}]}>{source}</PortalStreamingText>)
  expect(container).toHaveTextContent('MEDIA:report.csv')
})

beforeEach(()=>{
  vi.useFakeTimers()
  vi.stubGlobal('matchMedia',()=>({matches:false,addEventListener:vi.fn(),removeEventListener:vi.fn()}))
})
afterEach(()=>{vi.useRealTimers();vi.unstubAllGlobals()})
const advance=ms=>act(()=>vi.advanceTimersByTime(ms))

it('keeps a verified download free of authored navigation throughout reveal',()=>{
  const href='https://untrusted.example/api/download?siteId=invented'
  const source=`[![Download report.md](${href})](${href})`
  const artifact={schemaVersion:1,kind:'ods-pixel-workspace-artifact',relativePath:'report.md',
    siteId:`site-${'a'.repeat(24)}`,sha256:'a'.repeat(64),file:{path:'report.md',bytes:20,sha256:'b'.repeat(64)}}
  const {container}=render(<PortalStreamingText animate artifacts={[artifact]}>{source}</PortalStreamingText>)
  for(let elapsed=0;elapsed<2000;elapsed+=16){
    advance(16)
    expect([...container.querySelectorAll('a')].filter(a=>new URL(a.href).hostname==='untrusted.example'),
      `${elapsed}: ${container.innerHTML}`).toHaveLength(0)
  }
  expect(screen.getByRole('button',{name:'Download verified report.md'})).toBeInTheDocument()
})

it('finishes partially revealed Markdown atomically when its completed receipt arrives',()=>{
  const href='https://untrusted.example/download'
  const source=`[![Download report.md](${href})](${href})\n\n[Python docs](https://docs.python.org/3/)`
  const artifact={schemaVersion:1,kind:'ods-pixel-workspace-artifact',relativePath:'report.md',
    siteId:`site-${'a'.repeat(24)}`,sha256:'a'.repeat(64),file:{path:'report.md',bytes:20,sha256:'b'.repeat(64)}}
  const {container,rerender}=render(<PortalStreamingText active>{source}</PortalStreamingText>)
  advance(400)
  expect(container.firstChild).toHaveAttribute('aria-busy','true')
  expect(screen.queryByRole('button',{name:'Download verified report.md'})).toBeNull()
  rerender(<PortalStreamingText animate artifacts={[artifact]}>{source}</PortalStreamingText>)
  expect(container.firstChild).toHaveAttribute('aria-busy','false')
  expect(screen.getByRole('button',{name:'Download verified report.md'})).toBeInTheDocument()
  expect(screen.getAllByRole('link')).toHaveLength(1)
  expect(screen.getByRole('link',{name:/Python docs/})).toHaveAttribute('href','https://docs.python.org/3/')
  advance(2000)
  expect(screen.getAllByRole('link')).toHaveLength(1)
})

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

const documentName='fleet-pr7893-mac-squares.csv'
const deliveredDocument=(digit='a')=>({schemaVersion:1,kind:'ods-pixel-workspace-artifact',
  relativePath:documentName,siteId:`site-${digit.repeat(24)}`,sha256:digit.repeat(64),
  file:{path:documentName,bytes:149,sha256:digit.repeat(64)}})
const standaloneDocument=`**Download:**\n\n![${documentName}](https://untrusted.example/invented.csv?sha256=wrong)`

it('binds the actual standalone CSV image after its separate Download paragraph to a verified receipt',()=>{
  const {container}=render(<PortalStreamingText artifacts={[deliveredDocument()]}>{standaloneDocument}</PortalStreamingText>)
  expect(screen.getByRole('button',{name:`Download verified ${documentName}`})).toBeInTheDocument()
  expect(container.querySelector('img')).toBeNull()
  expect(container.querySelector('a[href]')).toBeNull()
})
it('keeps a standalone document label inert until its receipt arrives',()=>{
  const {container,rerender}=render(<PortalStreamingText active instant>{standaloneDocument}</PortalStreamingText>)
  expect(container.querySelector('img')).toBeNull()
  expect(container.querySelector('a[href]')).toBeNull()
  expect(screen.queryByRole('button')).toBeNull()
  expect(container).toHaveTextContent(documentName)
  rerender(<PortalStreamingText instant artifacts={[deliveredDocument()]}>{standaloneDocument}</PortalStreamingText>)
  expect(screen.getByRole('button',{name:`Download verified ${documentName}`})).toBeInTheDocument()
})
it('does not guess between duplicate standalone document names',()=>{
  const {container}=render(<PortalStreamingText artifacts={[deliveredDocument(),deliveredDocument('b')]}>{standaloneDocument}</PortalStreamingText>)
  expect(screen.queryByRole('button')).toBeNull()
  expect(container.querySelector('img,a[href]')).toBeNull()
})
it('uses only the receipt for an explicit standalone download image with a hostile source URL',()=>{
  const {container}=render(<PortalStreamingText artifacts={[deliveredDocument()]}>{`![Download ${documentName}](javascript:alert%281%29)`}</PortalStreamingText>)
  expect(screen.getByRole('button',{name:`Download verified ${documentName}`})).toBeInTheDocument()
  expect(container.querySelector('img,a[href]')).toBeNull()
})
it('preserves ordinary images and does not carry a Download prefix across other prose',()=>{
  const {container}=render(<PortalStreamingText artifacts={[deliveredDocument()]}>{`Download:\n\n![chart.png](https://example.com/chart.png)\n\nDownload:\n\nAn example follows.\n\n![${documentName}](https://example.com/example.csv)`}</PortalStreamingText>)
  expect(container.querySelectorAll('img')).toHaveLength(2)
  expect(screen.queryByRole('button')).toBeNull()
})
it('preserves quoted and code examples of standalone document downloads',()=>{
  const quoted=`> Download:\n>\n> ![${documentName}](https://example.com/example.csv)`
  const code='```md\n'+standaloneDocument+'\n```'
  const {container}=render(<PortalStreamingText artifacts={[deliveredDocument()]}>{quoted+'\n\n'+code}</PortalStreamingText>)
  expect(container.querySelector('blockquote img')).not.toBeNull()
  expect(container.querySelector('pre code')).toHaveTextContent(documentName)
  expect(screen.queryByRole('button')).toBeNull()
})
