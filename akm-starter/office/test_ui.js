// Offline UI tests. Run with Node.js: node test_ui.js
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const html = fs.readFileSync(__dirname + '/AKM_Trading_Office.html', 'utf8');
const script = html.match(/<script>([\s\S]*?)<\/script>/)[1];
const element = () => ({textContent:'', children:[], listeners:{}, appendChild(x){this.children.push(x)}, replaceChildren(){this.children=[]}, addEventListener(n,f){this.listeners[n]=f}});
const els = Object.fromEntries(['status','quotes','refresh','fed-status','fed-items','fed-refresh'].map(id => [id,element()]));
const events = {}, requests = [], timers = new Map(), intervals=[]; let timerId=0;
const document = {hidden:false,getElementById:id=>els[id],createElement:element,addEventListener:(n,f)=>events[n]=f};
const context = {document,window:{addEventListener(){}},AbortController,Date,Number,Error,URL,console,
 setTimeout:(f,ms)=>{const id=++timerId;timers.set(id,{f,ms});return id},clearTimeout:id=>timers.delete(id),setInterval:(f,ms)=>{intervals.push({f,ms});return 0},
 fetch:(url,opts)=>new Promise((resolve,reject)=>{requests.push({url,resolve,reject,signal:opts.signal});opts.signal.addEventListener('abort',()=>reject(Error('aborted')))})};
vm.createContext(context);vm.runInContext(script,context);
const settle = ()=>new Promise(resolve=>setImmediate(resolve));
const response = (tick=Date.now())=>({ok:true,json:async()=>({message:'connected',quotes:[{market:'XAUUSD',state:'fresh',bid:10,ask:11,tick_time_utc:new Date(tick).toISOString()}]})});
const fedResponse = data => ({ok:true,json:async()=>data});
const qs = () => requests.filter(r=>r.url==='/quotes');
const fsReq = () => requests.filter(r=>r.url==='/fed-headlines');
(async()=>{
  assert.equal(qs().length,1);assert.equal(fsReq().length,1);
  fsReq()[0].resolve(fedResponse({feed_time:'feed date',checked_at_utc:'check time',error:null,items:[
    {title:'<img onerror=alert(1)>',link:'https://www.federalreserve.gov/test',published:'item date'},
    {title:'bad',link:'https://federalreserve.gov.evil.test/a'},{title:'bad',link:'javascript:alert(1)'}]}));
  qs()[0].resolve(response()); await settle();
  assert.equal(els['fed-items'].children.length,1);
  assert.equal(els['fed-items'].children[0].children[0].textContent,'<img onerror=alert(1)>');
  assert.equal(els['fed-items'].children[0].children[0].href,'https://www.federalreserve.gov/test');
  assert.match(els['fed-status'].textContent,/feed date.*check time/);
  assert.match(els['fed-items'].children[0].children[1].textContent,/item date/);
  assert.ok(intervals.some(i=>i.ms===900000));
  assert.equal(els.quotes.children.length,1); assert.equal(els.quotes.children[0].children[2].textContent,10);
  els.refresh.listeners.click(); assert.equal(els.quotes.children.length,0);
  qs()[1].resolve(response(Date.now()-121000)); await settle();
  assert.equal(els.quotes.children[0].children[2].textContent,'—');
  els.refresh.listeners.click(); const old=qs()[2]; els.refresh.listeners.click();
  assert.equal(old.signal.aborted,true); old.resolve(response()); qs()[3].resolve(response()); await settle();
  assert.equal(els.quotes.children.length,1);
  document.hidden=true;events.visibilitychange();assert.equal(els.quotes.children.length,0);
  document.hidden=false;events.visibilitychange(); assert.equal(qs().length,5);
  const timeout=[...timers.values()].find(t=>t.ms===5000);assert.ok(timeout);timeout.f();await settle();
  assert.equal(els.quotes.children.length,0);assert.match(els.status.textContent,/timed out/);
  els['fed-refresh'].listeners.click(); fsReq()[1].resolve(fedResponse({error:'Feed unavailable',checked_at_utc:'now',items:[]}));await settle();
  assert.match(els['fed-status'].textContent,/Feed unavailable.*not provided.*now/);
  assert.equal(els['fed-items'].children.length,0);
  els['fed-refresh'].listeners.click();const prior=fsReq()[2];els['fed-refresh'].listeners.click();assert.ok(prior.signal.aborted);
  const fedTimeout=[...timers.values()].filter(t=>t.ms===10000).at(-1);assert.ok(fedTimeout);fedTimeout.f();await settle();
  assert.match(els['fed-status'].textContent,/unavailable or timed out/);
  console.log('UI tests passed: quotes, Fed rendering/escaping, links, timing and errors.');
})().catch(e=>{console.error(e);process.exitCode=1});
