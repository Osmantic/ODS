import { useEffect, useMemo, useState } from 'react'
import { ChevronRight, File, Folder, Search, SquareMinus, SquarePlus } from 'lucide-react'
import { PixelLanguageBadge } from './PixelCodeBlock'
import './portal-file-tree.css'

const STATUS = {created:'Added',modified:'Modified',deleted:'Deleted',published:'Published'}

function fileTree(files,rootPath) {
  const root = {folders:new Map(), files:[]}
  let directory=root, directoryPath=''
  for(const part of rootPath.split('/').filter(Boolean)) {
    directoryPath=directoryPath?`${directoryPath}/${part}`:part
    const folder={name:part,path:directoryPath,folders:new Map(),files:[],rootDirectory:true}
    directory.folders.set(part,folder)
    directory=folder
  }
  for (const file of files) {
    const parts = String(file.path).replaceAll('\\','/').split('/').filter(Boolean)
    let parent = directory, path = directoryPath
    for (const part of parts.slice(0,-1)) {
      path = path ? `${path}/${part}` : part
      if (!parent.folders.has(part)) parent.folders.set(part,{name:part,path,folders:new Map(),files:[]})
      parent = parent.folders.get(part)
    }
    parent.files.push({...file,name:parts.at(-1) || file.path})
  }
  return root
}

function ChangeStatus({change}) {
  if (!STATUS[change]) return null
  const Icon = change === 'created' ? SquarePlus : change === 'deleted' ? SquareMinus : null
  return <span className={`portal-file-tree-status is-${change}`} title={STATUS[change]} aria-label={STATUS[change]}>{Icon ? <Icon size={13} aria-hidden="true"/> : <span aria-hidden="true">●</span>}</span>
}

/** An ordinary disclosure navigation: keyboard buttons retain native focus and
 * activation on every platform, without pretending to be an editor widget. */
export default function PortalFileTree({files = [], rootPath='', selectedPath, onSelectFile, label = 'Files', filterLabel = 'Filter files'}) {
  const [query, setQuery] = useState('')
  const [closed, setClosed] = useState(() => new Set())
  // This is the receipt's actual directory, not a synthetic workspace prefix.
  // Files and callbacks remain relative to the immutable publication root.
  const directory=typeof rootPath==='string'?rootPath.replaceAll('\\','/').split('/').filter(Boolean).join('/'):''
  const shown = useMemo(() => files.filter(file => `${directory}/${file.path}`.toLowerCase().includes(query.trim().toLowerCase())),[files,query,directory])
  const tree = useMemo(() => fileTree(shown,directory),[shown,directory])
  useEffect(() => {
    if(!selectedPath)return
    const selected = [directory,String(selectedPath).replaceAll('\\','/')].filter(Boolean).join('/')
    setClosed(current => new Set([...current].filter(path => !selected.startsWith(`${path}/`))))
  },[selectedPath,directory])
  const toggle = path => setClosed(current => {const next = new Set(current); if (next.has(path)) next.delete(path); else next.add(path); return next})
  function children(node) {
    return <ul>{[...node.folders.values()].sort((a,b) => a.name.localeCompare(b.name)).map(folder => {
      let branch = folder, name = folder.name
      while (!branch.rootDirectory && !branch.files.length && branch.folders.size === 1) {branch = [...branch.folders.values()][0]; name += `/${branch.name}`}
      const open = Boolean(query.trim()) || !closed.has(branch.path)
      return <li className="portal-file-tree-folder" key={folder.path}>
        <button type="button" className="portal-file-tree-folder-button" aria-label={`Folder ${branch.path}`} aria-expanded={open} title={branch.path} onClick={() => toggle(branch.path)} disabled={Boolean(query.trim())}>
          <ChevronRight size={14} className="portal-file-tree-chevron" aria-hidden="true"/><Folder size={14} aria-hidden="true"/><span>{name}</span>
        </button>
        {open && children(branch)}
      </li>
    })}{[...node.files].sort((a,b) => a.name.localeCompare(b.name)).map(file => <li key={file.path}>
      <button type="button" className="portal-file-tree-file" aria-label={`Open ${file.path}`} aria-current={selectedPath === file.path ? 'true' : undefined} title={file.path} onClick={() => onSelectFile?.(file.path,files.find(item => item.path === file.path))}>
        {file.name.includes('.') ? <PixelLanguageBadge path={file.path}/> : <File size={14} aria-hidden="true"/>}<span>{file.name}</span><ChangeStatus change={file.change}/>
      </button>
    </li>)}</ul>
  }
  return <nav className="portal-file-tree" aria-label={label}>
    <label className="portal-file-tree-search"><Search size={13} aria-hidden="true"/><input type="search" aria-label={filterLabel} placeholder="Filter files…" value={query} onChange={event => setQuery(event.target.value)}/></label>
    <div className="portal-file-tree-scroll">{children(tree)}{!shown.length && <p role="status">{query ? 'No files match this filter.' : 'No files.'}</p>}</div>
  </nav>
}
