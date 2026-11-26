/* Real script, synthetic cache observations; no network or production values. */
const fs = require('fs'), vm = require('vm'), assert = require('assert');
class Element {
  constructor(tag) { this.tag=tag; this.children=[]; this.attrs={}; this.text=''; this.value='7'; this.listeners={}; }
  replaceChildren() { this.children=[]; this.text=''; }
  set textContent(v) { this.text=String(v); this.children=[]; }
  get textContent() { return this.text + this.children.map(c=>c.textContent).join(''); }
  addEventListener(type,fn) { this.listeners[type]=fn; }
}
const ids=['opencodex-chart','opencodex-models','opencodex-note','opencodex-days','refresh','history-source','history','model-table','opencodex-panel','h-opencodex'];
const roots=Object.fromEntries(ids.map(id=>[id,new Element('div')]));
roots['history-source'].value='opencodex';
roots['model-table'].closest=()=>roots['model-table'];
let fetches=[], rejectNext=false, resolvePending;
const agent={models:[{source:'openclaw',provider:'route-a',model:'EXACT_AGENT',totals:{total:8},daily:[{date:'2026-11-23',total:8}]},
                     {source:'hermes',provider:'route-b',model:'DO_NOT_MIX',totals:{total:99},daily:[]}],meta:{}};
const canonicalBytes=JSON.stringify(agent);
let canonicalRenders=0;
const context={
  periodStates:{7:{parts:{openclaw:agent}}},selectedDays:7,renderPeriod:()=>{canonicalRenders++;},
  $:id=>roots[id],tokens:v=>String(v),setInterval:()=>{},
  svgEl:(tag,attrs,parent)=>{const e=new Element(tag);e.attrs=attrs;parent.children.push(e);return e;},
  node:(tag,text,parent)=>{const e=new Element(tag);if(text!==undefined)e.textContent=text;parent.children.push(e);return e;},
  json:url=>{fetches.push(url);if(rejectNext)return Promise.reject(Error('offline'));return new Promise(resolve=>{resolvePending=resolve;});},
};
vm.createContext(context);
vm.runInContext(fs.readFileSync(process.argv[2],'utf8'),context);
const a={provider:'route-a',model:'<img onerror=bad>',account:null,total_tokens:100};
const b={provider:'route-b',model:'<img onerror=bad>',account:null,total_tokens:50};
const payload={status:'saved',start_date:'2026-11-22',end_date:'2026-11-23',models:[a,b],measurement_coverage:.98,
 estimated_cost_usd:1.234,daily:[{date:'2026-11-22',total_tokens:null,models:[]},{date:'2026-11-23',total_tokens:150,models:[a,b]}]};
const flatten=e=>[e].concat(e.children.flatMap(flatten));
const tick=()=>new Promise(resolve=>setImmediate(resolve));
(async()=>{
 resolvePending(payload);await tick();
 const chart=flatten(roots['opencodex-chart']);
 assert.equal(chart.filter(e=>e.tag==='rect').length,2,'one segment per exact provider/model; unknown is a gap');
 assert(chart.filter(e=>e.tag==='title').some(e=>e.text.includes('route-a')));
 assert(chart.filter(e=>e.tag==='title').some(e=>e.text.includes('route-b')));
 assert(roots['opencodex-models'].textContent.includes('Unknown (no model join)'));
 assert(roots['opencodex-models'].textContent.includes('<img onerror=bad>'));
 assert(!flatten(roots['opencodex-models']).some(e=>e.tag==='img'));
 assert(roots['opencodex-note'].textContent.includes('98.0%'));
 assert(roots['opencodex-note'].textContent.includes('not spend'));
 const original=roots['opencodex-models'].textContent;
 rejectNext=true;context.loadOpenCodex();await tick();
 assert.equal(roots['opencodex-models'].textContent,original,'offline keeps same-source graph/table');
 assert(roots['opencodex-note'].textContent.includes('retained'));
 roots['history-source'].value='openclaw';context.loadOpenCodex();
 assert(roots['opencodex-models'].textContent.includes('EXACT_AGENT'));
 assert(!roots['opencodex-models'].textContent.includes('DO_NOT_MIX'));
 assert(!roots['opencodex-models'].textContent.includes('<img'));
 roots['history-source'].value='opencodex';context.loadOpenCodex();await tick();
 assert.equal(roots['opencodex-models'].textContent,original,'never relabel cached agent observations as OpenCodex');
 roots['history-source'].value='hermes';context.loadOpenCodex();
 assert(!roots['opencodex-models'].textContent.includes('<img'),'no data = unknown, not another source');
 roots['history-source'].value='all';context.loadOpenCodex();
 assert.equal(roots['history'].hidden,false);assert.equal(roots['opencodex-panel'].hidden,true);
 context.renderPeriod({},{});assert.equal(canonicalRenders,1);
 assert.equal(JSON.stringify(agent),canonicalBytes,'canonical source inputs immutable');
 assert(fetches.every(url=>url.startsWith('/api/v1/opencodex?days=')),'cache-only endpoint');
 context.renderOpenCodex({status:'unavailable'});
 assert.equal(flatten(roots['opencodex-chart']).filter(e=>e.tag==='rect').length,0);
 assert(!roots['opencodex-note'].textContent.includes('$0'));
 assert(!roots['opencodex-note'].textContent.toLowerCase().includes('stale'));
 console.log('OpenCodex DOM/graph assertions passed');
})().catch(e=>{console.error(e);process.exitCode=1;});
