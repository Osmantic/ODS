import {test} from 'node:test';
import assert from 'node:assert/strict';
import {environment} from './ods-config.mjs';
const valid={UMAMI_INITIAL_PASSWORD:'twelve-or-more',UMAMI_TWO_FACTOR_KEY:'a'.repeat(64),UMAMI_APP_SECRET:'s'.repeat(32),UMAMI_DATABASE_PASSWORD:'p@ss:/?#% with space'};
test('database password cannot alter the connection destination',()=>{
 const result=environment(valid),url=new URL(result.DATABASE_URL);
 assert.equal(url.hostname,'umami-db');assert.equal(url.pathname,'/umami');assert.equal(decodeURIComponent(url.password),valid.UMAMI_DATABASE_PASSWORD);
 assert.equal(url.search,'');assert.equal(url.hash,'');
});
test('invalid secrets fail without exposing their contents',()=>{
 for(const [key,value] of [['UMAMI_INITIAL_PASSWORD','short'],['UMAMI_INITIAL_PASSWORD','界'.repeat(25)],['UMAMI_TWO_FACTOR_KEY','z'.repeat(64)],['UMAMI_APP_SECRET','small'],['UMAMI_DATABASE_PASSWORD','']]) {
  assert.throws(()=>environment({...valid,[key]:value}),error=>{if(value) assert.equal(error.message.includes(value),false);return true;});
 }
});
