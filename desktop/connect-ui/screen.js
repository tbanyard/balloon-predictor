"use strict";
const $=id=>document.getElementById(id), fields=["mode","address","host","username","ssh_port","web_port","allow_http"];
const field=key=>$(key.replaceAll("_","-"));
const message=(text,error=false)=>{$("status").textContent=text;$("status").classList.toggle("error",error);};
const form=$("connection"),connect=$("connect"),resume=$("resume"),splash=$("splash");
let challenge=null,initialized=false,splashDone=false,waiting=false,busy=false,controllerReady=false;
function updateActions(){
  const ready=initialized&&splashDone&&controllerReady;
  form.dataset.ready=String(ready);
  connect.disabled=resume.disabled=!ready||busy||waiting;
}
function mode(){
  const value=$("mode").value;
  $("web").hidden=value!=="web";$("ssh").hidden=value!=="ssh";
  $("address").disabled=value!=="web";
  for(const key of ["ssh-port","web-port"])$(key).disabled=value!=="ssh";
  connect.textContent=value==="local"?"Open locally":"Connect";
}
$("mode").onchange=mode;
async function submit(action,data={}){
  try{
    waiting=true;updateActions();
    const response=await fetch(action,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(data)});
    if(!response.ok)throw Error((await response.json()).error);
  }catch(error){message(error.message,true);}
  finally{waiting=false;updateActions();}
}
form.onsubmit=e=>{
  e.preventDefault();
  if(connect.disabled)return;
  message("Connecting…");
  submit("connect",Object.fromEntries(fields.map(k=>[k,field(k).type==="checkbox"?field(k).checked:field(k).value])));
};
resume.onclick=()=>{if(!resume.disabled)submit("resume");};
$("cancel").onclick=()=>submit("cancel");
$("challenge").onsubmit=e=>{
  e.preventDefault();
  const inputs=Array.from($("prompts").querySelectorAll("input")),values=inputs.map(input=>input.value);
  inputs.forEach(input=>input.value="");$("answer").disabled=true;
  submit("answer",{id:challenge,values});
};
function showPrompt(prompt){
  if((prompt?.id||null)===challenge)return;
  challenge=prompt?.id||null;
  $("prompts").replaceChildren();
  $("challenge").hidden=!prompt;
  if(!prompt)return;
  $("challenge-title").textContent=prompt.kind==="trust"?"Verify the SSH server":"SSH sign-in";
  $("challenge-text").textContent=prompt.text;
  $("answer").textContent=prompt.kind==="trust"?"Trust this fingerprint":"Continue";
  $("answer").disabled=false;
  for(const [text,echo] of prompt.prompts){
    const label=document.createElement("label"),input=document.createElement("input");
    label.textContent=text;input.type=echo?"text":"password";input.autocomplete="off";input.required=true;label.append(input);$("prompts").append(label);
  }
  ($("prompts").querySelector("input")||$("cancel")).focus();
}
async function poll(){
  try{
    const response=await fetch("state",{cache:"no-store"});
    if(!response.ok)throw Error("Controller unavailable");
    const state=await response.json();
    if(!initialized){
      for(const key of fields)if(state.prefs[key]!==undefined){
        if(field(key).type==="checkbox")field(key).checked=state.prefs[key]===true;
        else field(key).value=state.prefs[key];
      }
      initialized=true;mode();
    }
    if(state.target){location.replace(state.target);return;}
    busy=state.busy;resume.hidden=!state.resume;
    showPrompt(state.challenge);
    if(state.busy)message(state.challenge?"Waiting for SSH sign-in…":"Connecting…");
    else if(state.error)message(state.error,true);
    else if(!controllerReady&&!waiting)message("");
    controllerReady=true;
  }catch{controllerReady=false;message("The connection screen could not reach its local controller.",true);}
  updateActions();
  setTimeout(poll,250);
}
function finishSplash(){splash.remove();splashDone=true;updateActions();}
let seen=false;
try{seen=sessionStorage.getItem("bp-splash-seen")==="1";}catch{/* Storage may be unavailable in a WebView. */}
if(new URLSearchParams(location.search).get("splash")==="done"||seen)finishSplash();
else setTimeout(()=>{
  try{sessionStorage.setItem("bp-splash-seen","1");}catch{/* Continue without remembering the splash. */}
  splash.classList.add("leaving");setTimeout(finishSplash,270);
},2000);
mode();poll();
