import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import {WorkspaceState,groupToolCalls} from '../pi_deepagent/web/static/state.mjs';

function fixture(fetch){
  const nodes=new Map();let document,uuidSequence=0;
  class Element {
    constructor(tag='div'){this.tagName=tag;this.children=[];this.dataset={};this.attributes={};this.textContent='';this.value='';this.disabled=false;this.open=false;this.scrollTop=0;this.scrollHeight=200;this.clientHeight=200;this.classes=new Set();this.classList={toggle:(n,on)=>{if(on===undefined)on=!this.classes.has(n);on?this.classes.add(n):this.classes.delete(n);return on;},remove:n=>this.classes.delete(n),contains:n=>this.classes.has(n)};}
    append(...nodes){this.children.push(...nodes);} replaceChildren(...nodes){this.children=[...nodes];}
    set innerHTML(value){this.html=value;this.htmlWrites=(this.htmlWrites||0)+1;this.children=[];}get innerHTML(){return this.html||'';}
    get firstChild(){return this.children[0];}get lastChild(){return this.children.at(-1);}
    setAttribute(k,v){this.attributes[k]=v;} removeAttribute(k){delete this.attributes[k];}
    getAttribute(key){return this.attributes[key]??null;}
    contains(node){return this===node||this.children.some(n=>n.contains?.(node));}
    querySelector(selector){const task=selector.match(/data-task="(.*?)"/)?.[1],label=selector.match(/aria-label="(.*?)"/)?.[1],focus=selector.match(/data-focus-key="(.*?)"/)?.[1];for(const child of this.children){if((task&&child.dataset?.task===task)||(label&&child.attributes?.['aria-label']===label)||(focus&&child.dataset?.focusKey===focus))return child;const nested=child.querySelector?.(selector);if(nested)return nested;}return null;}
    focus(options){this.focusOptions=options;if(!this.disabled)document.activeElement=this;}
    click(){if(!this.disabled)this.onclick?.();}
    showModal(){this.open=true;}close(){this.open=false;this.onclose?.();}
    getBoundingClientRect(){return {width:0};}
  }
  document={activeElement:null,hidden:false,querySelector(selector){if(!nodes.has(selector))nodes.set(selector,new Element());return nodes.get(selector);},querySelectorAll(){return [];},createElement:t=>new Element(t),createDocumentFragment:()=>new Element(),createTextNode:t=>({textContent:t})};
  const context=vm.createContext({WorkspaceState,groupToolCalls,document,fetch,crypto:{randomUUID:()=> 'aaaaaaaa-aaaa-4aaa-aaaa-'+String(++uuidSequence).padStart(12,'0')},navigator:{onLine:true},setTimeout:()=>0,console,URL});
  const source=readFileSync(new URL('../pi_deepagent/web/static/app.js',import.meta.url),'utf8').replace(/^import .*?;\s*/,'').replace(/boot\(\);\s*$/,'');
  vm.runInContext(source+`;globalThis.hooks={state,api,refresh,mutate,clearPrivate,render,configure(id){activeId=id;connected=true;csrf='test-csrf';},getBusy:()=>busy};`,context);
  return {h:context.hooks,document,node:s=>document.querySelector(s)};
}
const reply=(data,status=200)=>({ok:status<400,status,json:async()=>data});
const task=(status,revision)=>({id:'a',title:'任務',status,revision,run_id:'run-a',messages:[],events:[],output:'',error:null});

