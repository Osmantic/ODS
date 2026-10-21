import { readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const read = (name) => readFileSync(resolve(root, name), 'utf8')

describe('index.html under the dashboard CSP', () => {
  it('has no inline scripts, which script-src would block', () => {
    const tags = read('index.html').match(/<script\b[^>]*>/gi) ?? []
    expect(tags.length).toBeGreaterThan(0)
    expect(tags.filter((tag) => !/\bsrc=/i.test(tag))).toEqual([])
  })

  it('loads theme init synchronously in head and registers the service worker', () => {
    const html = read('index.html')
    const head = html.slice(0, html.indexOf('</head>'))
    expect(head).toMatch(/<script src="\/theme-init\.js"><\/script>/)
    expect(html).toMatch(/<script src="\/sw-register\.js" defer><\/script>/)
    expect(read('public/theme-init.js')).toContain("setAttribute('data-theme', 'ods')")
    expect(read('public/sw-register.js')).toContain("register('/sw.js')")
  })

  it('keeps script-src strict', () => {
    const csp = read('nginx.conf').match(/script-src[^;]*;/)[0]
    expect(csp).not.toMatch(/unsafe-inline|nonce-|sha256-/)
  })
})
