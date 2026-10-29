// Pure state shared by the browser and deterministic regression tests.
export class WorkspaceState {
  constructor(){this.epoch=0;this.identity=null;this.tasks=new Map();this.drafts=new Map();this.pending=new Map();}
  login(identity){this.logout();this.identity=identity;return this.epoch;}
  logout(){this.epoch++;this.identity=null;this.tasks.clear();this.drafts.clear();this.pending.clear();}
  draft(id,text){if(arguments.length===2)this.drafts.set(id,text);return this.drafts.get(id)||'';}
  accept(task,epoch){
    if(epoch!==this.epoch||!this.identity)return false;
    const previous=this.tasks.get(task.id);
    if(previous&&previous.revision>task.revision)return false;
    this.tasks.set(task.id,{...previous,...task,...(Array.isArray(task.messages)?{detailRevision:task.revision}:{})});return true;
  }
  mutation(key,payload,makeId=()=>crypto.randomUUID()){
    const previous=this.pending.get(key);
    if(previous){
      const {request_id,...body}=previous;
      if(JSON.stringify(body)!==JSON.stringify(payload))throw new Error('請先確認上一筆操作的狀態。');
      return previous;
    }
    const body={...payload,request_id:makeId()};this.pending.set(key,body);return body;
  }
  resolved(key){this.pending.delete(key);}
}

// Pair by identity, never by positional proximity when multiple calls are pending.
export function groupToolCalls(events, runId = '') {
  const calls=[], byId=new Map();
  for(const [index,event] of events.entries()) {
    const start=event.kind==='tool_start'||event.text?.startsWith('使用工具：');
    const result=event.kind==='tool_result'||event.text?.startsWith('工具已回傳：');
    if(!start&&!result)continue;
    const name=event.name||event.text?.replace(/^(使用工具：|工具已回傳：)/,'')||'工具';
    const run=event.run_id||runId;
    const identity=event.call_id?run+'/'+event.call_id:null;
    let call=identity?byId.get(identity):null;
    if(!call&&result&&!identity){
      const pending=calls.filter(c=>c.run===run&&c.name===name&&c.started&&!c.returned);
      if(pending.length===1)call=pending[0];
    }
    if(!call){call={key:identity||run+'/event/'+(event.sequence??index),run,name,started:false,returned:false};calls.push(call);if(identity)byId.set(identity,call);}
    if(start){call.started=true;call.input=event.input;}
    if(result){call.returned=true;call.content=event.content;call.isError=event.is_error;}
  }
  return calls;
}
