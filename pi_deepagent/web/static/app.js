import {WorkspaceState,groupToolCalls} from '/state.mjs';

const $=s=>document.querySelector(s);
const state=new WorkspaceState();
const paths={lock:'M6 10h12v11H6zM8 10V7a4 4 0 0 1 8 0v3',user:'M16 7a4 4 0 1 1-8 0 4 4 0 0 1 8 0ZM4 21v-2a8 8 0 0 1 16 0v2',logout:'M9 4H4v16h5M10 12h11m-4-4 4 4-4 4',close:'m6 6 12 12M6 18 18 6',plus:'M12 5v14M5 12h14',menu:'M4 6h16M4 12h16M4 18h16',chat:'M21 11.5a8.5 8.5 0 0 1-8.5 8.5H4l-1 1v-9.5A8.5 8.5 0 0 1 11.5 3h1a8.5 8.5 0 0 1 8.5 8.5Z',activity:'M3 12h4l3-8 4 16 3-8h4',file:'M14 2H5v20h14V7l-5-5ZM14 2v6h5M8 12h8M8 16h6',send:'M12 19V5M5 12l7-7 7 7',stop:'M6 6h12v12H6z',spark:'m12 3 2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5Z',check:'m5 12 4 4L19 6',retry:'M3 10a9 9 0 1 1 2 8M3 4v6h6',copy:'M9 9h12v12H9zM5 15H3V3h12v2',download:'M12 3v12m-5-5 5 5 5-5M4 17v4h16v-4'};
Object.assign(paths,{archive:'M3 3h18v5H3zM5 8v13h14V8M10 12h4',restore:'M12 16V9m-4 4 4-4 4 4M4 4h16v17H4',trash:'M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7M14 10v7',terminal:'m4 6 6 6-6 6M13 18h7',edit:'m4 16 12-12 4 4L8 20H4v-4M13 7l4 4',search:'M16 10a6 6 0 1 1-12 0 6 6 0 0 1 12 0Zm-1 5 6 6',tool:'M14 6a5 5 0 0 0-6 6L3 17l4 4 5-5a5 5 0 0 0 6-6l-3 3-4-4 3-3'});
function icon(name){return '<svg class="icon" viewBox="0 0 24 24" aria-hidden="true"><path d="'+paths[name]+'"/></svg>';}
document.querySelectorAll('[data-icon]').forEach(e=>e.innerHTML=icon(e.dataset.icon));
const statuses={fresh:'準備就緒',running:'進行中',stopping:'停止中',completed:'已完成',failed:'失敗',stopped:'已停止',interrupted:'已中斷'};
const statusIcons={fresh:'spark',running:'activity',stopping:'stop',completed:'check',failed:'retry',stopped:'stop',interrupted:'retry'};
let activeId=null,csrf='',connected=false,busy=false,polling=null,rendered='',loginReturn=null,logoutPending=null;
const recoveries=new Map();
const toolsOpen=new Map();
let archivedView=false,initialSelection=true;
const selectedTasks=new Set();
let purgePayload=null;
class StaleResponse extends Error{constructor(){super('回應已過期');this.name='StaleResponse';}}
function announce(text){if($('#feedback').textContent!==text)$('#feedback').textContent=text;}
function current(){return state.tasks.get(activeId);}
function closeMenu(){ $('#sidebar').classList.remove('open');$('#menu').setAttribute('aria-expanded','false'); }
function clearPrivate(){
  state.logout();activeId=null;csrf='';connected=false;busy=false;rendered='';recoveries.clear();toolsOpen.clear();selectedTasks.clear();archivedView=false;initialSelection=true;purgePayload=null;if($('#purge-dialog').open)$('#purge-dialog').close();closeArtifacts();
  $('#password').value='';$('#prompt').value='';$('#workspace-note').textContent='';closeMenu();
  if($('#retry-dialog').open)$('#retry-dialog').close();render();
  const target=$('#menu').getBoundingClientRect().width?$('#menu'):logoutPending?$('#title'):$('#login-open');target.focus();
}
async function api(path,{method='GET',body,epoch=state.epoch,silentUnauthorized=false}={}){
  try{
  const response=await fetch(path,{method,credentials:'same-origin',cache:'no-store',headers:method==='GET'?{}:{'Content-Type':'application/json','X-CSRF-Token':csrf},body:body===undefined?undefined:JSON.stringify(body)});
  if(epoch!==state.epoch)throw new StaleResponse();
  const data=await response.json();
  if(epoch!==state.epoch)throw new StaleResponse();
  if(!response.ok){
    const error=new Error(data.error||data.message||'操作未完成，請重新確認。');error.status=response.status;
    if(response.status===401&&path!=='/api/login'&&!silentUnauthorized){clearPrivate();announce('登入已失效，請重新登入。');}
    throw error;
  }
  return data;
  }catch(error){if(epoch!==state.epoch)throw new StaleResponse();throw error;}
}
function taskList(){
  const list=$('#task-list');
  if(!state.identity){list.replaceChildren();return;}
  const focusKey=document.activeElement?.dataset?.focusKey;
  list.replaceChildren();
  const visible=[...state.tasks.values()].filter(t=>!!t.archived===archivedView);
  for(const task of visible){
    const row=document.createElement('div');row.className='task-row';
    if(archivedView){
      const check=document.createElement('input');check.type='checkbox';check.checked=selectedTasks.has(task.id);check.disabled=busy;
      check.setAttribute('aria-label','選取 '+task.title);check.dataset.focusKey='select/'+task.id;
      check.onchange=()=>{check.checked?selectedTasks.add(task.id):selectedTasks.delete(task.id);taskList();};row.append(check);
    }
    const button=document.createElement('button');button.className='task';button.dataset.task=task.id;button.dataset.focusKey='task/'+task.id;
    const title=document.createElement('span'),status=document.createElement('small');title.textContent=task.title;status.textContent=statuses[task.status]||'處理中';button.append(title,status);
    button.setAttribute('aria-pressed',String(task.id===activeId));button.onclick=()=>selectTask(task.id);row.append(button);
    const action=document.createElement('button');action.className='icon-button';action.innerHTML=icon(archivedView?'restore':'archive');
    action.title=(archivedView?'還原 ':'封存 ')+task.title;action.setAttribute('aria-label',action.title);action.dataset.focusKey='archive/'+task.id;
    action.disabled=busy||!connected||['running','stopping'].includes(task.status);
    action.onclick=()=>mutate('/api/tasks/'+task.id+(archivedView?'/restore':'/archive'),{},archivedView?'restore':'archive');row.append(action);list.append(row);
  }
  $('#archive-controls').hidden=!archivedView;
  $('#select-all').disabled=busy||!visible.length;
  $('#select-all').textContent=visible.length&&visible.every(t=>selectedTasks.has(t.id))?'取消全選':'全選';
  $('#purge-open').textContent='永久刪除已選項目（'+selectedTasks.size+'）';
  $('#purge-open').disabled=busy||!connected||!selectedTasks.size;
  $('#view-active').setAttribute('aria-pressed',String(!archivedView));$('#view-archived').setAttribute('aria-pressed',String(archivedView));
  if(focusKey)list.querySelector('[data-focus-key="'+focusKey+'"]')?.focus({preventScroll:true});
}
function artifactNodes(task){
  const box=document.createElement('div');
  for(const artifact of task?.artifacts||[]){
    const card=document.createElement('div');card.className='artifact artifact-file';
    const info=document.createElement('div'),name=document.createElement('strong'),size=document.createElement('small');
    name.textContent=artifact.name;size.textContent=artifact.size+' B';info.append(name,size);
    const b=document.createElement('button');b.className='icon-button';b.title='下載 '+artifact.name;b.setAttribute('aria-label',b.title);b.dataset.focusKey='artifact/'+artifact.id;b.innerHTML=icon('download');b.onclick=()=>download(task.id,artifact);card.append(info,b);box.append(card);
  }
  return box;
}
function toolIcon(name){
  const type=name.replace(/^pi_/,'').toLowerCase();
  if(['bash','shell','exec','execute'].includes(type))return 'terminal';
  if(['read','read_file'].includes(type))return 'file';
  if(['write','write_file','edit','edit_file'].includes(type))return 'edit';
  if(['grep','glob','find','search','ls'].includes(type))return 'search';
  return 'tool';
}
function toolNodes(events,key,taskStatus){
  const box=document.createElement('div');box.className='tool-list';
  for(const call of groupToolCalls(events,key)){
    const focusKey=key+'/'+call.key;
    const details=document.createElement('details');details.className='tool-calls tool-card';details.open=toolsOpen.get(focusKey)||false;
    const summary=document.createElement('summary');summary.dataset.focusKey=focusKey;summary.innerHTML=icon(toolIcon(call.name));
    const name=document.createElement('strong');name.textContent=call.name;
    const preview=document.createElement('span');preview.className='tool-preview';preview.textContent=(call.input??'').slice(0,140);
    const status=document.createElement('span');status.className='tool-status';
    status.textContent=call.returned?(call.isError?'工具回傳錯誤':'已回傳'):['running','stopping'].includes(taskStatus)?'等待回傳':taskStatus==='failed'?'執行失敗，未回傳':taskStatus==='stopped'||taskStatus==='interrupted'?'已停止，未回傳':'未保存回傳';
    summary.append(name,preview,status);
    const body=document.createElement('div');body.className='tool-body';
    for(const [label,value] of [['輸入參數',call.input??'此紀錄未保存輸入參數'],['Return value',call.returned?(call.content===undefined?'此紀錄未保存回傳內容':call.content===''?'（空字串）':call.content):call.input===undefined&&!['running','stopping'].includes(taskStatus)?'此紀錄未保存回傳內容':'尚無回傳內容']]){
      const heading=document.createElement('div'),pre=document.createElement('pre');heading.textContent=label;pre.textContent=value;body.append(heading,pre);
    }
    details.append(summary,body);details.ontoggle=()=>toolsOpen.set(focusKey,details.open);box.append(details);
  }
  return box;
}
function updateContent(task){
  const signature=task?`${task.id}/${task.revision}/${task.detailRevision}`:'empty';if(rendered===signature)return;rendered=signature;
  const area=$('#content'),scroll=area.scrollTop;const wasBottom=area.scrollHeight-area.clientHeight-scroll<32;
  const focusKey=document.activeElement?.dataset?.focusKey;
  const fragment=document.createDocumentFragment();
  if(!task){const intro=document.createElement('div');intro.className='intro';intro.innerHTML=icon('spark')+'<h2>今天想完成什麼？</h2>';fragment.append(intro);}
  else if(!Array.isArray(task.messages)){const p=document.createElement('p');p.className='empty';p.textContent='載入中…';fragment.append(p);}
  else{
    if(!task.messages?.length){const intro=document.createElement('div');intro.className='intro';intro.innerHTML=icon('spark')+'<h2>今天想完成什麼？</h2>';fragment.append(intro);}
    const messages=task.messages||[];
    const lastAssistant=messages.findLastIndex(m=>m.role==='assistant');
    for(const [index,message] of messages.entries()){
      const article=document.createElement('article');article.className='message'+(message.role==='user'?' user':'');article.setAttribute('aria-label',message.role==='user'?'你的訊息':'Pi 的訊息');
      if(message.role!=='user'){
        const label=document.createElement('div');label.className='label';label.textContent='Pi';article.append(label);
        // Older saved tasks only have the last run's event list. Render it once.
        const events=message.tools??(index===lastAssistant?(task.events||[]).filter(e=>['tool_start','tool_result'].includes(e.kind)||/^(使用工具：|工具已回傳：)/.test(e.text)):[]);
        if(events.length)article.append(toolNodes(events,task.id+'/'+(message.run_id||index),message.complete?'completed':(index===lastAssistant&&(!message.run_id||message.run_id===task.run_id)?task.status:'interrupted')));
      }
      if(message.text){const p=document.createElement('p');p.className='message-text';p.textContent=message.text;article.append(p);}fragment.append(article);
    }
    if(['running','stopping'].includes(task.status)){const p=document.createElement('p');p.className='small';p.textContent=task.status==='stopping'?'停止中…':'處理中…';fragment.append(p);}
    if(task.error){
      const p=document.createElement('p');p.className='task-error';p.setAttribute('role','alert');p.textContent=task.error;fragment.append(p);
      if(task.diagnostic){
        const d=task.diagnostic,key='diagnostic/'+d.id;
        const details=document.createElement('details');details.className='tool-calls diagnostics';details.open=toolsOpen.get(key)||false;
        const summary=document.createElement('summary');summary.textContent='除錯資訊';summary.dataset.focusKey=key;
        const report=document.createElement('pre');
        const phases={initialization:'初始化',model:'模型回應',tool:'工具執行',after_tool:'工具回傳後',cleanup:'執行清理'};
        report.textContent=`錯誤編號：${d.id}\n時間：${d.time}\n執行編號：${d.run_id}\n階段：${phases[d.phase]||d.phase}\n最後工具：${d.tool||'無'}\n錯誤類型：${d.code}\n\n${d.message}\n\n${d.log_saved?'詳細日誌：服務資料目錄內的 diagnostics.log，以錯誤編號搜尋。':'本機日誌寫入失敗，請檢查資料目錄權限與磁碟空間。'}\n工具已回傳不代表成功；重試前請確認已執行的操作。`;
        details.append(summary,report);details.ontoggle=()=>toolsOpen.set(key,details.open);fragment.append(details);
      }else{const p=document.createElement('p');p.className='small';p.textContent='此舊紀錄未保留除錯資訊。更新並重啟服務後的新錯誤才會記錄。';fragment.append(p);}
    }
    if(['stopped','interrupted'].includes(task.status)){const p=document.createElement('p');p.className='small';p.textContent='已發生的檔案修改或命令不會撤回。請檢查結果再繼續。';fragment.append(p);}
  }
  area.replaceChildren(fragment);area.scrollTop=wasBottom?area.scrollHeight:scroll;
  $('#aside-result').replaceChildren(artifactNodes(task));
  if(focusKey){const region=focusKey.startsWith('artifact/')?$('#aside-result'):area;region.querySelector('[data-focus-key="'+focusKey+'"]')?.focus({preventScroll:true});}
}
function render(){
  const authenticated=!!state.identity,task=current(),hasArtifacts=authenticated&&!!task?.artifacts?.length;
  $('.app').classList.toggle('guest',!authenticated);$('.app').classList.toggle('has-artifacts',hasArtifacts);
  $('#artifacts-open').hidden=!hasArtifacts;$('#artifact-count').textContent=task?.artifacts?.length||'';
  if(!hasArtifacts)closeArtifacts();
  $('#login-open').classList.toggle('hidden',authenticated);$('#identity-row').classList.toggle('hidden',!authenticated);$('#login-open').disabled=!!logoutPending;
  $('#new').disabled=!authenticated||!connected||busy;taskList();
  $('#title').textContent=task?.title||'Pi Workspace';
  $('#badge').hidden=!task;
  if(task&&$('#badge').dataset.status!==task.status){$('#badge').innerHTML=icon(statusIcons[task.status]||'spark');$('#badge').append(document.createTextNode(' '+(statuses[task.status]||'處理中')));$('#badge').dataset.status=task.status;}
  if(!authenticated){$('#content').innerHTML='<div class="intro">'+icon('lock')+'<h2>登入以開始工作</h2></div>';$('#aside-result').replaceChildren();$('#prompt').disabled=true;$('#send').disabled=true;return;}
  const running=['running','stopping'].includes(task?.status),canRetry=['failed','interrupted'].includes(task?.status)&&!$('#prompt').value.trim();
  const label=running?'停止':canRetry?'重試':'送出';$('#send').innerHTML=icon(running?'stop':canRetry?'retry':'send');if(canRetry){const span=document.createElement('span');span.textContent='重試';$('#send').append(span);}
  $('#send').classList.toggle('retry-button',canRetry);$('#send').title=label;$('#send').setAttribute('aria-label',label);
  $('#send').disabled=!connected||busy||!task;$('#send').setAttribute('aria-disabled',String(task?.status==='stopping'));$('#prompt').disabled=running||!task||busy||!!task.archived;$('#send').disabled= !connected||busy||!task||!!task?.archived;
  $('#prompt').placeholder=task?.archived?'封存對話為唯讀，請先還原':task?'想完成什麼？':'先建立一個任務';
  updateContent(task);
}
async function selectTask(id){activeId=id;rendered='';$('#prompt').value=state.draft(id);closeMenu();closeArtifacts();announce('');render();$('#title').focus();await refresh();}
async function settle(key,operation,epoch,focus=true){
  const task=await api(operation.path,{method:'POST',body:operation.body,epoch});
  if(epoch!==state.epoch)throw new StaleResponse();
  state.resolved(key);recoveries.delete(key);
  if(operation.kind==='purge'){
    initialSelection=false;
    for(const id of task.deleted_ids){state.tasks.delete(id);state.drafts.delete(id);selectedTasks.delete(id);if(activeId===id){activeId=null;rendered='';$('#prompt').value='';}}
    purgePayload=null;$('#purge-dialog').close();
  }else state.accept(task,epoch);
  if(['archive','restore'].includes(operation.kind)){selectedTasks.delete(task.id);if(activeId===task.id){activeId=null;rendered='';$('#prompt').value='';}}
  if(operation.kind==='create'){archivedView=false;selectedTasks.clear();activeId=task.id;$('#prompt').value=state.draft(activeId);closeMenu();closeArtifacts();rendered='';}
  if(operation.kind==='message'&&state.draft(task.id).trim()===operation.body.text){state.draft(task.id,'');if(activeId===task.id)$('#prompt').value='';}
  connected=true;if(focus)busy=false;render();
  if(focus&&['archive','restore'].includes(operation.kind))$(archivedView?'#view-archived':'#view-active').focus();
  if(focus&&operation.kind==='purge')$('#title').focus();
  if(focus&&activeId===task.id){if(['message','retry','stop'].includes(operation.kind))$('#send').focus();else $('#prompt').focus();}
}
async function mutate(path,payload,kind){
  if(busy||!connected)return;
  const epoch=state.epoch;busy=true;
  try{
    const body=state.mutation(path,payload);const operation={path,body,kind};recoveries.set(path,operation);render();
    await settle(path,operation,epoch);announce(kind==='stop'?'正在停止…':kind==='purge'?'已永久刪除':kind==='archive'?'已移入封存區':kind==='restore'?'已還原對話':'已接收');
  }catch(error){
    if(error instanceof StaleResponse)return;
    if(error.status){if(!(kind==='purge'&&error.status===503)){state.resolved(path);recoveries.delete(path);}announce(error.message);}
    else{connected=false;announce('連線中斷，正在確認狀態。');}
  }finally{if(epoch===state.epoch){busy=false;render();}}
}
async function refresh(){
  if(!state.identity||polling===state.epoch||busy)return;
  const epoch=state.epoch;polling=epoch;let restoreId=null;
  try{
    const previousStatus=current()?.status,view=archivedView;
    const data=await api('/api/tasks'+(archivedView?'?archived=true':''),{epoch});
    if(view!==archivedView)return;
    const ids=new Set(data.tasks.map(t=>t.id));
    for(const [id,t] of state.tasks){if(!!t.archived===archivedView&&!ids.has(id)){state.tasks.delete(id);selectedTasks.delete(id);if(id===activeId){activeId=null;rendered='';$('#prompt').value='';}}}
    for(const task of data.tasks)state.accept(task,epoch);
    if(initialSelection&&data.tasks.length){if(!activeId){activeId=data.tasks[0].id;$('#prompt').value=state.draft(activeId);}initialSelection=false;}
    if(activeId){const id=activeId;const task=await api('/api/tasks/'+id,{epoch});state.accept(task,epoch);
      if(id===activeId&&previousStatus!==task.status){announce(statuses[task.status]||'狀態已更新');if(!['running','stopping'].includes(task.status)&&document.activeElement===$('#send'))restoreId=id;}}
    connected=true;
    // Only replay the exact request id/body: the server ledger makes acceptance unambiguous.
    for(const [key,operation] of recoveries){try{await settle(key,operation,epoch,false);}catch(error){if(error.status&&error.status!==401){if(!(operation.kind==='purge'&&error.status===503)){state.resolved(key);recoveries.delete(key);}announce(error.message);}else throw error;}}
    render();
    if(restoreId===activeId&&document.activeElement===$('#send'))$('#prompt').focus({preventScroll:true});
  }catch(error){if(!(error instanceof StaleResponse)&&state.identity){connected=false;announce('連線中斷，正在確認狀態。');render();}}
  finally{if(polling===epoch)polling=null;}
}
async function download(id,artifact){
  const epoch=state.epoch;
  try{const response=await fetch('/api/tasks/'+id+'/artifacts/'+artifact.id,{credentials:'same-origin',cache:'no-store'});if(epoch!==state.epoch)return;if(response.status===401){clearPrivate();announce('登入已失效，請重新登入。');return;}if(!response.ok)throw new Error('無法下載此成果，請重新整理。');const blob=await response.blob();if(epoch!==state.epoch)return;const url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download=artifact.name.split('/').pop();a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}catch(error){if(epoch===state.epoch)announce(error.message);}
}
$('#prompt').oninput=()=>{if(activeId){state.draft(activeId,$('#prompt').value);render();}};
$('#prompt').onkeydown=e=>{
  if(e.key!=='Enter'||e.shiftKey||e.isComposing||e.keyCode===229||$('#prompt').disabled)return;
  e.preventDefault();if(!e.repeat)$('#send').click();
};
async function switchArchive(value){if(busy)return;archivedView=value;initialSelection=true;selectedTasks.clear();activeId=null;rendered='';$('#prompt').value='';render();await refresh();}
$('#view-active').onclick=()=>switchArchive(false);
$('#view-archived').onclick=()=>switchArchive(true);
$('#select-all').onclick=()=>{const tasks=[...state.tasks.values()].filter(t=>!!t.archived===archivedView);const all=tasks.every(t=>selectedTasks.has(t.id));for(const t of tasks)all?selectedTasks.delete(t.id):selectedTasks.add(t.id);taskList();};
$('#purge-open').onclick=()=>{if(!selectedTasks.size)return;purgePayload={task_ids:[...selectedTasks].sort()};$('#purge-note').textContent='將永久刪除 '+selectedTasks.size+' 筆對話及其成果下載快照，無法復原。工作目錄實際檔案會保留。';$('#purge-dialog').showModal();$('#purge-cancel').focus();};
$('#purge-cancel').onclick=()=>$('#purge-dialog').close();
$('#purge-confirm').onclick=()=>{if(purgePayload){$('#purge-dialog').close();mutate('/api/tasks/purge',purgePayload,'purge');}};
$('#purge-dialog').onclose=()=>{if(state.identity)$('#purge-open').focus();};
$('#new').onclick=()=>mutate('/api/tasks',{},'create');
$('#send').onclick=()=>{
  const task=current();if(!task||task.archived||task.status==='stopping'||busy||!connected)return;
  if(task.status==='running'){mutate('/api/tasks/'+task.id+'/stop',{expected_run_id:task.run_id},'stop');return;}
  const text=$('#prompt').value.trim();if(text){mutate('/api/tasks/'+task.id+'/messages',{text},'message');return;}
  if(['failed','interrupted'].includes(task.status)){$('#retry-dialog').dataset.task=task.id;$('#retry-dialog').dataset.run=task.run_id;$('#retry-dialog').showModal();$('#retry-cancel').focus();}
  else{announce('請輸入需求。');$('#prompt').focus();}
};
function closeArtifacts(){ $('.app').classList.remove('artifacts-open');$('#artifacts-open').setAttribute('aria-expanded','false'); }
$('#artifacts-open').onclick=()=>{const open=$('.app').classList.toggle('artifacts-open');$('#artifacts-open').setAttribute('aria-expanded',String(open));if(open)$('#artifact-close').focus();};
$('#artifact-close').onclick=()=>{closeArtifacts();$('#artifacts-open').focus();};
$('#artifact-panel').onkeydown=e=>{if(e.key==='Escape'){closeArtifacts();$('#artifacts-open').focus();}};
$('#menu').onclick=()=>{const open=$('#sidebar').classList.toggle('open');$('#menu').setAttribute('aria-expanded',String(open));if(open){const target=state.identity?($('#task-list').querySelector('[aria-pressed="true"]')||$('#new')):$('#login-open');target.focus();}};
$('#sidebar').onkeydown=e=>{if(e.key==='Escape') {closeMenu();$('#menu').focus();}};
$('#login-open').onclick=()=>{loginReturn=document.activeElement;$('#password').value='';$('#password').removeAttribute('aria-invalid');$('#login-error').textContent='';$('#login-dialog').showModal();$('#password').focus();};
$('#login-close').onclick=()=>$('#login-dialog').close();
$('#login-dialog').onclose=()=>{$('#password').value='';if(!state.identity)loginReturn?.focus();};
$('#login-form').onsubmit=async e=>{
  e.preventDefault();const button=e.submitter||$('#login-form button[type=submit]');if(button.disabled)return;button.disabled=true;const epoch=state.epoch;
  try{const data=await api('/api/login',{method:'POST',body:{username:'admin',password:$('#password').value},epoch});state.login(data.identity);csrf=data.csrf;connected=true;$('#password').value='';$('#login-dialog').close();$('#workspace-note').textContent=data.workspace||'';closeMenu();announce('');render();$('#title').focus();await refresh();}
  catch(error){if(!(error instanceof StaleResponse)){$('#login-error').textContent=error.message;$('#password').setAttribute('aria-invalid','true');$('#password').focus();}}
  finally{button.disabled=false;}
};
async function revokeSession(){
  if(!logoutPending)return;
  try{const response=await fetch('/api/logout',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json','X-CSRF-Token':logoutPending},body:'{}'});if(!response.ok&&response.status!==401)throw new Error();logoutPending=null;render();announce('已登出');if(document.activeElement===$('#title'))($('#menu').getBoundingClientRect().width?$('#menu'):$('#login-open')).focus();}
  catch{announce('登出尚未確認，連線恢復後會再試。');}
}
$('#logout').onclick=()=>{logoutPending=csrf;clearPrivate();revokeSession();};
$('#retry-cancel').onclick=()=>$('#retry-dialog').close();
$('#retry-confirm').onclick=()=>{const dialog=$('#retry-dialog'),id=dialog.dataset.task,run=dialog.dataset.run;dialog.close();mutate('/api/tasks/'+id+'/retry',{expected_run_id:run},'retry');};
$('#retry-dialog').onclose=()=>{if(state.identity)$('#send').focus();};
async function tick(){if(logoutPending)await revokeSession();else await refresh();setTimeout(tick,document.hidden||!navigator.onLine?5000:1000);}
async function boot(){render();try{const data=await api('/api/session',{silentUnauthorized:true});state.login(data.identity);csrf=data.csrf;connected=true;$('#workspace-note').textContent=data.workspace||'';render();await refresh();}catch(error){if(!(error instanceof StaleResponse)&&error.status!==401)announce('無法連線，請確認工作台服務已啟動。');}tick();}
boot();
