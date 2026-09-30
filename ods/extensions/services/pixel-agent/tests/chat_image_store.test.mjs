import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {createHash} from 'node:crypto';
import {spawnSync} from 'node:child_process';
import {createChatImageStore, createChatImageReadHandler, normalizeChatImageReference, CHAT_IMAGE_CACHE_BYTES} from '../host/chat_image_store.mjs';
import {createChatImageReadTool} from '../plugin/chat-image-read.mjs';

const user = `ods-${'a'.repeat(64)}`, other = `ods-${'b'.repeat(64)}`;
const data = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aR9kAAAAASUVORK5CYII=', 'base64');
const digest = value => createHash('sha256').update(value).digest('hex');
const image = {id:`img-${'1'.repeat(32)}`, sha256:digest(data), mimeType:'image/png', data};
const ref = ({id,sha256}) => ({id,sha256});
function setup(t, options) {
  const temporary = fs.mkdtempSync(path.join(os.tmpdir(), 'ods-images-test-'));
  t.after(() => fs.rmSync(temporary, {recursive:true, force:true}));
  const directory = path.join(temporary,'private');
  return {directory, store:createChatImageStore(directory, options)};
}

test('a process dying after publish recovers without deleting the published image', t => {
  const {directory,store}=setup(t);
  const moduleUrl=new URL('../host/chat_image_store.mjs',import.meta.url).href;
  const script=`import fs from 'node:fs'; import {createChatImageStore} from ${JSON.stringify(moduleUrl)};
    const original=fs.linkSync; fs.linkSync=(...args)=>{original(...args);process.exit(91)};
    createChatImageStore(${JSON.stringify(directory)}).put(${JSON.stringify(user)},
      [{...${JSON.stringify({...image,data:undefined})},data:Buffer.from(${JSON.stringify(data.toString('base64'))},'base64')}],${JSON.stringify([ref(image)])});`;
  const child=spawnSync(process.execPath,['--input-type=module','-e',script],{encoding:'utf8'});
  assert.equal(child.status,91,child.stderr);
  assert.ok(fs.readdirSync(directory).some(name=>name.includes('.tmp-')));
  const restarted=createChatImageStore(directory);
  assert.deepEqual(restarted.read(user,ref(image),[ref(image)]).data,data);
  assert.deepEqual(store.put(user,[image],[ref(image)]),[ref(image)]);
  assert.deepEqual(store.read(user,ref(image),[ref(image)]).data,data);
  assert.equal(fs.readdirSync(directory).length,1);
});

test('a live writer lease refuses a second writer without removing either image or lease', t => {
  const {directory,store}=setup(t);
  const lease=path.join(directory,`.write-lock-${process.pid}-${'a'.repeat(32)}`);
  fs.writeFileSync(lease,'',{mode:0o600});
  assert.throws(()=>createChatImageStore(directory),error=>error.code==='image-storage-busy');
  assert.throws(()=>store.put(user,[image],[ref(image)]),error=>error.code==='image-storage-busy');
  assert.deepEqual(fs.readdirSync(directory),[path.basename(lease)]);
});

test('startup discards an interrupted unpublished copy and keeps prior published images', t => {
  const {directory,store}=setup(t);
  store.put(user,[image],[ref(image)]);
  const second={...image,id:`img-${'2'.repeat(32)}`};
  const moduleUrl=new URL('../host/chat_image_store.mjs',import.meta.url).href;
  const script=`import fs from 'node:fs';import {createChatImageStore} from ${JSON.stringify(moduleUrl)};
    const store=createChatImageStore(${JSON.stringify(directory)});
    const original=fs.writeFileSync;fs.writeFileSync=(...args)=>{original(...args);process.exit(92)};
    store.put(${JSON.stringify(user)},[{...${JSON.stringify({...second,data:undefined})},data:Buffer.from(${JSON.stringify(data.toString('base64'))},'base64')}],${JSON.stringify([ref(second)])});`;
  const child=spawnSync(process.execPath,['--input-type=module','-e',script],{encoding:'utf8'});
  assert.equal(child.status,92,child.stderr);
  assert.ok(fs.readdirSync(directory).some(name=>name.includes('.tmp-')));
  const restarted=createChatImageStore(directory);
  assert.deepEqual(restarted.read(user,ref(image),[ref(image)]).data,data);
  assert.throws(()=>restarted.read(user,ref(second),[ref(second)]),error=>error.code==='image-copy-unavailable');
  assert.equal(fs.readdirSync(directory).length,1);
});

