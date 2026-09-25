import {execFile} from 'node:child_process';

const unavailable=()=>new Error('history-unavailable');
const ID=/^[a-f0-9]{64}$/;
const IMAGE=/^sha256:[a-f0-9]{64}$/;
const PROJECT=/^[a-z0-9][a-z0-9_-]{0,127}$/;
const USER=/^[1-9][0-9]*:[0-9]+$/;
const INSPECT='{{json .Id}}\n{{json .Image}}\n{{json .State.Running}}\n{{json (index .Config.Labels "com.docker.compose.project")}}\n{{json (index .Config.Labels "com.docker.compose.service")}}\n{{json .Config.User}}';

// This fixed client has no caller-selected URL, command, path or headers.
// Ingress remains Unix-only; only its existing read-history route is reachable.
const CLIENT=`
import http from 'node:http';
const deadline=setTimeout(()=>process.exit(1),4000);
let size=0;const input=[];
for await(const chunk of process.stdin) {
  size+=chunk.length;input.push(chunk);
  if(size>4096) process.exit(1);
}
let value;
try {value=JSON.parse(Buffer.concat(input).toString('utf8'))} catch {process.exit(1)}
if(!value || Array.isArray(value) || !/^ods-[a-f0-9]{64}$/.test(value.user || '') ||
  Object.keys(value).some(k=>!['user','query','offset','limit'].includes(k)) ||
  (value.query!==undefined && (typeof value.query!=='string' || value.query.length>200)) ||
  (value.offset!==undefined && (!Number.isInteger(value.offset) || value.offset<0 || value.offset>2000)) ||
  (value.limit!==undefined && (!Number.isInteger(value.limit) || value.limit<1 || value.limit>20))) process.exit(1);
const body=JSON.stringify(value);
const req=http.request({socketPath:'/runtime/pixel-ingress.sock',path:'/v1/chat/history',method:'POST',
  headers:{'content-type':'application/json','content-length':Buffer.byteLength(body)}},res=>{
  let size=0;const parts=[];
  res.on('data',chunk=>{size+=chunk.length;if(size>16384) process.exit(1);parts.push(chunk)});
  res.on('error',()=>process.exit(1));
  res.on('end',()=>{
    if(res.statusCode!==200) process.exit(1);
    clearTimeout(deadline);process.stdout.write(Buffer.concat(parts));
  });
});
req.on('error',()=>process.exit(1));req.end(body);
`;

let activeRequests=0;
export async function readHistoryViaDocker(user,args,options={}) {
  if(activeRequests>=2) throw unavailable();
  activeRequests++;
  try {return await executeHistory(user,args,options);}
  finally {activeRequests--;}
}

async function executeHistory(user,args,{dockerPath,project,image,containerUser,
  execute=execFile,now=Date.now}={}) {
  if(typeof dockerPath!=='string' || !dockerPath.startsWith('/') || /[\0\r\n]/.test(dockerPath) ||
    !PROJECT.test(project || '') || !IMAGE.test(image || '') || !USER.test(containerUser || '')) throw unavailable();
  if(!/^ods-[a-f0-9]{64}$/.test(user || '') || !args || typeof args!=='object' || Array.isArray(args) ||
    Object.keys(args).some(k=>!['query','offset','limit'].includes(k)) ||
    (args.query!==undefined && (typeof args.query!=='string' || args.query.length>200)) ||
    (args.offset!==undefined && (!Number.isInteger(args.offset) || args.offset<0 || args.offset>2000)) ||
    (args.limit!==undefined && (!Number.isInteger(args.limit) || args.limit<1 || args.limit>20))) throw unavailable();
  const deadline=now()+5000;
  const run=(argv,input='')=>new Promise((resolve,reject)=>{
    const remaining=deadline-now();
    if(remaining<=0) {reject(unavailable());return;}
    try {
      const child=execute(dockerPath,argv,{timeout:remaining,maxBuffer:64*1024,encoding:'utf8',killSignal:'SIGKILL'},
        (error,stdout)=>error?reject(unavailable()):resolve(stdout));
      child.stdin.on('error',()=>reject(unavailable()));
      child.stdin.end(input);
    } catch {reject(unavailable());}
  });
  const ids=(await run(['ps','--no-trunc','--filter','label=com.docker.compose.project='+project,
    '--filter','label=com.docker.compose.service=pixel-native-ingress','--format','{{.ID}}'])).trim().split('\n');
  if(ids.length!==1 || !ID.test(ids[0])) throw unavailable();
  const id=ids[0];
  let identity;
  try {identity=(await run(['inspect','--format',INSPECT,id])).trim().split('\n').map(line=>JSON.parse(line));}
  catch {throw unavailable();}
  if(identity.length!==6 || identity[0]!==id || identity[1]!==image || identity[2]!==true ||
    identity[3]!==project || identity[4]!=='pixel-native-ingress' || identity[5]!==containerUser) throw unavailable();
  // Exec by inspected immutable ID, never by a reusable container name.
  const result=await run(['exec','-i','--env','NODE_OPTIONS=',id,'node','--input-type=module','-e',CLIENT],
    JSON.stringify({user,...args}));
  if(Buffer.byteLength(result)>16384) throw unavailable();
  return result;
}