test('delayed JSON body from old session cannot revoke a newer login',async()=>{
  let resolveBody,started;const ready=new Promise(r=>started=r);
  const {h}=fixture(async()=>({ok:false,status:401,json:()=>{started();return new Promise(r=>resolveBody=r);}}));
  h.state.login({name:'old'});const request=h.api('/api/tasks');await ready;
  h.clearPrivate();h.state.login({name:'new'});resolveBody({error:'expired'});
  await assert.rejects(request,{name:'StaleResponse'});
  assert.equal(h.state.identity.name,'new');
});
test('completed poll restores prompt focus after it becomes enabled',async()=>{
  const completed=task('completed',2);let call=0;
  const {h,node,document}=fixture(async()=>reply(++call===1?{tasks:[completed]}:completed));
  const epoch=h.state.login({name:'admin'});h.state.accept(task('running',1),epoch);h.configure('a');h.render();
  node('#send').focus();assert.equal(node('#prompt').disabled,true);
  await h.refresh();
  assert.equal(node('#prompt').disabled,false);assert.equal(document.activeElement,node('#prompt'));
  assert.equal(node('#prompt').focusOptions?.preventScroll,true);
});
test('lost acceptance replays same request, preserves changed draft and later revision',async()=>{
  const sent=[];let phase=0;const complete=task('completed',4);
  const {h,node}=fixture(async(path,options)=>{
    if(options.method==='POST'){sent.push(JSON.parse(options.body));if(phase++===0)throw new TypeError('offline');return reply(task('running',2),202);}
    return reply(path==='/api/tasks'?{tasks:[complete]}:complete);
  });
  const epoch=h.state.login({name:'admin'});h.state.accept(task('fresh',1),epoch);h.configure('a');h.state.draft('a','原需求');node('#prompt').value='原需求';
  await h.mutate('/api/tasks/a/messages',{text:'原需求'},'message');
  h.state.draft('a','下一個草稿');node('#prompt').value='下一個草稿';
  await h.refresh();
  assert.equal(sent.length,2);assert.deepEqual(sent[1],sent[0]);
  assert.equal(h.state.tasks.get('a').status,'completed');assert.equal(node('#prompt').value,'下一個草稿');
  assert.equal(h.state.pending.size,0);assert.equal(h.getBusy(),false);
});
test('unchanged status is not rewritten while typing or polling',()=>{
  const {h,node}=fixture();const epoch=h.state.login({name:'admin'});h.state.accept(task('running',1),epoch);h.configure('a');h.render();const count=node('#badge').htmlWrites;
  h.render();h.render();assert.equal(node('#badge').htmlWrites,count);
});
test('artifact refresh preserves the focused download action',()=>{
  const {h,node,document}=fixture();const epoch=h.state.login({name:'admin'});
  const artifacts=[{id:'file-a',name:'report.md',size:12}];
  h.state.accept({...task('completed',1),artifacts},epoch);h.configure('a');h.render();
  node('#aside-result').querySelector('[aria-label="下載 report.md"]').focus();
  h.state.accept({...task('completed',2),artifacts},epoch);h.render();
  assert.equal(document.activeElement,node('#aside-result').querySelector('[aria-label="下載 report.md"]'));
});
test('late rejected fetch cannot disconnect a new session',async()=>{
  let rejectFetch,started;const ready=new Promise(resolve=>started=resolve);
  const {h,node}=fixture(()=>{started();return new Promise((resolve,reject)=>rejectFetch=reject);});
  let epoch=h.state.login({name:'old'});h.state.accept(task('fresh',1),epoch);h.configure('a');
  const pending=h.refresh();await ready;
  h.clearPrivate();epoch=h.state.login({name:'new'});h.state.accept(task('fresh',1),epoch);h.configure('a');h.render();
  assert.equal(node('#send').disabled,false);rejectFetch(new TypeError('old network failure'));await pending;
  assert.equal(node('#send').disabled,false);assert.equal(h.state.identity.name,'new');
});
test('stopping retains a keyboard focus target until terminal poll',async()=>{
  let phase=0;const stopped=task('stopped',3);
  const {h,node,document}=fixture(async(path,options)=>reply(options.method==='POST'?task('stopping',2):++phase===1?{tasks:[stopped]}:stopped));
  const epoch=h.state.login({name:'admin'});h.state.accept(task('running',1),epoch);h.configure('a');h.render();node('#send').focus();
  await h.mutate('/api/tasks/a/stop',{expected_run_id:'run-a'},'stop');
  assert.equal(node('#send').disabled,false);assert.equal(document.activeElement,node('#send'));assert.equal(node('#send').getAttribute('aria-disabled'),'true');
  await h.refresh();assert.equal(document.activeElement,node('#prompt'));
});
test('opening a summary loads full conversation even when revision is unchanged',async()=>{
  const detail={...task('completed',5),messages:[{role:'assistant',text:'已保存的實際回覆'}]};
  const summary={id:'a',title:'任務',status:'completed',revision:5};let call=0;
  const {h,node}=fixture(async()=>reply(++call===1?{tasks:[summary]}:detail));
  const epoch=h.state.login({name:'admin'});h.state.accept(summary,epoch);h.configure('a');h.render();
  await h.refresh();
  function allText(element){return [element.textContent,...(element.children||[]).map(allText)].join(' ');}
  assert.match(allText(node('#content')),/已保存的實際回覆/);
});
test('Enter sends, Shift Enter adds a line, and IME or held keys never send',()=>{
  const {node}=fixture();let sent=0,prevented=0;node('#send').onclick=()=>sent++;
  const key=(options)=>node('#prompt').onkeydown({key:'Enter',preventDefault:()=>prevented++,...options});
  key({isComposing:true});key({keyCode:229});key({shiftKey:true});key({key:'a'});
  assert.equal(sent,0);assert.equal(prevented,0);
  key({});key({ctrlKey:true});key({metaKey:true});assert.equal(sent,3);assert.equal(prevented,3);
  key({repeat:true});assert.equal(sent,3);assert.equal(prevented,4);
  node('#prompt').disabled=true;key({});assert.equal(sent,3);
});

