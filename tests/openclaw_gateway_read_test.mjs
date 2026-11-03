import assert from 'node:assert/strict';
import {collect, narrow} from '../openclaw_gateway_read.mjs';
const start='2026-10-01', end='2026-10-07';
function reply(status='fresh', total=10) { return {startDate:start,endDate:end,updatedAt:1791331200000,
  sessions:[],totals:{totalTokens:total},cacheStatus:{status,pendingFiles:status==='fresh'?0:1,staleFiles:0},
  aggregates:{byModel:[{provider:'provider',model:'model',totals:{input:3,output:2,cacheRead:5,totalTokens:total}}],
    modelDaily:[{provider:'provider',model:'model',date:end,tokens:total}]}}; }
let calls=[];
const full=await collect(async(method,params)=>{calls.push({method,params});return reply();},start,end);
assert.equal(calls.length,1);assert.equal(full.coverage.complete,true);
assert.deepEqual(calls[0],{method:'sessions.usage',params:{agentScope:'all',startDate:start,endDate:end,mode:'utc',limit:1,offset:20000,includeContextWeight:false}});
assert.equal('projection' in calls[0].params,false);assert.equal('sessions' in full,false);
assert.equal(full.aggregates.byModel[0].totals.cacheRead,5);
calls=[];
const replaced=await collect(async()=>{calls.push(1);return calls.length===1?reply('refreshing',3):reply('fresh',12);},start,end);
assert.equal(calls.length,2);assert.equal(replaced.aggregates.byModel[0].totals.totalTokens,12); // not 3+12 or max by model
assert.equal(replaced.coverage.complete,true);
const partial=await collect(async()=>({...reply('fresh'),partial:{reason:'time-budget'}}),start,end);
assert.equal(partial.coverage.complete,false);assert.equal(partial.partial.reason,'time-budget');
const mismatch=reply();mismatch.aggregates.modelDaily=[];
assert.equal(narrow(mismatch,start,end).complete,false);
assert.throws(()=>narrow({...reply(),startDate:'2026-09-30'},start,end));
await assert.rejects(()=>collect(async()=>({...reply(),sessions:[{private:'never copied'}]}),start,end));
const empty=await collect(async()=>({startDate:start,endDate:end,sessions:[],totals:{totalTokens:0},aggregates:{byModel:[],modelDaily:[]}}),start,end);
assert.equal(empty.coverage.complete,true);assert.equal(empty.cacheStatus.status,'fresh');
console.log('OK: exact aggregate-only transport, coverage and replacement');
