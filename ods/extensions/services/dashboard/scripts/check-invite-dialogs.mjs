// Optional browser regression: npm install --no-save --package-lock=false playwright@1.63.0
// npx playwright install chromium && node scripts/check-invite-dialogs.mjs
/* global document */
import assert from 'node:assert/strict'
import console from 'node:console'
import {createRequire} from 'node:module'
import {fileURLToPath, URL} from 'node:url'
import {createServer} from 'vite'

const {chromium} = createRequire(import.meta.url)('playwright')
const root = fileURLToPath(new URL('../', import.meta.url))
const entry = `import React from 'react'; import {createRoot} from 'react-dom/client';
import Invites from '/src/pages/Invites.jsx'; import '/src/index.css';
createRoot(document.getElementById('root')).render(React.createElement(Invites));`
const server = await createServer({root, appType:'custom', server:{host:'127.0.0.1',port:0}, plugins:[{
  name:'invite-dialog-regression', resolveId:id => id === 'invite-dialog-regression' ? id : null,
  load:id => id === 'invite-dialog-regression' ? entry : null,
}]})
server.middlewares.use(async (request,response,next) => {
  if (request.url !== '/__invite_dialogs') return next()
  response.setHeader('Content-Type','text/html')
  response.end(await server.transformIndexHtml('/__invite_dialogs', '<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"></head><body><div id="root"></div><script type="module" src="/@id/invite-dialog-regression"></script></body></html>'))
})
let browser
const errors = [], receipts = []
try {
  await server.listen()
  browser = await chromium.launch({headless:true})
  for (const viewport of [{width:320,height:480},{width:390,height:640},{width:1280,height:900}]) {
    const page = await browser.newPage({viewport})
    page.on('pageerror',error => errors.push(error.message))
    await page.route('**/api/auth/magic-link/**', async route => {
      const path = new URL(route.request().url()).pathname
      if (path.endsWith('/list')) return route.fulfill({json:{tokens:[]}})
      if (path.endsWith('/status')) return route.fulfill({json:{ready:true,url_mode:'lan'}})
      if (path.endsWith('/qr')) return route.fulfill({status:503,json:{detail:'Fixture QR unavailable'}})
      if (path.endsWith('/generate')) {
        const request = route.request().postDataJSON()
        return route.fulfill({json:{...request,url:'http://ods.example.test/magic-link/fixture-only',expires_at:'2099-01-01T00:00:00Z'}})
      }
      throw new Error(`Unexpected fixture request: ${path}`)
    })
    await page.goto(`http://127.0.0.1:${server.httpServer.address().port}/__invite_dialogs`)
    for (const owner of [true,false]) {
      const openerName = owner ? 'Print owner card' : 'New guest invite'
      await page.getByRole('button',{name:openerName,exact:true}).click()
      for (const phase of ['create','generated']) {
        const dialog = page.getByRole('dialog')
        await dialog.waitFor()
        const layout = await dialog.evaluate(element => {
          const panel = element.querySelector('.access-dialog-panel') || element
          const box = panel.getBoundingClientRect()
          return {modal:element.matches(':modal'),top:box.top,bottom:box.bottom,
            left:box.left,right:box.right,clientWidth:panel.clientWidth,scrollWidth:panel.scrollWidth}
        })
        await page.locator('.owner-access-heading button').evaluate(element => element.focus())
        const backgroundBlocked = await dialog.evaluate(element => element.contains(document.activeElement))
        // Verify keyboard traversal never lands on an action behind the dialog.
        await dialog.getByRole('button',{name:'Close',exact:true}).focus()
        let backgroundTabs = 0
        for (let index = 0; index < 14; index++) {
          await page.keyboard.press('Tab')
          if (await page.evaluate(() => Boolean(document.activeElement?.closest('.owner-access-heading, .owner-access-section-heading')))) backgroundTabs++
        }
        receipts.push({viewport,owner,phase,...layout,backgroundBlocked,backgroundTabs})
        if (phase === 'create') {
          await dialog.getByPlaceholder('alice').fill('fixture-user')
          await dialog.getByRole('button',{name:owner ? 'Generate owner QR' : 'Generate',exact:true}).click()
          await page.getByRole('dialog',{name:owner ? 'Owner card created' : 'Invite created'}).waitFor()
        } else {
          await page.keyboard.press('Escape')
          await page.waitForTimeout(50)
          const closed = await page.getByRole('dialog').count() === 0
          const returnedFocus = await page.getByRole('button',{name:openerName,exact:true}).evaluate(element => element === document.activeElement)
          receipts.push({viewport,owner,closed,returnedFocus})
          if (!closed) await dialog.getByRole('button',{name:'Done',exact:true}).click()
        }
      }
    }
    await page.close()
  }
  console.log(JSON.stringify({browser:browser.version(),receipts,errors},null,2))
  assert.deepEqual(errors,[])
  for (const receipt of receipts) {
    if ('modal' in receipt) {
      assert.ok(receipt.modal && receipt.backgroundBlocked && receipt.backgroundTabs === 0, JSON.stringify(receipt))
      assert.ok(receipt.top >= 0 && receipt.bottom <= receipt.viewport.height + 1 && receipt.left >= 0 && receipt.right <= receipt.viewport.width + 1, JSON.stringify(receipt))
      assert.ok(receipt.scrollWidth <= receipt.clientWidth + 1, JSON.stringify(receipt))
    } else assert.ok(receipt.closed && receipt.returnedFocus,JSON.stringify(receipt))
  }
} finally {
  await browser?.close()
  await server.close()
}
