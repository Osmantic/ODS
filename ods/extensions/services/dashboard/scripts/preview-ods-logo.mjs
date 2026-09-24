// Isolated appearance preview: node scripts/preview-ods-logo.mjs
// Uses the real logo and shell styles; no API calls or stored preferences.
import {createServer} from 'vite'
import {fileURLToPath, URL} from 'node:url'
import console from 'node:console'

const root = fileURLToPath(new URL('../', import.meta.url))
const entry = `import React,{useState,useEffect} from 'react';
import {createRoot} from 'react-dom/client';
import ODSLogo from '/src/components/ODSLogo.jsx';
import {WALLPAPERS} from '/src/lib/wallpapers.js';
import '/src/index.css';import '/src/pixel-workspace.css';import '/src/wallpaper-themes.css';
function Preview(){
 const [chosen,setChosen]=useState('bannalpsee'); const [collapsed,setCollapsed]=useState(false);
 useEffect(()=>{const root=document.documentElement;root.dataset.theme='ods';
  const wallpaper=WALLPAPERS.find(item=>item.id===chosen);
  if(chosen==='ods'){delete root.dataset.wallpaper;root.style.removeProperty('--workspace-wallpaper')}
  else{root.dataset.wallpaper=chosen;root.style.setProperty('--workspace-wallpaper',chosen==='bright'?'linear-gradient(120deg,#fff,#e3f5fa)':chosen==='dark'?'linear-gradient(120deg,#080814,#13152a)':chosen==='colorful'?'linear-gradient(120deg,#f16394,#9843ee,#21abc5)': 'url("'+wallpaper.image+'")')}
 },[chosen]);
 return <><div className="preview-controls" style={{position:'fixed',zIndex:20,left:260,top:18,right:20,display:'flex',flexWrap:'wrap',gap:8,alignItems:'center',padding:14,borderRadius:10,background:'#111b',backdropFilter:'blur(16px)',color:'#eee'}}>
 <label>Wallpaper <select aria-label="Wallpaper" value={chosen} onChange={event=>setChosen(event.target.value)} style={{background:'#252936',padding:8,borderRadius:6}}>
 {[...WALLPAPERS,{id:'bright',name:'Bright contrast'},{id:'dark',name:'Dark contrast'},{id:'colorful',name:'Colorful contrast'}].map(item=><option key={item.id} value={item.id}>{item.name}</option>)}
 </select></label><button onClick={()=>setCollapsed(!collapsed)} style={{padding:8,border:'1px solid #ffffff40',borderRadius:6}}>{collapsed?'Expand sidebar':'Collapse sidebar'}</button>
 <span style={{fontSize:12}}>Isolated logo preview — no live services or preferences</span></div>
 <div className="pixel-app"><aside className={'pixel-sidebar '+(collapsed?'is-collapsed':'')} aria-label="ODS navigation"><div className="pixel-brand ods-brand"><a href="#" aria-label="ODS home"><ODSLogo/></a></div><nav className="pixel-nav"><a className="pixel-nav-item is-active" href="#">Dashboard</a><a className="pixel-nav-item" href="#">Models</a><a className="pixel-nav-item" href="#">Settings</a></nav></aside><main className="pixel-workspace dashboard-market-shell" style={{padding:'130px 40px'}}><h1 style={{fontSize:28,color:'var(--pixel-text)'}}>Frosted OS mark</h1><p style={{marginTop:16,color:'var(--pixel-muted)',maxWidth:520}}>The original silhouette and responsive size remain unchanged. Wallpaper color passes through its translucent surface.</p></main></div></>
}
createRoot(document.getElementById('root')).render(<Preview/>);`
const server=await createServer({root,appType:'custom',server:{host:'127.0.0.1',port:4178,strictPort:true},plugins:[{
 name:'ods-logo-preview',resolveId:id=>id==='ods-logo-preview.jsx'?id:null,
 load:id=>id==='ods-logo-preview.jsx'?entry:null,
}]})
server.middlewares.use(async(request,response,next)=>{
 if(request.url!=='/')return next()
 response.setHeader('Content-Type','text/html')
 response.end(await server.transformIndexHtml('/','<!doctype html><html><head><title>ODS logo appearance preview</title></head><body><div id="root"></div><script type="module" src="/@id/ods-logo-preview.jsx"></script></body></html>'))
})
await server.listen()
console.log('ODS logo preview: http://127.0.0.1:4178/')
