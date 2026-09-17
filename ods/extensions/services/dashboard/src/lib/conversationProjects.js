import {parseProjectTasks,parseTaskActivity} from './pixelTaskActivity'

// Project membership comes from verified workspace metadata or publication
// receipts, never model prose, tool display labels, or command strings.
export function publicationDirectory(value) {
  if (!value || value.schemaVersion !== 1 || value.kind !== 'ods-pixel-workspace-preview'
    || typeof value.relativeDirectory !== 'string'
    || !/^(?!\/)(?!.*(?:^|\/)\.\.?(?:\/|$))[A-Za-z0-9][A-Za-z0-9._/-]{0,511}$/.test(value.relativeDirectory)
    || value.relativeDirectory.split('/').some(part => !part)
    || !/^[a-f0-9]{64}$/.test(value.sha256 || '') || !/^[a-f0-9]{64}$/.test(value.entrySha256 || '')
    || value.siteId !== `site-${value.sha256.slice(0,24)}`
    || !Number.isInteger(value.port) || value.port < 1 || value.port > 65535
    || value.url !== `http://${value.siteId}.localhost:${value.port}/${value.siteId}/`
    || !Number.isInteger(value.files) || value.files < 1 || value.files > 128
    || !Number.isInteger(value.bytes) || value.bytes < 1 || value.bytes > 16*1024*1024) return null
  return value.relativeDirectory
}

export function conversationProject(chat) {
  const messages=Array.isArray(chat?.messages)?chat.messages:[]
  let directory=null
  for(let index=messages.length-1;index>=0;index--) {
    const message=messages[index]
    if(message?.role!=='assistant')continue
    const task=parseTaskActivity(message.task,message.task?.runId)
    const observed=[task,...(parseProjectTasks(message.projectTasks) || [])]
      .filter(value=>value?.projects?.length)
      .sort((a,b)=>b.projects[0].observedAt.localeCompare(a.projects[0].observedAt))
    directory=observed[0]?.projects[0].relativeDirectory || publicationDirectory(message.publication)
    if(directory)break
  }
  directory ||= publicationDirectory(chat?.preview)
  if (!directory) return null
  const parts=directory.split('/')
  return parts[0]==='Playground' && parts.length>1
    ? {root:'Playground',name:parts[1],path:parts.slice(0,2).join('/')}
    : {root:null,name:directory,path:directory}
}

export function groupProjectConversations(chats) {
  const projects=new Map(),recent=[]
  for (const chat of chats) {
    const project=conversationProject(chat)
    if (!project) {recent.push(chat);continue}
    if (!projects.has(project.path)) projects.set(project.path,{...project,chats:[]})
    projects.get(project.path).chats.push(chat)
  }
  return {projects:[...projects.values()],recent}
}
