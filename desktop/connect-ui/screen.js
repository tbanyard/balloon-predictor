"use strict";
const $=id=>document.getElementById(id), fields=["mode","address","host","username","ssh_port","web_port","allow_http"];
const field=key=>$(key.replaceAll("_","-"));
const message=(text,error=false)=>{$("status").textContent=text;$("status").classList.toggle("error",error);};
let challenge=null,initialized=false,waiting=false;
function mode(){
  const value=$("mode").value;
  $("web").hidden=value!=="web";$("ssh").hidden=value!=="ssh";
  $("address").disabled=value!=="web";
  for(const key of ["ssh-port","web-port"])$(key).disabled=value!=="ssh";
  $("connect").textContent=value==="local"?"Open locally":"Connect";
}
$("mode").onchange=mode;
async function submit(action,data={}){
  try{
    waiting=true;
    const response=await fetch(action,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(data)});
    if(!response.ok)throw Error((await response.json()).error);
  }catch(error){message(error.message,true);}
  finally{waiting=false;}
}
$("connection").onsubmit=e=>{
  e.preventDefault();message("Connecting…");
  submit("connect",Object.fromEntries(fields.map(k=>[k,field(k).type==="checkbox"?field(k).checked:field(k).value])));
};
$("resume").onclick=()=>submit("resume");
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
    const state=await (await fetch("state",{cache:"no-store"})).json();
    if(!initialized){
      for(const key of fields)if(state.prefs[key]!==undefined){
        if(field(key).type==="checkbox")field(key).checked=state.prefs[key]===true;
        else field(key).value=state.prefs[key];
      }
      initialized=true;mode();
    }
    if(state.target){location.replace(state.target);return;}
    $("connect").disabled=$("resume").disabled=state.busy||waiting;
    $("resume").hidden=!state.resume;
    showPrompt(state.challenge);
    if(state.busy)message(state.challenge?"Waiting for SSH sign-in…":"Connecting…");
    else if(state.error)message(state.error,true);
  }catch{message("The connection screen could not reach its local controller.",true);}
  setTimeout(poll,250);
}
mode();poll();
setTimeout(()=>{$("splash").classList.add("leaving");setTimeout(()=>$("splash").remove(),270);},780);
