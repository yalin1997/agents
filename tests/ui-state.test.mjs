import {test} from 'node:test';
import assert from 'node:assert/strict';
import {WorkspaceState,groupToolCalls} from '../pi_deepagent/web/static/state.mjs';

test('logout rejects late private snapshots and clears drafts and requests',()=>{
  const state=new WorkspaceState(); const epoch=state.login({name:'admin'});
  state.draft('a','我的需求'); state.mutation('a/messages',{text:'我的需求'},()=> 'request-a');
  state.logout();
  assert.equal(state.accept({id:'a',revision:1},epoch),false);
  assert.equal(state.tasks.size,0); assert.equal(state.draft('a'),'');
  assert.equal(state.pending.size,0); assert.equal(state.identity,null);
});
test('unknown result keeps original request id and cannot silently replace payload',()=>{
  const state=new WorkspaceState(); state.login({name:'admin'});
  const body=state.mutation('a/messages',{text:'原需求'},()=> 'first');
  assert.deepEqual(body,{text:'原需求',request_id:'first'});
  assert.deepEqual(state.mutation('a/messages',{text:'原需求'},()=> 'second'),body);
  assert.throws(()=>state.mutation('a/messages',{text:'不同需求'},()=> 'third'),/確認/);
  state.resolved('a/messages');
  assert.equal(state.mutation('a/messages',{text:'不同需求'},()=> 'third').request_id,'third');
});
test('polling cannot overwrite a later revision or lose per-task drafts',()=>{
  const state=new WorkspaceState();const epoch=state.login({name:'admin'});
  state.draft('a','A');state.draft('b','B');
  assert.equal(state.accept({id:'a',revision:3,status:'completed'},epoch),true);
  assert.equal(state.accept({id:'a',revision:2,status:'running'},epoch),false);
  assert.equal(state.tasks.get('a').status,'completed');
  assert.equal(state.draft('a'),'A');assert.equal(state.draft('b'),'B');
});
test('a response from an earlier login cannot enter a new admin session',()=>{
  const state=new WorkspaceState();const old=state.login({name:'admin'});
  state.logout();const current=state.login({name:'admin'});
  assert.notEqual(old,current);assert.equal(state.accept({id:'old',revision:9},old),false);
  assert.equal(state.accept({id:'new',revision:0},current),true);
});

const start=(id,name='pi_read',run='r')=>({kind:'tool_start',call_id:id,name,run_id:run,input:'{"path":"a"}'});
const result=(id,content='',name='pi_read',run='r')=>({kind:'tool_result',call_id:id,name,run_id:run,content});
test('tool call identity joins out-of-order results and separates runs and repeated names',()=>{
  const calls=groupToolCalls([start('a'),start('b'),result('b','second'),result('a','first'),result('c','early'),start('c'),start('a','pi_read','r2')]);
  assert.equal(calls.length,4);assert.deepEqual(calls.map(c=>c.content),['first','second','early',undefined]);
  assert.equal(calls[2].started,true);assert.equal(calls[2].returned,true);assert.equal(calls[3].returned,false);
});
test('ID-less results only join a unique same-run same-name pending tool',()=>{
  assert.equal(groupToolCalls([start(undefined),result(undefined,'')]).length,1);
  const ambiguous=groupToolCalls([start(undefined),start(undefined),result(undefined,'unmatched')]);
  assert.equal(ambiguous.length,3);assert.equal(ambiguous.filter(c=>c.returned).length,1);
  assert.equal(groupToolCalls([start(undefined),result(undefined,'other','pi_read','r2')]).length,2);
  assert.equal(groupToolCalls([start(undefined),result(undefined,'other','pi_bash')]).length,2);
});
test('legacy tools retain missing values and structured tools retain errors and empty values',()=>{
  const calls=groupToolCalls([{text:'使用工具：custom'},{text:'工具已回傳：custom'},start('a'),{...result('a',''),is_error:true}]);
  assert.equal(calls.length,2);assert.equal(calls[0].content,undefined);assert.equal(calls[0].returned,true);
  assert.equal(calls[1].content,'');assert.equal(calls[1].isError,true);
});