test('conversation updates follow the bottom but preserve the position when reading history',()=>{
  const {h,node}=fixture();const epoch=h.state.login({name:'admin'});h.configure('a');
  const area=node('#content');area.scrollHeight=1200;area.clientHeight=400;area.scrollTop=780;
  h.state.accept(task('running',1),epoch);h.render();assert.equal(area.scrollTop,1200);
  area.scrollTop=120;h.state.accept(task('running',2),epoch);h.render();assert.equal(area.scrollTop,120);
});

const allText=element=>[element.textContent,...(element.children||[]).map(allText)].join(' ');
const descendants=element=>[element,...(element.children||[]).flatMap(descendants)];

test('failure diagnostics are collapsed, selectable text and retain focus across polls',()=>{
  const {h,node,document}=fixture();const epoch=h.state.login({name:'admin'});h.configure('a');
  const diagnostic={id:'error-a',time:'2026-10-26',run_id:'run-a',phase:'after_tool',tool:'pi_bash',code:'model_error',message:'HTTP 400 <script>not markup</script>',log_saved:true};
  h.state.accept({...task('failed',1),error:'執行失敗',diagnostic},epoch);h.render();
  let details=descendants(node('#content')).find(n=>n.className==='tool-calls diagnostics');
  assert.equal(details.open,false);assert.match(allText(details),/error-a/);assert.match(allText(details),/工具回傳後/);
  assert.match(allText(details),/diagnostics.log/);assert.match(allText(details),/<script>not markup<\/script>/);
  assert.equal(descendants(details).some(n=>n.tagName==='script'),false);
  details.open=true;details.ontoggle();details.firstChild.focus();
  h.state.accept({...task('failed',2),error:'執行失敗',diagnostic},epoch);h.render();
  details=descendants(node('#content')).find(n=>n.className==='tool-calls diagnostics');
  assert.equal(details.open,true);assert.equal(document.activeElement,details.firstChild);
  h.state.accept({...task('failed',3),error:'執行失敗',diagnostic:{...diagnostic,log_saved:false}},epoch);h.render();
  assert.match(allText(node('#content')),/日誌寫入失敗/);
  h.clearPrivate();h.render();assert.doesNotMatch(allText(node('#content')),/error-a|invalid tool/);
});

test('chat text appears only in conversation; artifact cards contain only file metadata',()=>{
  const {h,node}=fixture();const epoch=h.state.login({name:'admin'});h.configure('a');
  h.state.accept({...task('completed',1),output:'unique answer',messages:[{role:'assistant',text:'unique answer'}]},epoch);h.render();
  assert.equal(allText(node('#content')).split('unique answer').length-1,1);
  assert.equal(allText(node('#aside-result')).includes('unique answer'),false);
  assert.equal(node('.app').classList.contains('has-artifacts'),false);
  h.state.accept({...task('completed',2),output:'unique answer',messages:[{role:'assistant',text:'unique answer'}],artifacts:[{id:'file-a',name:'report.md',size:42}]},epoch);h.render();
  assert.equal(node('.app').classList.contains('has-artifacts'),true);
  assert.match(allText(node('#aside-result')),/report\.md/);
  assert.doesNotMatch(allText(node('#aside-result')),/unique answer/);
  assert.doesNotMatch(allText(node('#content')),/report\.md/);
});

test('tool calls appear once in their turn; expansion and keyboard focus survive updates',()=>{
  const {h,node,document}=fixture();const epoch=h.state.login({name:'admin'});h.configure('a');
  const tools=[{kind:'tool_start',text:'使用工具：pi_read'},{kind:'tool_result',text:'工具已回傳：pi_read'}];
  const messages=[{role:'user',text:'read'},{role:'assistant',run_id:'run-a',text:'answer',tools}];
  h.state.accept({...task('running',1),messages,events:tools},epoch);h.render();
  let details=descendants(node('#content')).find(n=>n.tagName==='details');
  assert.equal(details.open,false);assert.equal(allText(node('#content')).split('pi_read').length-1,1);
  details.open=true;details.ontoggle();details.firstChild.focus();
  h.state.accept({...task('completed',2),messages,events:tools},epoch);h.render();
  details=descendants(node('#content')).find(n=>n.tagName==='details');
  assert.equal(details.open,true);assert.equal(document.activeElement,details.firstChild);
  h.clearPrivate();h.state.login({name:'admin'});h.configure('a');h.state.accept({...task('completed',2),messages,events:tools},h.state.epoch);h.render();
  assert.equal(descendants(node('#content')).find(n=>n.tagName==='details').open,false);
});