test('immutable private copy survives restart and transcript pruning, with exact digest', async t => {
  const {directory, store} = setup(t);
  const refs = [ref(image)];
  assert.deepEqual(store.put(user, [image], refs), refs);
  const before = fs.readdirSync(directory).map(name => [name, fs.statSync(path.join(directory,name)).mtimeMs]);
  assert.deepEqual(store.put(user, [image], refs), refs);
  assert.deepEqual(fs.readdirSync(directory).map(name => [name,fs.statSync(path.join(directory,name)).mtimeMs]), before);
  const restarted = createChatImageStore(directory);
  // The native transcript may contain zero image parts; custody is the ledger.
  const handler = createChatImageReadHandler(restarted, async scope => scope === user ? refs : []);
  const response = await handler(user, refs[0]);
  assert.equal(response.schemaVersion, 1);
  assert.deepEqual(Buffer.from(response.image.data,'base64'), data);
  assert.equal(digest(Buffer.from(response.image.data,'base64')), refs[0].sha256);
  await assert.rejects(handler(other, refs[0]), {code:'image-not-admitted'});
  if (process.platform !== 'win32') {
    assert.equal(fs.statSync(directory).mode & 0o777, 0o700);
    assert.equal(fs.statSync(path.join(directory, before[0][0])).mode & 0o777, 0o600);
  }
});

test('unadmitted references, cross-chat reads and hash drift fail without new files', t => {
  const {directory, store} = setup(t);
  assert.throws(() => store.put(user, [image], []), {code:'image-not-admitted'});
  assert.equal(fs.readdirSync(directory).length, 0);
  store.put(user, [image], [ref(image)]);
  assert.throws(() => store.read(other, ref(image), [ref(image)]), {code:'image-copy-unavailable'});
  const changed = {...image, data:Buffer.concat([data, Buffer.from('changed')])}; changed.sha256 = digest(changed.data);
  assert.throws(() => store.put(user, [changed], [ref(changed)]), {code:'image-identity-conflict'});
  assert.throws(() => store.read(user, ref(image), [ref(changed)]), {code:'image-identity-conflict'});
  assert.deepEqual(store.read(user, ref(image), [ref(image)]).data, data);
});

test('reference and user grammars reject terminal newline and non-string values exactly', async t => {
  const {store} = setup(t);
  for (const suffix of ['\n','\r\n']) {
    assert.throws(() => normalizeChatImageReference({...ref(image),id:image.id+suffix}), {code:'invalid-image-reference'});
    assert.throws(() => normalizeChatImageReference({...ref(image),sha256:image.sha256+suffix}), {code:'invalid-image-reference'});
    assert.throws(() => store.put(user+suffix,[image],[ref(image)]), {code:'invalid-image-user'});
  }
  for (const value of [null,0,[],{}]) {
    assert.throws(() => normalizeChatImageReference({...ref(image),id:value}), {code:'invalid-image-reference'});
    assert.throws(() => normalizeChatImageReference({...ref(image),sha256:value}), {code:'invalid-image-reference'});
    assert.throws(() => store.put(value,[image],[ref(image)]), {code:'invalid-image-user'});
  }
  const handler = createChatImageReadHandler(store,()=>assert.fail('invalid user must not reach ledger'));
  await assert.rejects(handler(user+'\n',ref(image)),{code:'invalid-image-user'});
});

