import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {decodeChatImageTurn,nativeHistoryMessages,validateImageRoute} from '../host/chat_image_transport.mjs';

const data=Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a6ioAAAAASUVORK5CYII=','base64');
const ref={id:'img-'+'a'.repeat(32),sha256:createHash('sha256').update(data).digest('hex')};
const archive={role:'user',content:'Read image',images:[ref]};
test('native image admission rechecks route and capability instead of trusting API timing',()=>{
  const route={routeFingerprint:'f'.repeat(64),unknownConsent:false};
  const model={routeFingerprint:route.routeFingerprint,imageInput:'supported'};
  assert.equal(validateImageRoute(route,model).imageInput,'supported');
  assert.throws(()=>validateImageRoute(route,{...model,routeFingerprint:'e'.repeat(64)}),/image-route-changed/);
  assert.throws(()=>validateImageRoute({...route,unknownConsent:true},{...model,imageInput:'unsupported'}),/image-input-unsupported/);
  assert.throws(()=>validateImageRoute(route,{...model,imageInput:'unknown'}),/image-input-unverified/);
  assert.equal(validateImageRoute({...route,unknownConsent:true},{...model,imageInput:'unknown'}).unknownConsent,true);
});
const message=()=>({role:'user',images:[{...ref}],content:[{type:'text',text:'Read image'},
  {type:'image_url',image_url:{url:'data:image/png;base64,'+data.toString('base64')}},
  {type:'text',text:'Trusted Edge delivery guidance'}]});

test('image bytes survive the Edge guidance and are bound to archive references',()=>{
  const current=message(),before=structuredClone(current);
  assert.deepEqual(decodeChatImageTurn(current,archive),[{...ref,mimeType:'image/png',data}]);
  assert.deepEqual(current,before);
  current.images=[{sha256:ref.sha256,id:ref.id}];
  assert.equal(decodeChatImageTurn(current,archive).length,1);
});

test('missing, reordered identity, altered and remote content never becomes text-only',()=>{
  for(const change of [
    value=>{value.content=value.content.filter(part=>part.type==='text');},
    value=>{value.images[0].sha256='b'.repeat(64);},
    value=>{value.content[1].image_url.url='https://example.com/image.png';},
    value=>{value.content[1].image_url.url+='\n';},
    value=>{value.content[1].image_url.url='data:image/png;base64,QQ==';},
  ]) {
    const current=message();change(current);
    assert.throws(()=>decodeChatImageTurn(current,archive),/invalid-image-turn/);
  }
});

test('native history preserves retrieval identity without pretending it carries pixels',()=>{
  const before=structuredClone(archive);
  const [native]=nativeHistoryMessages([archive]);
  assert.equal(native.role,'user');
  assert.match(native.content,/pixel_ods_image_read/);
  assert.ok(native.content.includes(ref.id)&&native.content.includes(ref.sha256));
  assert.ok(!native.content.includes(data.toString('base64')));
  assert.deepEqual(archive,before);
  assert.deepEqual(nativeHistoryMessages([{role:'assistant',content:'hello'}]),[{role:'assistant',content:'hello'}]);
});
