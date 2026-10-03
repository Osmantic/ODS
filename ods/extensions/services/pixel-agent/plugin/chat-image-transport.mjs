import http from 'node:http';
import {execFile} from 'node:child_process';
import {performance} from 'node:perf_hooks';

const USER=/^ods-[a-f0-9]{64}$/;
const SHA=/^[a-f0-9]{64}$/;
const LIMITS=Object.freeze({'/v1/chat/image':12*1024*1024,'/v1/chat/image-policy':2048});
const fail=()=>Object.assign(new Error('image-read-unavailable'),{code:'image-read-unavailable'});
const exact=(value,keys)=>value && typeof value==='object' && !Array.isArray(value)
  && Object.keys(value).sort().join(',')===keys;
const INSPECT='{{json .Id}}\n{{json .Image}}\n{{json .State.Running}}\n{{json (index .Config.Labels "com.docker.compose.project")}}\n{{json (index .Config.Labels "com.docker.compose.service")}}\n{{json .Config.User}}';

// The program and route enum are fixed. Caller data travels only on stdin.
const CLIENT=`import http from 'node:http';
const timer=setTimeout(()=>process.exit(1),4500);
let size=0;const parts=[];
for await(const chunk of process.stdin){size+=chunk.length;if(size>1024)process.exit(1);parts.push(chunk);}
let input;try{input=JSON.parse(Buffer.concat(parts).toString('utf8'));}catch{process.exit(1);}
const limits={'/v1/chat/image':12582912,'/v1/chat/image-policy':2048};
if(!input || Object.keys(input).sort().join(',')!=='body,path' || !Object.hasOwn(limits,input.path))process.exit(1);
const value=input.body;
if(!value || !/^ods-[a-f0-9]{64}$/.test(value.user || '') ||
 (input.path==='/v1/chat/image-policy' ? Object.keys(value).join(',')!=='user' :
 Object.keys(value).sort().join(',')!=='id,sha256,user' || !/^img-[a-f0-9]{32}$/.test(value.id || '') || !/^[a-f0-9]{64}$/.test(value.sha256 || '')))process.exit(1);
const body=JSON.stringify(value);
const req=http.request({socketPath:'/runtime/pixel-ingress.sock',path:input.path,method:'POST',headers:{'content-type':'application/json','content-length':Buffer.byteLength(body)}},res=>{
 let size=0;const parts=[];
 res.on('data',chunk=>{size+=chunk.length;if(size>limits[input.path])process.exit(1);parts.push(chunk);});
 res.on('error',()=>process.exit(1));res.on('aborted',()=>process.exit(1));
 res.on('end',()=>{if(res.statusCode!==200)process.exit(1);clearTimeout(timer);process.stdout.write(Buffer.concat(parts));});
});req.on('error',()=>process.exit(1));req.end(body);`;

async function dockerRead(path,body,{dockerPath,project,image,containerUser,execute=execFile,signal,now=()=>performance.now()},deadline) {
  if(typeof dockerPath!=='string' || !dockerPath.startsWith('/') || /[\0\r\n]/.test(dockerPath)
      || !/^[a-z0-9][a-z0-9_-]{0,127}$/.test(project || '') || !/^sha256:[a-f0-9]{64}$/.test(image || '')
      || !/^[1-9][0-9]*:[0-9]+$/.test(containerUser || '')) throw fail();
  const run=(argv,input='',limit=65536)=>new Promise((resolve,reject)=>{
    const remaining=deadline-now();
    if(signal?.aborted || remaining<=0) return reject(fail());
    let child,settled=false,failed=false;
    const finish=(error,value)=>{
      if(settled)return;settled=true;clearTimeout(timer);signal?.removeEventListener('abort',abort);
      if(error){failed=true;child?.kill?.('SIGKILL');reject(fail());}else resolve(value);
    };
    const abort=()=>finish(true);
    const timer=setTimeout(abort,remaining);
    signal?.addEventListener('abort',abort,{once:true});
    try {
      child=execute(dockerPath,argv,{timeout:remaining,maxBuffer:limit,encoding:'utf8',killSignal:'SIGKILL',signal},
        (error,stdout)=>finish(error || typeof stdout!=='string' || Buffer.byteLength(stdout)>limit,stdout));
      child.stdin.on('error',abort);child.stdin.end(input);
      if(signal?.aborted || failed) child.kill?.('SIGKILL');
    }catch{finish(true);}
  });
  const ids=(await run(['ps','--no-trunc','--filter','label=com.docker.compose.project='+project,
    '--filter','label=com.docker.compose.service=pixel-native-ingress','--format','{{.ID}}'])).trim().split('\n');
  if(ids.length!==1 || !SHA.test(ids[0]))throw fail();
  const id=ids[0];let identity;
  try{identity=(await run(['inspect','--format',INSPECT,id])).trim().split('\n').map(line=>JSON.parse(line));}catch{throw fail();}
  if(identity.length!==6 || identity[0]!==id || identity[1]!==image || identity[2]!==true
      || identity[3]!==project || identity[4]!=='pixel-native-ingress' || identity[5]!==containerUser)throw fail();
  return run(['exec','-i','--env','NODE_OPTIONS=',id,'node','--input-type=module','-e',CLIENT],
    JSON.stringify({path,body}),LIMITS[path]);
}