test('invalid input and whole-turn bounds are validated before storage mutation', t => {
  const {directory, store} = setup(t);
  for (const invalid of [{...image, mimeType:'image/svg+xml'}, {...image, sha256:'f'.repeat(64)},
    {...image, data:'data:image/png;base64,'}, {...image, id:'../../secret'}, {...image, path:'/etc/passwd'}]) {
    assert.throws(() => store.put(user, [invalid], [ref(image)]));
    assert.deepEqual(fs.readdirSync(directory), []);
  }
  assert.throws(() => store.put(user,[image,image],[ref(image)]), {code:'duplicate-image-reference'});
  const large = Buffer.alloc(5*1024*1024); data.subarray(0,8).copy(large);
  const one = {...image,data:large,sha256:digest(large)}, two = {...one,id:`img-${'2'.repeat(32)}`};
  assert.throws(() => store.put(user,[one,two],[ref(one),ref(two)]), {code:'image-turn-too-large'});
  assert.deepEqual(fs.readdirSync(directory), []);
});

test('global physical quota refuses new images without evicting existing receipts', t => {
  const {directory, store} = setup(t, {maxBytes:600});
  store.put(user,[image],[ref(image)]);
  const second = {...image,id:`img-${'2'.repeat(32)}`};
  assert.throws(() => store.put(other,[second],[ref(second)]), {code:'image-storage-limit'});
  assert.deepEqual(store.read(user,ref(image),[ref(image)]).data,data);
  assert.equal(fs.readdirSync(directory).length,1);
  assert.throws(() => createChatImageStore(directory,{maxBytes:CHAT_IMAGE_CACHE_BYTES+1}), {code:'invalid-image-storage-limit'});
});

test('tampered bytes and persisted cross-chat metadata never become tool images', t => {
  const {directory, store} = setup(t);
  store.put(user,[image],[ref(image)]);
  const file = path.join(directory,fs.readdirSync(directory)[0]);
  const bytes = fs.readFileSync(file); bytes[bytes.length-1] ^= 1; fs.writeFileSync(file,bytes);
  assert.throws(() => store.read(user,ref(image),[ref(image)]), {code:'image-identity-conflict'});
});

test('stale writer lock fails visibly instead of guessing another operation completed', t => {
  const {directory, store} = setup(t);
  fs.mkdirSync(path.join(directory,'.write-lock'),{mode:0o700});
  assert.throws(() => store.put(user,[image],[ref(image)]), {code:'image-storage-busy'});
  assert.deepEqual(fs.readdirSync(directory),['.write-lock']);
});

test('symlink, hardlink and public permissions are rejected', {skip:process.platform === 'win32'}, t => {
  const {directory, store} = setup(t);
  store.put(user,[image],[ref(image)]);
  const file = path.join(directory,fs.readdirSync(directory)[0]), external = path.join(path.dirname(directory),'external');
  fs.linkSync(file,external);
  assert.throws(() => store.read(user,ref(image),[ref(image)]), {code:'image-storage-unavailable'});
  fs.unlinkSync(external);
  fs.renameSync(file,external); fs.symlinkSync(external,file);
  assert.throws(() => store.read(user,ref(image),[ref(image)]), {code:'image-storage-unavailable'});
  fs.unlinkSync(file); fs.renameSync(external,file); fs.chmodSync(file,0o644);
  assert.throws(() => store.read(user,ref(image),[ref(image)]), {code:'image-storage-unavailable'});
});

test('store -> authenticated handler -> tool reuses bytes after native-session recreation only with ledger admission', async t => {
  const {directory, store} = setup(t);
  store.put(user,[image],[ref(image)]);
  let refs = [ref(image)];
  const handler = createChatImageReadHandler(createChatImageStore(directory), async scope => scope === user ? refs : []);
  const context = {agentId:'pixel', sessionKey:`agent:pixel:openai-user:${user}`, sessionId:'recreated-native-session'};
  const tool = createChatImageReadTool(context, {
    getSessionEntry:async () => ({sessionId:context.sessionId}),
    readImage:handler,
    imagePolicyForContext:async () => ({imageInput:'unknown',routeFingerprint:'f'.repeat(64),unknownConsent:true}),
  });
  const result = await tool.execute('call',ref(image));
  assert.deepEqual(Buffer.from(result.content.find(item=>item.type==='image').data,'base64'),data);
  refs = [];
  const denied = await tool.execute('next',ref(image));
  assert.equal(denied.details.errorCode,'image-not-admitted');
  assert.equal(denied.content.some(item=>item.type==='image'),false);
});
