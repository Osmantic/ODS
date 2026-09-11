// Only the configured owner workspace is an alias for /workspace. Never infer
// a host root from model arguments or expand traversal.
import {lstatSync} from 'node:fs';
import path from 'node:path';

const selectedTool = id => typeof id === 'string' && /^(?:openclaw:core:)?(?:read|write|edit|exec)$/.test(id)
  ? id.split(':').at(-1) : id === 'pixel_ods_workspace_preview' ? id : undefined;

export function workspaceFileParent(tool, params, root, stat = lstatSync) {
  if (tool === 'tool_call') return workspaceFileParent(selectedTool(params?.id),params?.args,root,stat);
  if (tool !== 'write' || typeof root !== 'string' || !path.isAbsolute(root) || typeof params?.path !== 'string') return undefined;
  const parts=params.path.split('/');
  if (!parts.every(part => /^[A-Za-z0-9][A-Za-z0-9._ -]{0,127}$/.test(part) && part !== '..')) return undefined;
  for(let i=1;i<parts.length;i++) {
    const relative=parts.slice(0,i).join('/');
    let entry;
    try {entry=stat(path.join(root,...parts.slice(0,i)));} catch {return undefined;}
    // Never follow symlinks; the existing core sandbox handles that boundary.
    if(entry.isSymbolicLink()) return undefined;
    if(entry.isFile()) return relative;
    if(!entry.isDirectory()) return undefined;
  }
  return undefined;
}
export function canonicalWorkspaceParams(tool, params, root) {
  if (!params || typeof params !== 'object' || Array.isArray(params)) return params;
  if (tool === 'tool_call') {
    const selected=selectedTool(params.id);
    return selected && params.args ? {...params, args: canonicalWorkspaceParams(selected, params.args, root)} : params;
  }
  if (!['read','write','edit','exec','pixel_ods_workspace_preview'].includes(tool)) return params;
  const result = {...params};
  if (typeof root === 'string' && root.startsWith('/') && root.length > 1) {
    const prefix = root.replace(/\/+$/, '') + '/';
    for (const key of ['path','filePath','directory','relativeDirectory','workdir']) {
      if (typeof result[key] === 'string' && result[key].startsWith(prefix)) result[key] = result[key].slice(prefix.length);
    }
  }
  if (tool === 'pixel_ods_workspace_preview' && Object.keys(result).join() === 'path') return {relativeDirectory:result.path};
  return result;
}

export function extensionlessHtmlWrite(tool, params) {
  if (tool === 'tool_call') return extensionlessHtmlWrite(selectedTool(params?.id), params?.args);
  if (tool !== 'write' || typeof params?.path !== 'string' || typeof params.content !== 'string') return false;
  const leaf = params.path.replace(/\/+$/, '').split('/').at(-1);
  return Boolean(leaf && !leaf.includes('.') && /^\s*(?:<!doctype\s+html\b[^>]*>\s*)?<html\b/i.test(params.content));
}
