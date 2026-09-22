import {useEffect,useId,useRef,useState} from 'react'
import {Globe2,FileCode2,FileText,Files,Users,PanelRightClose,PanelRightOpen,RefreshCw,ExternalLink,X,MoreHorizontal,Maximize2,Minimize2} from 'lucide-react'
import {loadSnapshotFiles} from '../lib/pixelArtifacts'
import PixelPreviewSource from './PixelPreviewSource'
import PixelPreviewViewport from './PixelPreviewViewport'
import PixelSnapshotChanges from './PixelSnapshotChanges'
import PortalFileTree from './PortalFileTree'
import PortalFileTreeResize,{useFileTreeResize} from './PortalFileTreeResize'
import PortalSubagents from './PortalSubagents'
import PortalLiveReview from './PortalLiveReview'
import './portal-workspace.css'

export default function PortalWorkspace({preview,before,access,title,request,onRequestHandled,refresh=0,onRefresh,onClose,collapsed,onCollapse,onPublish,expanded,onExpand,agents,renderApproval,task,working=false}) {
  const [active,setActive]=useState(request?.kind==='agents'?'agents':!preview?'review':'preview'),[tabs,setTabs]=useState([]),[manifest,setManifest]=useState(null)
  const [agentsTabOpen,setAgentsTabOpen]=useState(request?.kind==='agents')
  const [pendingPath,setPendingPath]=useState(null),[missingPath,setMissingPath]=useState(null)
  const manifestKey=`${preview?.siteId}/${preview?.sha256}`
  const files=manifest?.key===manifestKey?manifest.files:null,error=manifest?.key===manifestKey && manifest.error
  const workspaceId=useId()
  const tabDomId=key=>`${workspaceId}-tab-${encodeURIComponent(key)}`
  const panelDomId=key=>`${workspaceId}-panel-${key.startsWith('file:')?'file':key}`
  const [treeOpen,setTreeOpen]=useState(false),[reviewPath,setReviewPath]=useState(null),[options,setOptions]=useState(false),[retry,setRetry]=useState(0)
  const consumed=useRef(null),root=useRef(null),focusTimer=useRef(null)
  const treeLayout=useFileTreeResize(root,{narrowBelow:561})
  useEffect(()=>()=>{if(focusTimer.current!==null)clearTimeout(focusTimer.current)},[])
  useEffect(()=>{
    setTabs([]);setActive(current=>current==='agents'?'agents':request?.siteId===preview?.siteId && request?.kind==='review'?'review':!preview?'review':'preview');setReviewPath(null);setOptions(false);setPendingPath(null);setMissingPath(null)
  },[preview?.siteId])
  useEffect(()=>{
    setManifest(null)
    if(!preview)return
    let current=true;const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),12000)
    loadSnapshotFiles(preview,controller.signal).then(value=>{if(current)setManifest({key:manifestKey,files:value,error:false})}).catch(()=>{if(current)setManifest({key:manifestKey,files:null,error:true})}).finally(()=>clearTimeout(timer))
    return ()=>{current=false;controller.abort();clearTimeout(timer)}
  },[preview?.siteId,preview?.sha256,preview?.entrySha256,preview?.files,preview?.bytes,refresh,retry])
  function openFile(path) {
    if(!files){setPendingPath(path);return}
    if(!files.some(file=>file.path===path)){setMissingPath(path);return}
    setMissingPath(null)
    setTabs(value=>value.includes(path)?value:[...value,path]);setActive(`file:${path}`);setTreeOpen((root.current?.clientWidth || 0)>560)
  }
  useEffect(()=>{if(files && pendingPath){openFile(pendingPath);setPendingPath(null)}},[files,pendingPath])
  useEffect(()=>{
    if(!request || request===consumed.current)return
    if(request.kind==='agents'){
      consumed.current=request;setAgentsTabOpen(true);setActive('agents');setOptions(false);setPendingPath(null);setMissingPath(null);onRequestHandled?.(request);return
    }
    if(request.siteId!==preview?.siteId)return
    consumed.current=request
    if(request.kind==='file'){setActive('review');openFile(request.path)}
    else {setPendingPath(null);setMissingPath(null);setActive(request.kind==='review'?'review':!preview?'review':'preview');setReviewPath(request.path || null)}
    onRequestHandled?.(request)
  },[request,preview?.siteId,files,onRequestHandled])
  const selected=files?.find(file=>`file:${file.path}`===active)
  const fileView=active.startsWith('file:')
  const tabList=[{id:'preview',label:'Preview',Icon:Globe2},{id:'review',label:'Review',Icon:FileCode2},...tabs.map(path=>({id:`file:${path}`,label:path.split('/').at(-1),Icon:FileText})),...(agentsTabOpen && agents?[{id:'agents',label:'Subagents',Icon:Users}]:[])]
  function switchTab(id) {consumed.current=request;setPendingPath(null);setMissingPath(null);setActive(id);setOptions(false)}
  function closeTab(id) {
    if(id==='agents'){setAgentsTabOpen(false);agents?.select(null);if(active===id)setActive('preview')}
    else {const path=id.slice(5);setTabs(value=>value.filter(item=>item!==path));if(active===id)setActive('review')}
    if(focusTimer.current!==null)clearTimeout(focusTimer.current)
    focusTimer.current=setTimeout(()=>{focusTimer.current=null;root.current?.querySelector('[role=tab][aria-selected=true]')?.focus()},0)
  }
  return <div ref={root} className="portal-workbench" data-collapsed={collapsed}>
    <header className="portal-workbench-tabs">
      {!collapsed && <div role="tablist" aria-label="Workspace tabs" onKeyDown={event=>{
        if(!['ArrowLeft','ArrowRight','Home','End'].includes(event.key))return
        const index=tabList.findIndex(tab=>tab.id===active)
        const next=event.key==='Home'?0:event.key==='End'?tabList.length-1:(index+(event.key==='ArrowRight'?1:-1)+tabList.length)%tabList.length
        event.preventDefault();switchTab(tabList[next].id)
        event.currentTarget.querySelectorAll('[role=tab]')[next]?.focus()
      }}>{tabList.map(({id,label,Icon})=><div key={id} className="portal-workbench-tab" data-selected={active===id}>
        <button type="button" role="tab" id={tabDomId(id)} aria-controls={panelDomId(id)} aria-selected={active===id} tabIndex={active===id?0:-1} title={id.startsWith('file:')?id.slice(5):undefined} onClick={()=>switchTab(id)}><Icon size={13}/><span>{label}</span></button>
        {!['preview','review'].includes(id) && <button type="button" aria-label={id==='agents'?'Close Subagents':`Close ${id.slice(5)}`} onClick={()=>closeTab(id)}><X size={12}/></button>}
      </div>)}</div>}
      <div className="portal-workbench-window-actions">
        {!collapsed && preview && active!=='agents' && <>
          <button type="button" title="Reload preview" aria-label="Reload preview" onClick={onRefresh}><RefreshCw size={14}/></button>
          {active!=='review' && <button type="button" title="Browse files" aria-label="Browse files" aria-pressed={treeOpen} onClick={()=>setTreeOpen(value=>!value)}><Files size={14}/></button>}
          <button type="button" title="Workspace options" aria-label="Workspace options" aria-expanded={options} onClick={()=>setOptions(value=>!value)}><MoreHorizontal size={16}/></button>
        </>}
        {!collapsed && <button type="button" title={expanded?'Restore workspace size':'Expand workspace'} aria-label={expanded?'Restore workspace size':'Expand workspace'} onClick={onExpand}>{expanded?<Minimize2 size={14}/>:<Maximize2 size={14}/>}</button>}
        <button type="button" title={collapsed?'Expand preview':'Collapse preview'} aria-label={collapsed?'Expand preview':'Collapse preview'} onClick={onCollapse}>{collapsed?<PanelRightOpen size={14}/>:<PanelRightClose size={14}/>}</button>
        <button type="button" title="Close preview" aria-label="Close preview" onClick={onClose}><X size={14}/></button>
      </div>
    </header>
    {!collapsed && !preview && active==='preview' && <section className="portal-workbench-empty"><Files size={24}/><h2>No files to show yet</h2><p>Published files and web previews will appear here.</p>{onPublish && <button type="button" onClick={onPublish}>Ask Portal to publish</button>}</section>}
    <div className="portal-workbench-live" hidden={!!preview || collapsed || active!=='review'}><PortalLiveReview task={task} active={working}/></div>
    {agentsTabOpen && agents && <div className="portal-workbench-body" hidden={collapsed || active!=='agents'} id={panelDomId('agents')} role="tabpanel" aria-labelledby={tabDomId('agents')}><PortalSubagents controller={agents} renderApproval={renderApproval}/></div>}
    {preview && <div className="portal-workbench-body" hidden={collapsed || active==='agents'}>
      {missingPath && <p role="status" className="portal-source-notice">This file is not available in this publication. <button type="button" onClick={()=>setMissingPath(null)}>Dismiss</button></p>}
      {pendingPath && <p role="status" className="portal-source-notice">{error?'Files unavailable.':'Opening file…'}{error && <button type="button" onClick={()=>setRetry(value=>value+1)}>Retry</button>}</p>}
      {options && <div className="portal-workbench-options" role="group" aria-label="Workspace actions">
        <a href={access.url} target="_blank" rel="noopener noreferrer"><ExternalLink size={14}/>Open preview in a new tab</a>
        {active!=='preview' && <button type="button" onClick={()=>switchTab('preview')}><Globe2 size={14}/>Show preview</button>}
      </div>}
      {/* Retain the frame while reading/reviewing files so its local state survives tab switches. */}
      <div className={`portal-workbench-content${treeOpen && !treeLayout.narrow?' portal-tree-split':''}`} style={treeLayout.style} id={panelDomId('preview')} role="tabpanel" aria-labelledby={tabDomId('preview')} hidden={active!=='preview'}>
        <PixelPreviewViewport key={`${preview.siteId}/${refresh}`} access={access} title={title} hidden={collapsed || active!=='preview'} compact/>
        {treeOpen && <PortalFileTreeResize layout={treeLayout}/>}
        {treeOpen && <aside className="portal-workbench-file-tree">{files?<PortalFileTree files={files} rootPath={preview.relativeDirectory} selectedPath={null} onSelectFile={path=>openFile(path)} label="Published files" filterLabel="Filter task files"/>:error?<p role="alert">Files unavailable. <button onClick={()=>setRetry(value=>value+1)}>Retry</button></p>:<p role="status">Loading files…</p>}</aside>}
      </div>
      {active==='review' && <div className="portal-workbench-review" id={panelDomId('review')} role="tabpanel" aria-labelledby={tabDomId('review')}>
        {error && <p className="portal-source-notice" role="status">Project files unavailable. <button type="button" onClick={()=>setRetry(value=>value+1)}>Retry files</button></p>}
        <PixelSnapshotChanges key={`${preview.siteId}/${refresh}`} preview={preview} rootPath={preview.relativeDirectory} before={before} projectFiles={files} selectedPath={reviewPath} onSelectFile={path=>setReviewPath(path)} onOpenFile={file=>openFile(file.path)}/>
      </div>}
      {fileView && <div className={`portal-workbench-content${treeOpen && !treeLayout.narrow?' portal-tree-split':''}`} style={treeLayout.style} id={panelDomId(active)} role="tabpanel" aria-labelledby={tabDomId(active)}>
        <div className="portal-workbench-document">{selected?<PixelPreviewSource key={`${preview.siteId}/${active}/${refresh}`} preview={preview} file={selected} workbench onOpenFile={openFile}/>:<p role="status">{error?'File unavailable.':'Loading file…'}{error && <button type="button" onClick={()=>setRetry(value=>value+1)}>Retry</button>}</p>}</div>
        {treeOpen && <PortalFileTreeResize layout={treeLayout}/>}
        <aside className="portal-workbench-file-tree" hidden={!treeOpen}>{files && <PortalFileTree files={files} rootPath={preview.relativeDirectory} selectedPath={selected?.path} onSelectFile={path=>openFile(path)} label="Published files" filterLabel="Filter task files"/>}</aside>
      </div>}
    </div>}
  </div>
}
