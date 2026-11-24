"use strict";
(()=>{
  const $=id=>document.getElementById(id),form=$("signin-form"),challenge=$("signin-challenge");
  const destination=location.pathname==="/"?"/"+location.search:"/";
  let pollTimer=null,promptId=null,posting=false,flow=0;
  $("signin-server").textContent=location.host;
  function message(text,error=false){$("signin-status").textContent=text;$("signin-status").classList.toggle("error",error);}
  function busy(value){
    $("signin-button").disabled=$("username").disabled=$("password").disabled=value;
    $("signin-loader").hidden=!value;$("signin-cancel").hidden=!value;$("signin-cancel").disabled=posting;
  }
  async function request(path,data){
    let response;
    const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),20000);
    try{response=await fetch(path,data===undefined?{cache:"no-store",signal:controller.signal}:{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(data),signal:controller.signal});}
    catch{throw Error("Could not reach the sign-in service. Check your VPN or connection, then retry.");}
    finally{clearTimeout(timer);}
    let value;try{value=await response.json();}catch{throw Error("The sign-in service returned an unreadable response. Please retry.");}
    if(!response.ok)throw Error(value.detail||"Sign-in could not be completed.");
    return value;
  }
  function ready(text="",error=false){
    clearTimeout(pollTimer);pollTimer=null;posting=false;promptId=null;
    form.hidden=false;challenge.hidden=true;$("challenge-fields").replaceChildren();
    busy(false);message(text,error);
    if(error){$("password").setAttribute("aria-invalid","true");$("password").focus();}
  }
  function showQuestion(value){
    form.hidden=true;challenge.hidden=false;busy(true);$("signin-loader").hidden=true;
    message(value.message||"Complete the server sign-in below.");
    const prompt=value.challenge;
    if(!prompt||prompt.id===promptId)return;
    promptId=prompt.id;$("challenge-text").textContent=prompt.text;
    $("challenge-fields").replaceChildren();$("challenge-button").disabled=false;
    for(const [index,[text,echo]] of prompt.prompts.entries()){
      const label=document.createElement("label"),input=document.createElement("input");
      input.id="answer-"+index;label.htmlFor=input.id;label.textContent=text;
      input.type=echo?"text":"password";input.autocomplete="one-time-code";input.maxLength=4096;input.required=true;
      $("challenge-fields").append(label,input);
    }
    ($("challenge-fields").querySelector("input")||$("challenge-button")).focus();
  }
  function apply(value){
    if(value.status==="authenticated"){message("Opening your personal workspace…");location.replace(destination);return;}
    if(value.status==="failed"||value.status==="cancelled"||value.status==="idle"){ready(value.status==="idle"?"":value.message,value.status==="failed");return;}
    if(value.status==="challenge")showQuestion(value);
    else{busy(true);message(value.message||"Checking your server account…");}
    pollTimer=setTimeout(poll,350);
  }
  async function poll(){
    const version=flow;
    try{const value=await request("/api/login");if(version===flow)apply(value);}catch(error){if(version===flow)ready(error.message,true);}
  }
  form.onsubmit=async event=>{
    event.preventDefault();if(posting)return;
    posting=true;const version=++flow;clearTimeout(pollTimer);
    const username=$("username").value.trim(),password=$("password").value;
    $("password").value="";$("password").removeAttribute("aria-invalid");
    busy(true);message("Checking your server account…");
    try{const value=await request("/api/login",{username,password});if(version===flow){posting=false;apply(value);}}catch(error){if(version===flow)ready(error.message,true);}
  };
  challenge.onsubmit=async event=>{
    event.preventDefault();const version=flow;$("challenge-button").disabled=true;clearTimeout(pollTimer);
    const inputs=Array.from($("challenge-fields").querySelectorAll("input")),answers=inputs.map(input=>input.value);
    inputs.forEach(input=>input.value="");$("signin-loader").hidden=false;
    try{const value=await request("/api/login/answer",{id:promptId,answers});if(version===flow)apply(value);}
    catch(error){if(version===flow){$("challenge-button").disabled=false;message(error.message,true);pollTimer=setTimeout(poll,350);}}
  };
  $("signin-cancel").onclick=async()=>{
    ++flow;clearTimeout(pollTimer);
    try{await request("/api/login/cancel",{});ready("Sign-in cancelled.");}
    catch(error){ready(error.message,true);}
  };
  const token=location.hash.slice(1);
  if(token){
    history.replaceState(null,"","/signin");busy(true);message("Opening your personal workspace…");
    request("/api/session",{token}).then(()=>location.replace(destination)).catch(error=>ready(error.message,true));
  }else poll();
})();
