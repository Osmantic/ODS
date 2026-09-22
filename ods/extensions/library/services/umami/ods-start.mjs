import {spawn} from 'node:child_process';
import {environment} from './ods-config.mjs';
let env;
try { env = environment(process.env); } catch(error) { console.error(error.message); process.exit(1); }
const child = spawn('sh',['/app/ods-start.sh'],{env,stdio:'inherit'});
for(const signal of ['SIGTERM','SIGINT']) process.on(signal,()=>child.kill(signal));
child.on('error',()=>{console.error('Unable to start Umami');process.exit(1)});
child.on('exit',(code,signal)=>process.exit(code ?? (signal ? 1 : 0)));