function unixRead(path,body,{socketPath,request=http.request,signal,now=()=>performance.now()},deadline) {
  return new Promise((resolve,reject)=>{
    if(signal?.aborted || deadline<=now())return reject(fail());
    let req,res,settled=false;
    const finish=(error,value)=>{
      if(settled)return;settled=true;clearTimeout(timer);signal?.removeEventListener('abort',abort);
      if(error){res?.destroy();req?.destroy();reject(fail());}else resolve(value);
    };
    const abort=()=>finish(true),timer=setTimeout(abort,deadline-now());
    signal?.addEventListener('abort',abort,{once:true});
    try {
      const data=JSON.stringify(body);
      req=request({socketPath,path,method:'POST',headers:{'content-type':'application/json','content-length':Buffer.byteLength(data)}},response=>{
        res=response;let size=0;const parts=[];
        response.on('error',abort);response.on('aborted',abort);
        response.on('data',chunk=>{size+=chunk.length;if(size>LIMITS[path])abort();else parts.push(chunk);});
        response.on('end',()=>finish(response.statusCode!==200,Buffer.concat(parts).toString('utf8')));
      });
      req.on('error',abort);req.end(data);
      if(signal?.aborted)abort();
    }catch{finish(true);}
  });
}

let active=0;
async function read(path,body,options={}) {
  if(active>=2 || !USER.test(body.user || '') || body.user.length!==68
      || Buffer.byteLength(JSON.stringify({path,body}))>1024 || options.signal?.aborted)throw fail();
  const deps={socketPath:process.env.PIXEL_INGRESS_SOCKET || '/run/ods-pixel/pixel-ingress.sock',
    transport:process.env.PIXEL_HISTORY_TRANSPORT || 'unix',dockerPath:process.env.PIXEL_HISTORY_DOCKER,
    project:process.env.PIXEL_HISTORY_PROJECT,image:process.env.PIXEL_HISTORY_IMAGE,
    containerUser:process.env.PIXEL_HISTORY_USER,now:()=>performance.now(),...options};
  active++;
  try {
    const deadline=deps.now()+5000;
    const raw=deps.transport==='unix'?await unixRead(path,body,deps,deadline)
      :deps.transport==='docker-exec'?await dockerRead(path,body,deps,deadline):null;
    if(deps.signal?.aborted || typeof raw!=='string' || Buffer.byteLength(raw)>LIMITS[path])throw fail();
    return JSON.parse(raw);
  }catch{throw fail();}finally{active--;}
}

export async function readConversationImage(user,reference,options={}) {
  if(!exact(reference,'id,sha256') || typeof reference.id!=='string' || reference.id.length!==36 || !/^img-[a-f0-9]{32}$/.test(reference.id)
      || typeof reference.sha256!=='string' || reference.sha256.length!==64 || !SHA.test(reference.sha256))throw fail();
  const value=await read('/v1/chat/image',{user,id:reference.id,sha256:reference.sha256},options);
  if(!exact(value,'image,schemaVersion') || value.schemaVersion!==1 || !exact(value.image,'bytes,data,id,mimeType,sha256')
      || value.image.id!==reference.id || value.image.sha256!==reference.sha256
      || !['image/png','image/jpeg','image/webp'].includes(value.image.mimeType)
      || !Number.isInteger(value.image.bytes) || value.image.bytes<1 || value.image.bytes>8*1024*1024
      || typeof value.image.data!=='string' || value.image.data.length!==4*Math.ceil(value.image.bytes/3))throw fail();
  // The image tool verifies canonical base64, decoded byte count and SHA-256.
  return value;
}

export async function readConversationImagePolicy(user,options={}) {
  const value=await read('/v1/chat/image-policy',{user},options);
  if(!exact(value,'policy,schemaVersion') || value.schemaVersion!==1 || !exact(value.policy,'imageInput,routeFingerprint,unknownConsent')
      || !['supported','unsupported','unknown'].includes(value.policy.imageInput)
      || typeof value.policy.routeFingerprint!=='string' || value.policy.routeFingerprint.length!==64 || !SHA.test(value.policy.routeFingerprint)
      || typeof value.policy.unknownConsent!=='boolean')throw fail();
  return value;
}
