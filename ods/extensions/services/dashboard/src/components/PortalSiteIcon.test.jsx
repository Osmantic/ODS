import {fireEvent,render,screen} from '@testing-library/react'
import PortalSiteIcon,{siteIconUrl} from './PortalSiteIcon'
import PortalInlineCitation from './PortalInlineCitation'

it('requests a cached icon for the hostname without disclosing the article or its parameters',()=>{
  const source='https://portal.stf.jus.br/noticias/artigo?id=42&token=private#section'
  const {container}=render(<PortalInlineCitation href={source}>Notícia do STF</PortalInlineCitation>)
  expect(screen.getByRole('link',{name:'Notícia do STF'})).toHaveAttribute('href',source)
  const img=container.querySelector('img'),url=new URL(img.src)
  expect(url.origin).toBe('https://t3.gstatic.com')
  expect(url.searchParams.get('url')).toBe('https://portal.stf.jus.br')
  expect(img.src).not.toMatch(/noticias|private|section/)
  expect(img).toHaveAttribute('referrerpolicy','no-referrer')
  expect(img).toHaveAttribute('loading','lazy')
  expect(img).toHaveAttribute('alt','')
})

it.each(['http://localhost:3001/a','http://site-aaaaaaaaaaaaaaaaaaaaaaaa.localhost:8000/','http://127.0.0.1/private','http://2130706433/a','http://192.168.0.1/','http://[::1]/','https://router.lan/a','https://app.internal/','https://host.local/','https://user:secret@example.com/a','file:///tmp/index.html','javascript:alert(1)','/pixel-preview/site/','not a URL'])(
  'does not send local, unsafe or authenticated addresses to the icon service: %s',href=>{
    expect(siteIconUrl(href)).toBeNull()
    const {container}=render(<PortalSiteIcon href={href}/>)
    expect(container.querySelector('img')).toBeNull()
    expect(container.querySelector('svg')).toBeInTheDocument()
  }
)

it('uses a stable fallback when loading fails, preserves the link, and resets for another site',()=>{
  const {container,rerender}=render(<PortalInlineCitation href="https://www.bbc.com/news">BBC</PortalInlineCitation>)
  const image=container.querySelector('img')
  fireEvent.load(image)
  expect(image).toHaveAttribute('data-loaded','true')
  fireEvent.error(image)
  expect(container.querySelector('img')).toBeNull()
  expect(container.querySelector('svg')).toBeInTheDocument()
  expect(screen.getByRole('link',{name:'BBC'})).toHaveAttribute('href','https://www.bbc.com/news')
  rerender(<PortalInlineCitation href="https://www.bbc.com/news">BBC updated</PortalInlineCitation>)
  expect(container.querySelector('img')).toBeNull()
  rerender(<PortalInlineCitation href="https://www.stj.jus.br/news">STJ</PortalInlineCitation>)
  expect(new URL(container.querySelector('img').src).searchParams.get('url')).toBe('https://www.stj.jus.br')
  expect(container.querySelector('img')).toHaveAttribute('data-loaded','false')
})
