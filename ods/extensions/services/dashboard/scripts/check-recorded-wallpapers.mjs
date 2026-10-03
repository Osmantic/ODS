// npm install --no-save --package-lock=false playwright@1.63.0
// npx playwright install chromium && node scripts/check-recorded-wallpapers.mjs
/* global document, MediaRecorder, Blob, setInterval, clearInterval, setTimeout */
import assert from 'node:assert/strict'
import {Buffer} from 'node:buffer'
import console from 'node:console'
import {createRequire} from 'node:module'
import {fileURLToPath, URL} from 'node:url'
import {createServer} from 'vite'

const {chromium} = createRequire(import.meta.url)('playwright')
const root = fileURLToPath(new URL('../', import.meta.url))
const entry = `import React from 'react'; import {createRoot} from 'react-dom/client';
import {ThemeProvider} from '/src/contexts/ThemeContext.jsx';
import CustomWallpaperPicker from '/src/components/CustomWallpaperPicker.jsx';
import WallpaperVideo from '/src/components/WallpaperVideo.jsx'; import '/src/index.css';
createRoot(document.getElementById('root')).render(React.createElement(ThemeProvider,null,
React.createElement(CustomWallpaperPicker),React.createElement(WallpaperVideo)));`
const server = await createServer({root,appType:'custom',server:{host:'127.0.0.1',port:0},plugins:[{
  name:'recorded-wallpaper-regression',resolveId:id=>id==='recorded-wallpaper-regression'?id:null,
  load:id=>id==='recorded-wallpaper-regression'?entry:null,
}]})
server.middlewares.use(async (request,response,next) => {
  if (request.url !== '/__recorded_wallpapers') return next()
  response.setHeader('Content-Type','text/html')
  response.end(await server.transformIndexHtml('/__recorded_wallpapers','<!doctype html><html><body><div id="root"></div><script type="module" src="/@id/recorded-wallpaper-regression"></script></body></html>'))
})
let browser
const errors = [], receipts = []
try {
  await server.listen()
  browser = await chromium.launch({headless:true})
  const page = await browser.newPage()
  page.setDefaultTimeout(10000)
  page.on('pageerror',error=>errors.push(error.message))
  await page.goto(`http://127.0.0.1:${server.httpServer.address().port}/__recorded_wallpapers`)
  await page.getByRole('button',{name:'Add wallpaper',exact:true}).waitFor()
  for (const timeslice of [undefined,50]) {
    // Produce actual browser recordings; sliced WebM output cannot backfill
    // duration into the header of a chunk already delivered to the caller.
    const recording = await page.evaluate(async timeslice => {
      const canvas = document.createElement('canvas')
      canvas.width=160;canvas.height=90
      const stream=canvas.captureStream(20)
      const recorder=new MediaRecorder(stream,{mimeType:'video/webm;codecs=vp8'})
      const chunks=[]
      recorder.ondataavailable=event=>chunks.push(event.data)
      const stopped=new Promise(resolve=>{recorder.onstop=resolve})
      const timer=setInterval(()=>{const ctx=canvas.getContext('2d');ctx.fillStyle='#447799';ctx.fillRect(0,0,160,90)},25)
      let url, video
      try {
        recorder.start(timeslice)
        await new Promise(resolve=>setTimeout(resolve,500))
        recorder.stop();await stopped
        const blob=new Blob(chunks,{type:'video/webm'})
        url=URL.createObjectURL(blob);video=document.createElement('video')
        await new Promise((resolve,reject)=>{video.onloadeddata=resolve;video.onerror=reject;video.src=url})
        return {bytes:[...new Uint8Array(await blob.arrayBuffer())],duration:String(video.duration)}
      } finally {
        clearInterval(timer);stream.getTracks().forEach(track=>track.stop())
        video?.removeAttribute('src');video?.load();if(url)URL.revokeObjectURL(url)
      }
    }, timeslice)
    if (timeslice) assert.equal(recording.duration,'Infinity','Fixture must reproduce missing duration metadata')
    const previous = await page.evaluate(()=>document.documentElement.getAttribute('data-wallpaper'))
    await page.getByLabel('Choose local wallpaper').setInputFiles({name:`recorded-${timeslice || 'whole'}.webm`,mimeType:'video/webm',buffer:Buffer.from(recording.bytes)})
    await page.getByRole('button',{name:'Add wallpaper',exact:true}).waitFor()
    const error = await page.getByRole('alert').allTextContents()
    receipts.push({timeslice:timeslice || null,bytes:recording.bytes.length,duration:recording.duration,error})
    assert.deepEqual(error,[],JSON.stringify(receipts))
    await page.waitForFunction(previous=>document.documentElement.getAttribute('data-wallpaper')!==previous,previous)
    await page.waitForFunction(()=>{const video=document.querySelector('.workspace-wallpaper-video');return video && !video.paused && video.currentTime>0})
    assert.equal(await page.locator('.workspace-wallpaper-video').evaluate(video=>video.loop),true)
  }
  const selected = await page.evaluate(()=>document.documentElement.getAttribute('data-wallpaper'))
  await page.reload()
  await page.waitForFunction(selected=>document.documentElement.getAttribute('data-wallpaper')===selected,selected)
  await page.waitForFunction(()=>{const video=document.querySelector('.workspace-wallpaper-video');return video && !video.paused && video.currentTime>0})
  // Corrupt input still rejects and leaves the stored selection intact.
  await page.getByLabel('Choose local wallpaper').setInputFiles({name:'broken.webm',mimeType:'video/webm',buffer:Buffer.from('not a video')})
  await page.getByRole('alert').waitFor()
  assert.match(await page.getByRole('alert').textContent(),/could not be opened/)
  assert.equal(await page.evaluate(()=>document.documentElement.getAttribute('data-wallpaper')),selected)
  assert.deepEqual(errors,[])
  console.log(JSON.stringify({browser:browser.version(),receipts,reloaded:true,corruptRejected:true,errors},null,2))
} finally {
  await browser?.close()
  await server.close()
}