test('older saved tool logs still appear only once without a separate activity view',()=>{
  const {h,node}=fixture();const epoch=h.state.login({name:'admin'});h.configure('a');
  h.state.accept({...task('completed',1),messages:[{role:'assistant',text:'old answer'}],events:[{text:'開始執行'},{text:'使用工具：pi_read'}]},epoch);h.render();
  assert.equal(allText(node('#content')).split('pi_read').length-1,1);
  assert.doesNotMatch(allText(node('#content')),/開始執行/);
  const html=readFileSync(new URL('../pi_deepagent/web/static/index.html',import.meta.url),'utf8');
  assert.doesNotMatch(html,/data-view=/);assert.match(html,/conversation-heading/);
});

test('tool card uses matching icons, shows safe results and updates the original card',()=>{
  const {h,node}=fixture();const epoch=h.state.login({name:'admin'});h.configure('a');
  const start={kind:'tool_start',name:'pi_bash',call_id:'c',run_id:'r',input:'{"command":"pwd"}'};
  const messages=tools=>[{role:'assistant',run_id:'r',text:'',tools}];
  h.state.accept({...task('running',1),run_id:'r',messages:messages([start])},epoch);h.render();
  assert.equal(descendants(node('#content')).filter(n=>n.tagName==='details').length,1);
  assert.match(allText(node('#content')),/等待回傳/);
  const result={kind:'tool_result',name:'pi_bash',call_id:'c',run_id:'r',content:'<script>plain text</script>'};
  h.state.accept({...task('completed',2),run_id:'r',messages:messages([start,result])},epoch);h.render();
  const cards=descendants(node('#content')).filter(n=>n.tagName==='details');
  assert.equal(cards.length,1);assert.match(allText(cards[0]),/<script>plain text<\/script>/);
  assert.equal(descendants(cards[0]).some(n=>n.tagName==='script'),false);
  assert.match(cards[0].firstChild.innerHTML,/m4 6 6 6/);
});
test('archive controls select all, cancel selection and confirm the explicit purge set',async()=>{
  const sent=[];const archived=[{...task('completed',1),id:'a',archived:true},{...task('completed',1),id:'b',archived:true}];
  const {h,node}=fixture(async(path,options)=>{
    if(options.method==='POST'){sent.push(JSON.parse(options.body));return reply({deleted_ids:['a','b']});}
    return reply(path.includes('?')?{tasks:archived}:archived[0]);
  });
  h.state.login({name:'admin'});h.configure('a');await node('#view-archived').onclick();
  node('#select-all').onclick();assert.match(node('#purge-open').textContent,/2/);
  node('#select-all').onclick();assert.match(node('#purge-open').textContent,/0/);
  node('#select-all').onclick();node('#purge-open').onclick();assert.equal(node('#purge-dialog').open,true);
  node('#purge-cancel').onclick();assert.equal(sent.length,0);
  node('#purge-open').onclick();node('#purge-confirm').onclick();
  await new Promise(resolve=>setImmediate(resolve));
  assert.deepEqual(sent[0].task_ids,['a','b']);assert.equal(h.state.tasks.size,0);assert.equal(node('#prompt').value,'');
});
test('archived sessions disable sending and preserve selection on a lost purge response',async()=>{
  const archived={...task('completed',1),archived:true};
  const {h,node}=fixture(async(path,options)=>{if(options.method==='POST')throw new TypeError('offline');return reply(path.includes('?')?{tasks:[archived]}:archived);});
  h.state.login({name:'admin'});h.configure('a');await node('#view-archived').onclick();
  assert.equal(node('#prompt').disabled,true);assert.equal(node('#send').disabled,true);
  node('#select-all').onclick();node('#purge-open').onclick();node('#purge-confirm').onclick();
  await new Promise(resolve=>setImmediate(resolve));
  assert.match(node('#purge-open').textContent,/1/);assert.equal(h.state.pending.size,1);
});
