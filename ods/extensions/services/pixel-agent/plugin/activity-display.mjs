// Explicit public status and bounded, filtered tool metadata/excerpts only.
// Never project private reasoning or arbitrary tool output bodies.
export const ACTIVITY_CONTRACT = 'For multi-step work, use pixel_ods_activity to send brief public progress updates in the owner language: what you are doing or what you checked. This appears above the answer. Use {message:"..."}. Do not expose private reasoning, secrets or raw tool output. Updates do not execute the task: use the real tools, then deliver the answer. Send one concise update before the first meaningful action, then only at a meaningful phase change, a verified finding, or a blocker. Keep updates in the owner language and grounded in actual work. Do not narrate every tool call, repeat an earlier update, reveal internal deliberation, or claim success before checking. Skip progress updates for simple conversation.';
const text = (value, max) => typeof value === 'string' ? value.replace(/[\u0000-\u001f\u007f]/g,' ').trim().slice(0,max) : '';
export function activityToolName(event, context) {
  const name=context?.toolName ?? event?.toolName;
  return name==='tool_call' ? String(event?.params?.id ?? '').split(':').at(-1) : name;
}
const argsFor=(event,context)=>(context?.toolName ?? event?.toolName)==='tool_call' ? event?.params?.args : event?.params;
// Display only bounded excerpts; never mirror credential files or secret-bearing
// lines into the browser's conversation history.
function excerpt(value) {
  const clean=String(value ?? '').replace(/\r\n/g,'\n').replace(/[\u0000-\u0008\u000b-\u001f\u007f]/g,'');
  const safe=clean.split('\n').map(line=>/authorization|bearer\s|api[_-]?key|access[_-]?token|password\s*[:=]|secret\s*[:=]|BEGIN .*PRIVATE KEY/i.test(line)?'[sensitive line omitted]':line).join('\n');
  return {text:safe.slice(0,1000),truncated:safe.length>1000};
}
function source(value) {
  try {
    const url=new URL(value?.url ?? value?.source_url);
    if(!['http:','https:'].includes(url.protocol) || url.username || url.password || url.href.length>512
      || [...url.searchParams.keys()].some(key=>/token|secret|password|credential|signature|api.?key/i.test(key)))return null;
    // Core web tools wrap titles as untrusted evidence. Remove only their
    // display envelope; the original evidence given to the model is intact.
    const title=typeof value.title==='string'?value.title
      .replace(/<<<(?:END_)?EXTERNAL_UNTRUSTED_CONTENT(?:\s+id="[^"]*")?>>>/g,'')
      .replace(/^\s*Source:\s*Web (?:Search|Fetch)\s*\r?\n---\s*\r?\n/i,''):'';
    return {title:text(title,120)||url.hostname,url:url.href};
  } catch {return null;}
}
export function displayForActivity(event, context, previous=null) {
  const name=activityToolName(event,context), args=argsFor(event,context) || {};
  const display=previous ? structuredClone(previous) : {type:'tool',label:'Using a tool',detail:null,sources:[],steps:[],change:null};
  const labels={read:'Reading a file',ls:'Listing files',glob:'Finding files',grep:'Searching files',write:'Writing a file',edit:'Editing a file',apply_patch:'Applying changes',exec:'Running a command',shell:'Running a command',bash:'Running a command',process:'Checking a process',tool_search:'Finding available tools',tool_describe:'Checking tool parameters',session_status:'Checking the session',pixel_ods_status:'Checking ODS',pixel_ods_workspace_preview:'Publishing a preview',pixel_ods_ask_user:'Asking for your input',sessions_spawn:'Starting an agent',sessions_send:'Coordinating an agent'};
  if(labels[name])display.label=labels[name];
  if(['read','write','edit','apply_patch','ls','glob','grep'].includes(name)) {
    const path=args.path ?? args.file_path ?? args.filePath;
    if(typeof path==='string')display.detail=text(path.split(/[\\/]/).filter(Boolean).at(-1),120)||null;
  }
  if(['exec','shell','bash'].includes(name) && typeof args.command==='string')display.detail=text(excerpt(args.command).text,400)||null;
  // The UI exposes changes only after the associated tool reports completion.
  if(['write','edit','apply_patch'].includes(name)) {
    const patch=args.patch ?? args.input;
    const path=args.path ?? args.file_path ?? args.filePath;
    const file=typeof path==='string'?path.split(/[\\/]/).filter(Boolean).at(-1):'Patch';
    if(!/^(?:\.env(?:\.|$)|credentials|secrets|id_rsa|id_ed25519)|\.(?:pem|key)$/i.test(file ?? '')) {
      const before=excerpt(args.oldText ?? args.old_string ?? '');
      const after=excerpt(name==='write'?args.content:name==='edit'?args.newText ?? args.new_string:patch);
      if((name==='write' && typeof args.content==='string') || (name==='edit' && typeof (args.newText ?? args.new_string)==='string') || (name==='apply_patch' && typeof patch==='string'))
        display.change={file:text(file,120)||'File',kind:name==='apply_patch'?'patch':name,before:before.text,after:after.text,truncated:before.truncated||after.truncated};
      if(name==='edit' && Array.isArray(args.edits) && args.edits.length) {
        // Newer runtimes accept multiple independent replacements. Keep hunk
        // boundaries instead of inventing unchanged content between them.
        const edits=args.edits.slice(0,8).filter(edit=>typeof edit?.oldText==='string' && typeof edit?.newText==='string');
        let clipped=args.edits.length>8 || edits.length!==args.edits.length;
        const hunks=edits.map(edit=>{
          const before=excerpt(edit.oldText),after=excerpt(edit.newText);
          clipped ||= before.truncated || after.truncated;
          const prefix=(value,sign)=>value?value.replace(/\n$/,'').split('\n').map(line=>sign+line).join('\n'):'';
          return ['@@ Replacement @@',prefix(before.text,'-'),prefix(after.text,'+')].filter(Boolean).join('\n');
        }).join('\n');
        const bounded=excerpt(hunks);
        if(edits.length)display.change={file:text(file,120)||'File',kind:'patch',before:'',after:bounded.text,truncated:clipped||bounded.truncated};
      }
    }
  }
  if(['web_search','web_fetch','pixel_ods_research','pixel_ods_web_extract','browser'].includes(name)) {
    display.type='search';display.label=text(args.query ?? args.search ?? args.search_query,160)||previous?.label||'Browsing the web';
    const link=source({url:args.url});
    if(link && !display.sources.some(s=>s.url===link.url))display.sources.push(link);
    // Core tools and the installed researcher expose structured sources. Do
    // not scrape freeform text or mistake the wrapper envelope for evidence.
    let result=event?.result;
    for(let i=0;i<3 && result;i++) {
      const details=result.details;
      for(const item of (Array.isArray(details?.sources)?details.sources:Array.isArray(details?.results)?details.results:[])) {
        const link=source(item);
        if(link && !display.sources.some(s=>s.url===link.url))display.sources.push(link);
        if(display.sources.length>=3)break;
      }
      result=details?.result ?? result.result;
    }
    display.sources=display.sources.slice(0,3);
  }
  if(name==='pixel_ods_goal') {
    display.type='steps'; display.label=text(args.summary,160)||previous?.label||'Updating the plan';
    if(Array.isArray(args.steps))display.steps=args.steps.slice(0,8).filter(s=>s && typeof s.id==='string' && /^[a-z][a-z0-9_]{0,31}$/.test(s.id) && ['pending','running','completed','blocked'].includes(s.status) && text(s.title,160)).map(s=>({id:s.id,title:text(s.title,160),status:s.status}));
  }
  if(name==='pixel_ods_activity') { display.type='text';display.label=text(args.message,160)||previous?.label||'Updating progress'; }
  if(name==='pixel_ods_ask_user')display.type='trace';
  return display;
}
export function createActivityTool() {
  return {name:'pixel_ods_activity',description:'Send a short public progress update to the owner during multi-step work. Say what you are doing or what you verified, not private reasoning. This only updates the interface; continue using real tools and deliver the result.',
    parameters:{type:'object',additionalProperties:false,required:['message'],properties:{message:{type:'string',minLength:1,maxLength:160}}},
    async execute(_id,params) {
      if(!params || Object.keys(params).join(',')!=='message' || typeof params.message!=='string' || !params.message.trim() || params.message.length>160 || /[\u0000-\u001f\u007f]/.test(params.message))return {isError:true,content:[{type:'text',text:'Provide only message: a short public update of up to 160 characters.'}]};
      return {content:[{type:'text',text:'Progress shared. Continue the actual work and deliver the result.'}]};
    }};
}
