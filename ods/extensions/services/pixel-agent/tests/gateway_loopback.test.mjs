import test from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import {createLoopbackGatewayFetch, gatewayFetch} from '../host/pixel_ingress.mjs';

const ok = () => new Response('{"ok":true}', {headers:{'content-type':'application/json'}});
const origin = 'http://127.0.0.1:18789';
const mutation = {method:'POST',headers:{authorization:'Bearer test-only'},body:'{"request_id":"one"}'};

test('qualifies IPv6 first with a read-only request and keeps the endpoint stable for a burst', async () => {
  const calls=[];
  const fetch=createLoopbackGatewayFetch(async (url,options) => {calls.push({url,options});return ok();});
  await Promise.all([fetch(origin+'/pixel-ods/compact',mutation),fetch(origin+'/pixel-ods/activity',mutation)]);
  await fetch(origin+'/v1/chat/completions',mutation);
  assert.equal(calls.filter(x=>x.url.endsWith('/health')).length,1);
  assert.equal(calls[0].options.method,'GET');
  assert.equal(calls[0].options.body,undefined);
  assert.equal(calls[0].options.headers.authorization,'Bearer test-only');
  assert.ok(calls.every(x=>x.url.startsWith('http://[::1]:18789/')));
  assert.deepEqual(calls.slice(1).map(x=>x.options.body),[mutation.body,mutation.body,mutation.body]);
});

test('IPv4-only installations receive mutations only after a successful IPv4 health probe', async () => {
  const calls=[];
  const fetch=createLoopbackGatewayFetch(async (url,options) => {
    calls.push({url,method:options.method});
    if(url.includes('[::1]')) throw new Error('ECONNREFUSED');
    return ok();
  });
  await fetch(origin+'/pixel-ods/compact',mutation);
  assert.deepEqual(calls,[{url:'http://[::1]:18789/health',method:'GET'},
    {url:origin+'/health',method:'GET'},{url:origin+'/pixel-ods/compact',method:'POST'}]);
});

test('unhealthy discovery never dispatches the pending mutation', async () => {
  const methods=[];
  const fetch=createLoopbackGatewayFetch(async (_url,options) => {methods.push(options.method);return new Response('{}',{status:503});});
  await assert.rejects(fetch(origin+'/pixel-ods/compact',mutation),/unavailable/);
  assert.deepEqual(methods,['GET','GET']);
});

test('a reset after dispatch is never replayed; only the next explicit request rediscovers', async () => {
  const calls=[];let healthyV6=true;
  const fetch=createLoopbackGatewayFetch(async (url,options) => {
    calls.push({url,method:options.method});
    if(url.includes('[::1]') && (!healthyV6 || options.method==='POST')) {
      healthyV6=false;throw new Error('ECONNRESET after accepting request');
    }
    return ok();
  });
  await assert.rejects(fetch(origin+'/v1/chat/completions',mutation),/ECONNRESET/);
  assert.equal(calls.filter(x=>x.method==='POST').length,1);
  assert.equal(calls.some(x=>x.url===origin+'/v1/chat/completions'),false);
  await fetch(origin+'/pixel-ods/context',mutation);
  assert.equal(calls.filter(x=>x.method==='POST').length,2);
  assert.equal(calls.at(-1).url,origin+'/pixel-ods/context');
});

test('body failure after response headers cannot cause replay or address-family switching', async () => {
  let submissions=0;
  const fetch=createLoopbackGatewayFetch(async (_url,options) => {
    if(options.method==='GET') return ok();
    submissions++;
    return new Response(new ReadableStream({start(controller){controller.enqueue(new TextEncoder().encode('partial'));controller.error(new Error('stream reset'));}}));
  });
  const response=await fetch(origin+'/v1/chat/completions',mutation);
  await assert.rejects(response.text(),/stream reset/);
  assert.equal(submissions,1);
});

test('expired discovery is requalified and an aborted caller never sends its body', async () => {
  let time=0, probes=0, submissions=0;
  const fetch=createLoopbackGatewayFetch(async (_url,options) => {
    if(options.method==='GET') probes++;else submissions++;
    return ok();
  },{cacheMs:50,now:()=>time});
  await fetch(origin+'/pixel-ods/context',mutation);
  time=51;await fetch(origin+'/pixel-ods/context',mutation);
  assert.equal(probes,2);
  const controller=new AbortController();controller.abort(new Error('cancelled'));
  await assert.rejects(fetch(origin+'/pixel-ods/compact',{...mutation,signal:controller.signal}),/cancelled/);
  assert.equal(submissions,2);
  await assert.rejects(fetch('http://example.com:18789/',mutation),/invalid local/);
});

test('real IPv4-only listener remains supported by the production transport', async t => {
  const calls=[];
  const server=http.createServer((req,res)=>{calls.push({path:req.url,method:req.method});res.end('{"ok":true}');});
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  t.after(()=>new Promise(resolve=>server.close(resolve)));
  const response=await gatewayFetch(`http://127.0.0.1:${server.address().port}/pixel-ods/compact`,mutation);
  assert.equal(response.status,200);await new Response(response.body).text();
  assert.deepEqual(calls,[{path:'/health',method:'GET'},{path:'/pixel-ods/compact',method:'POST'}]);
});

test('real IPv6 listener is selected even when the IPv4 proxy resets connections', async t => {
  const requests=[];
  const ipv6=http.createServer((req,res)=>{requests.push(req.url);res.end('{"ok":true}');});
  try {
    await new Promise((resolve,reject)=>{ipv6.once('error',reject);ipv6.listen({port:0,host:'::1',ipv6Only:true},resolve);});
  } catch(error) {
    if(['EAFNOSUPPORT','EADDRNOTAVAIL'].includes(error.code)) {t.skip('IPv6 loopback unavailable on this host');return;}
    throw error;
  }
  t.after(()=>new Promise(resolve=>ipv6.close(resolve)));
  const port=ipv6.address().port;
  let ipv4Connections=0;
  const ipv4=http.createServer();
  ipv4.on('connection',socket=>{ipv4Connections++;socket.destroy();});
  await new Promise(resolve=>ipv4.listen(port,'127.0.0.1',resolve));
  t.after(()=>new Promise(resolve=>ipv4.close(resolve)));
  const response=await gatewayFetch(`http://127.0.0.1:${port}/pixel-ods/compact`,mutation);
  assert.equal(response.status,200);await new Response(response.body).text();
  assert.deepEqual(requests,['/health','/pixel-ods/compact']);
  assert.equal(ipv4Connections,0);
});
