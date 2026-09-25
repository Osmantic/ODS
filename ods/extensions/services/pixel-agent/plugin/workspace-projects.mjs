// Project identity is a host-verified conversation association, never a
// model-authored claim or an empty directory reserved before a write succeeds.
import * as fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {createHash, randomBytes} from 'node:crypto';

const MAX_BYTES = 16384;
const MAX_PROJECTS = 8;
const RESERVED = /^(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)/i;
const SESSION = /^agent:[a-z0-9_-]+:openai-user:ods-[a-f0-9]{64}$/;
const stamp = value => typeof value === 'string' && /^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z$/.test(value)
  && Number.isFinite(Date.parse(value)) && new Date(value).toISOString() === value;
const projectName = value => /^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/.test(value) && !value.endsWith('.') && !RESERVED.test(value);
const exact = (value, keys) => value && typeof value === 'object' && !Array.isArray(value)
  && Object.keys(value).sort().join() === keys.split(',').sort().join();

function checkedFile(workspaceRoot, suppliedPath) {
  if (typeof workspaceRoot !== 'string' || !path.isAbsolute(workspaceRoot)
      || typeof suppliedPath !== 'string' || suppliedPath.length > 1024 || /[\x00-\x1f\x7f]/.test(suppliedPath)) return null;
  const root = fs.realpathSync(workspaceRoot);
  if (root === path.parse(root).root || !fs.statSync(root).isDirectory()) return null;
  let relative = suppliedPath.replaceAll('\\', '/');
  if (relative.split('/').includes('..')) return null;
  if (relative.startsWith('/workspace/')) relative = relative.slice(11);
  else if (path.isAbsolute(suppliedPath)) relative = path.relative(root, suppliedPath).replaceAll('\\', '/');
  if (relative.startsWith('./')) relative = relative.slice(2);
  const parts = relative.split('/');
  if (parts.length < 3 || parts.length > 16 || parts[0] !== 'Playground' || !projectName(parts[1])
      || parts.some(part => !part || part === '.' || part === '..' || part.length > 128
        || /[:<>"|?*]/.test(part) || /[. ]$/.test(part) || RESERVED.test(part))) return null;
  let target = root;
  for (const [index, part] of parts.entries()) {
    target = path.join(target, part);
    const info = fs.lstatSync(target);
    if (info.isSymbolicLink() || (index < parts.length - 1 ? !info.isDirectory() : !info.isFile() || info.nlink !== 1)) return null;
  }
  const before = fs.lstatSync(target);
  const fd = fs.openSync(target, fs.constants.O_RDONLY | (fs.constants.O_NOFOLLOW || 0) | (fs.constants.O_NONBLOCK || 0));
  try {
    const opened = fs.fstatSync(fd);
    if (!opened.isFile() || opened.nlink !== 1 || opened.ino !== before.ino || opened.dev !== before.dev
        || fs.realpathSync(target) !== target) return null;
  } finally {fs.closeSync(fd);}
  return {workspaceRoot:root, file:relative, directory:parts.slice(0, 2).join('/')};
}

function projectFile(workspaceRoot, suppliedDirectory) {
  if(typeof workspaceRoot!=='string' || !path.isAbsolute(workspaceRoot) || typeof suppliedDirectory!=='string'
      || suppliedDirectory.length>1024 || suppliedDirectory.replaceAll('\\','/').split('/').includes('..')) return null;
  const root=fs.realpathSync(workspaceRoot);
  if(root===path.parse(root).root) return null;
  let relative=suppliedDirectory.replaceAll('\\','/');
  if(relative.startsWith('/workspace/')) relative=relative.slice(11);
  else if(path.isAbsolute(suppliedDirectory)) relative=path.relative(root,suppliedDirectory).replaceAll('\\','/');
  if(relative.startsWith('./')) relative=relative.slice(2);
  if(!relative.startsWith('Playground/') || !projectName(relative.slice(11))) return null;
  for(const directory of ['Playground',relative]) {
    const info=fs.lstatSync(path.join(root,directory));
    if(!info.isDirectory() || info.isSymbolicLink()) return null;
  }
  // Only this explicit project is inspected. Cap both entries and depth; do
  // not traverse links or infer associations by scanning the whole workspace.
  let remaining=128;
  const queue=[relative];
  while(queue.length && remaining>0) {
    const current=queue.shift(),entries=fs.opendirSync(path.join(root,current));
    try {
      let entry;
      while(remaining>0 && (entry=entries.readSync())) {
        remaining--;
        if(entry.isSymbolicLink() || !entry.name || /[:<>"|?*\x00-\x1f\x7f]/.test(entry.name)
            || /[. ]$/.test(entry.name) || RESERVED.test(entry.name)) continue;
        const file=`${current}/${entry.name}`;
        if(entry.isFile()) {try {const checked=checkedFile(root,file);if(checked)return checked;}catch{}}
        else if(entry.isDirectory() && file.split('/').length<16) queue.push(file);
      }
    } finally {entries.closeSync();}
  }
  return null;
}

// Read only the file headers of an executed patch. File contents and shell
// commands are not evidence of another file being created.
export function workspaceMutationFiles(name, params) {
  if (['write','edit'].includes(name)) return typeof params?.path==='string' ? [params.path] : [];
  const input=params?.input;
  if (name!=='apply_patch' || typeof input!=='string' || input.length>1_048_576) return [];
  const lines=input.replaceAll('\r\n','\n').trim().split('\n');
  if (lines[0]!=='*** Begin Patch' || lines.at(-1)!=='*** End Patch') return [];
  const files=[];let pending=null,headers=0;
  for (const line of lines.slice(1,-1)) {
    const header=line.match(/^\*\*\* (Add File|Update File|Delete File|Move to): (.+)$/);
    if (!header) continue;
    if (++headers>64) return [];
    if (header[1]==='Move to') {
      if (pending?.kind!=='Update File' || pending.moved) return [];
      pending.file=header[2];pending.moved=true;
    } else {
      if (pending && pending.kind!=='Delete File') files.push(pending.file);
      pending={kind:header[1],file:header[2]};
    }
  }
  if (pending && pending.kind!=='Delete File') files.push(pending.file);
  return [...new Set(files)];
}

export function createWorkspaceProjects({stateDir=path.join(process.env.OPENCLAW_STATE_DIR || path.join(os.homedir(), '.openclaw'), '.ods-workspace-projects'), now=()=>new Date().toISOString(), maximumSessions=512}={}) {
  const identity = sessionKey => typeof sessionKey === 'string' && SESSION.test(sessionKey)
    ? createHash('sha256').update(sessionKey).digest('hex') : null;
  function directory(create=false) {
    if (!path.isAbsolute(stateDir)) throw new Error('Private project registry required');
    if (create) fs.mkdirSync(stateDir, {recursive:true, mode:0o700});
    const info=fs.lstatSync(stateDir);
    if (!info.isDirectory() || info.isSymbolicLink() || (process.platform!=='win32'
        && (info.uid!==process.getuid() || (info.mode & 0o077)!==0))) throw new Error('Private project registry required');
    return stateDir;
  }
  function read(id) {
    const file=path.join(directory(), `${id}.json`);
    const before=fs.lstatSync(file);
    if (!before.isFile() || before.isSymbolicLink() || before.nlink!==1 || before.size>MAX_BYTES
        || (process.platform!=='win32' && (before.uid!==process.getuid() || (before.mode & 0o077)!==0))) throw new Error('Invalid project registry');
    const fd=fs.openSync(file,fs.constants.O_RDONLY | (fs.constants.O_NOFOLLOW || 0));
    let data;
    try {
      const info=fs.fstatSync(fd);
      if (info.ino!==before.ino || info.dev!==before.dev) throw new Error('Changed project registry');
      const bytes=Buffer.alloc(MAX_BYTES+1), length=fs.readSync(fd,bytes,0,bytes.length,0);
      if (length>MAX_BYTES) throw new Error('Invalid project registry');
      data=JSON.parse(bytes.subarray(0,length).toString('utf8'));
    } finally {fs.closeSync(fd);}
    if (!exact(data,'schemaVersion,sessionHash,workspaceRoot,projects') || data.schemaVersion!==1 || data.sessionHash!==id
        || typeof data.workspaceRoot!=='string' || !path.isAbsolute(data.workspaceRoot)
        || !Array.isArray(data.projects) || data.projects.length>MAX_PROJECTS) throw new Error('Invalid project registry');
    const seen=new Set();
    for(const item of data.projects) {
      if (!exact(item,'directory,file,observedAt') || typeof item.directory!=='string'
          || item.directory.split('/').length!==2 || !item.directory.startsWith('Playground/') || !projectName(item.directory.slice(11))
          || typeof item.file!=='string' || !item.file.startsWith(`${item.directory}/`) || !stamp(item.observedAt)
          || seen.has(item.directory)) throw new Error('Invalid project registry');
      seen.add(item.directory);
    }
    return data;
  }
  return {
    record({sessionKey,workspaceRoot,file,directory:projectDirectory,kind}) {
      if (!['write','edit','apply_patch','exec'].includes(kind)) return false;
      const id=identity(sessionKey);
      if (!id) return false;
      let temporary;
      try {
        const checked=kind==='exec' ? projectFile(workspaceRoot,projectDirectory) : checkedFile(workspaceRoot,file), observedAt=now();
        if (!checked || !stamp(observedAt)) return false;
        let previous=null;
        try {previous=read(id);}catch(error) {if(error.code!=='ENOENT') return false;}
        const folder=directory(true), target=path.join(folder,`${id}.json`);
        if (!previous && fs.readdirSync(folder).filter(name=>name.endsWith('.json')).length>=maximumSessions) return false;
        const projects=[{directory:checked.directory,file:checked.file,observedAt},
          ...(previous?.workspaceRoot===checked.workspaceRoot?previous.projects.filter(item=>item.directory!==checked.directory):[])].slice(0,MAX_PROJECTS);
        temporary=path.join(folder,`.write-${randomBytes(16).toString('hex')}`);
        const fd=fs.openSync(temporary,fs.constants.O_WRONLY|fs.constants.O_CREAT|fs.constants.O_EXCL,0o600);
        try {fs.writeFileSync(fd,JSON.stringify({schemaVersion:1,sessionHash:id,workspaceRoot:checked.workspaceRoot,projects}));fs.fsyncSync(fd);}
        finally {fs.closeSync(fd);}
        fs.renameSync(temporary,target);temporary=null;
        return true;
      }catch {return false;}
      finally {if(temporary)try{fs.unlinkSync(temporary);}catch{}}
    },
    forSession(sessionKey) {
      const id=identity(sessionKey);
      if (!id) return [];
      try {
        const data=read(id);
        return data.projects.flatMap(item=>{
          try {
            let checked;
            try {checked=checkedFile(data.workspaceRoot,item.file);}catch{}
            checked ??= projectFile(data.workspaceRoot,item.directory);
            return checked?.directory===item.directory
              ? [{schemaVersion:1,kind:'ods-workspace-project',relativeDirectory:item.directory,observedAt:item.observedAt}] : [];
          }catch{return [];}
        });
      }catch{return [];}
    },
  };
}
