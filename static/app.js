"use strict";
let state,id=0,mcpSession,ready=false,busy=false,lastFocus=null;
const $=id=>document.getElementById(id);
try{mcpSession=sessionStorage.getItem("resume-mcp-session");}catch{}
// Disabling a focused control drops focus to <body>; remember it and restore it when the action finishes.
function controls(){if(busy&&!lastFocus)lastFocus=document.activeElement;document.querySelectorAll("button, input, select").forEach(el=>el.disabled=busy||!ready);$("save-checkpoint").disabled=busy||!state;if(!busy&&lastFocus){if(!lastFocus.disabled)lastFocus.focus();lastFocus=null;}}
function rememberSession(value){mcpSession=value;try{if(value)sessionStorage.setItem("resume-mcp-session",value);else sessionStorage.removeItem("resume-mcp-session");}catch{}}
controls();
async function rpc(method,params={},notification=false){
 const payload={jsonrpc:"2.0",method,params};if(!notification)payload.id=++id;
 const headers={"Content-Type":"application/json","Accept":"application/json, text/event-stream","MCP-Protocol-Version":"2025-11-25"};if(mcpSession)headers["MCP-Session-Id"]=mcpSession;
 const r=await fetch("/mcp",{method:"POST",headers,body:JSON.stringify(payload)});
 if(r.status===404){rememberSession(null);ready=false;controls();throw Error("Local session expired or the server restarted. Download the displayed checkpoint if available, then reload to start a new session and restore it. The failed action was not retried.");}
 if(r.status===202)return null;
 const d=await r.json();if(!r.ok)throw Error(typeof d.error==="string"?d.error:d.error?.message||"Local MCP error.");if(d.error)throw Error(d.error.message);
 if(method==="initialize"&&r.headers.get("MCP-Session-Id"))rememberSession(r.headers.get("MCP-Session-Id"));return d.result;
}
async function call(name,args){
 const result=await rpc("tools/call",{name,arguments:args});
 if(result.isError)throw Error(result.content[0].text);
 state=JSON.parse(result.content[0].text);render();
}
function render(){
 const routine=state.routines[state.routine_id];
 $("routine-title").textContent=routine.name;
 $("progress").textContent=state.completed+" of 4 complete";
 $("mode").textContent=state.paused?"Paused":"Your pace";
 $("next").textContent=state.paused?"Your place is saved.":state.current===null?(state.completed===4?"All done.":"Some steps are set aside."):routine.steps[state.current];
 $("status").textContent=state.message;$("confirm").hidden=!state.pending_reset;
 $("steps").replaceChildren();routine.steps.forEach((title,i)=>{const li=document.createElement("li");li.dataset.status=state.steps[i];li.dataset.current=String(i===state.current);const d=document.createElement("div");d.textContent=title;const s=document.createElement("span");s.textContent=state.steps[i]==="done"?"Complete":state.steps[i]==="deferred"?"Set aside — not complete":i===state.current?"Current step":"Coming up";d.append(s);li.append(d);$("steps").append(li);});
 $("trace").textContent=JSON.stringify(state.trace,null,2);
 $("routine").value=state.routine_id;
}
async function turn(text){
 if(!ready||busy)return;
 $("error").textContent="";busy=true;controls();
 try{await call("routine_turn",{text,version:state.version});}
 catch(e){$("error").textContent=e.message;if(ready){try{await call("routine_state",{});}catch(refreshError){$("error").textContent=e.message+" "+refreshError.message;}}}
 finally{busy=false;controls();}
}
document.querySelectorAll("[data-command]").forEach(b=>b.onclick=()=>turn(b.dataset.command));
$("talk").onsubmit=e=>{e.preventDefault();const v=$("utterance").value;$("utterance").value="";turn(v);};
$("start").onclick=async()=>{if(!ready||busy)return;busy=true;controls();try{$("error").textContent="";await call("routine_start",{routine_id:$("routine").value,version:state.version,confirmed:$("replace").checked});$("replace").checked=false;}catch(e){$("error").textContent=e.message;}finally{busy=false;controls();}};
async function connect(){if(!mcpSession){await rpc("initialize",{protocolVersion:"2025-11-25",capabilities:{},clientInfo:{name:"resume-web",version:"0.1.1"}});}await rpc("notifications/initialized",{},true);}
// A stale stored session (server restarted) is replaced on page load; no user action is pending, so nothing is replayed.
(async()=>{try{const stored=Boolean(mcpSession);try{await connect();}catch(e){if(!stored||mcpSession)throw e;await connect();}const available=await rpc("tools/list");if(available.tools.length!==4)throw Error("Unexpected local tool list.");await call("routine_state",{});for(const [key,r]of Object.entries(state.routines)){const o=document.createElement("option");o.value=key;o.textContent=r.name;$("routine").append(o);}$("routine").value=state.routine_id;ready=true;}catch(e){$("error").textContent=e.message;}finally{controls();}})();


$("save-checkpoint").onclick=()=>{const a=document.createElement("a");a.href=URL.createObjectURL(new Blob([JSON.stringify(state.checkpoint,null,2)],{type:"application/json"}));a.download="resume-checkpoint.json";a.click();setTimeout(()=>URL.revokeObjectURL(a.href),1000);};
$("restore-checkpoint").onclick=async()=>{if(!ready||busy)return;busy=true;controls();try{$("error").textContent="";const file=$("checkpoint-file").files[0];if(!file||file.size>4096)throw Error("Choose a Resume checkpoint under 4 KB.");const checkpoint=JSON.parse(await file.text());await call("routine_restore",{checkpoint,version:state.version,confirmed:$("restore-confirm").checked});$("restore-confirm").checked=false;$("routine").value=state.routine_id;}catch(e){$("error").textContent=e.message;}finally{busy=false;controls();}};
