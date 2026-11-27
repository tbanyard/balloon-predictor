/* Connection recovery never navigates or replaces the flight document. */
(()=>{
  'use strict';
  const endpoint='/__bp_connection/',labels={connected:'Connected',reconnecting:'Reconnecting…',signin_required:'Sign-in required'};
  let username=null,native=false,known=false,state='connected',recovery=null,checking=null,promptId=null,abortPrompt=null,recoveryStop=null;
  const indicator=document.querySelector('.local-indicator');
  if(indicator){indicator.classList.add('connection-indicator');indicator.setAttribute('role','status');}
  function show(value,mode='server'){
    state=value;const label=labels[value]||labels.reconnecting;
    if(indicator){indicator.dataset.state=value;indicator.title=label;indicator.setAttribute('aria-label',label);indicator.replaceChildren();const dot=document.createElement('i');indicator.append(document.createTextNode(mode==='local'?'Local workspace ':'Server '),dot);}
    document.dispatchEvent(new CustomEvent('bp:connection',{detail:{status:value,label}}));
  }
  async function raw(path,options={}){
    const ctrl=new AbortController(),timer=setTimeout(()=>ctrl.abort(),options.timeout||15000);
    const stop=()=>ctrl.abort();if(options.signal?.aborted)ctrl.abort();else options.signal?.addEventListener('abort',stop,{once:true});
    try{return await fetch(path,{...options,signal:ctrl.signal,cache:'no-store'});}finally{clearTimeout(timer);options.signal?.removeEventListener('abort',stop);}
  }
  function aborted(){return new DOMException('Calculation cancelled','AbortError');}
  function waitFor(promise,signal){if(!signal)return promise;if(signal.aborted)return Promise.reject(aborted());return new Promise((resolve,reject)=>{const stop=()=>reject(aborted());signal.addEventListener('abort',stop,{once:true});promise.then(resolve,reject).finally(()=>signal.removeEventListener('abort',stop));});}
  async function control(action,data){
    const response=await raw(endpoint+action,data===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
    if(!response.ok)throw Error('The connection could not recover. Check the VPN and server. Your flights remain open.');
    return response.json();
  }
  async function check(probe=false){
    if(probe)known=false;
    if(checking)return checking;
    checking=(async()=>{
      if(native||!known){try{const value=await control('state');if(value.native_ssh){native=true;known=true;show(value.status);return value;}}catch{}known=true;}
      try{const response=await raw('/api/session-status');if(response.status===401){show('signin_required');return {status:'signin_required'};}if(response.ok){const value=await response.json();username=value.username||null;show('connected',value.mode);return {status:'connected'};}}catch{}
      show('reconnecting');return {status:'reconnecting'};
    })().finally(()=>checking=null);return checking;
  }
  const modal=document.createElement('dialog');modal.id='connection-recovery';modal.innerHTML='<h2>Reconnect to your server</h2><p>Your current flights stay open.</p><form><div data-prompts></div><p data-message role="status"></p><div class="recovery-actions"><button class="button primary" type="submit">Continue</button><button class="button quiet" type="button" data-cancel>Cancel</button></div></form>';
  document.body.append(modal);
  const fields=modal.querySelector('[data-prompts]'),message=modal.querySelector('[data-message]'),form=modal.querySelector('form');
  function clearPrompt(){promptId=null;fields.replaceChildren();form.onsubmit=null;if(modal.open)modal.close();}
  function question(text,prompts,submit){
    fields.replaceChildren();message.textContent=text;const inputs=[];
    for(const [label,echo] of prompts){const wrap=document.createElement('label'),input=document.createElement('input');wrap.textContent=label;input.type=echo?'text':'password';input.autocomplete='off';input.maxLength=4096;input.required=true;wrap.append(input);fields.append(wrap);inputs.push(input);}
    form.onsubmit=async event=>{event.preventDefault();const values=inputs.map(input=>input.value);inputs.forEach(input=>input.value='');try{await submit(values);}catch(error){message.textContent=error.message;}};
    if(!modal.open)modal.showModal();(inputs[0]||form.querySelector('button')).focus();
  }
  function cancel(){recoveryStop?.abort();abortPrompt?.();clearPrompt();}
  modal.querySelector('[data-cancel]').onclick=cancel;modal.addEventListener('cancel',event=>{event.preventDefault();cancel();});
  async function webLogin(){
    let done=false,failure=null,polling=false;
    abortPrompt=()=>{failure=Error('Sign-in cancelled. Your flights remain open.');raw('/api/login/cancel',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'}).catch(()=>{});};
    async function apply(value){
      if(value.status==='authenticated'){done=true;clearPrompt();return;}
      if(value.status==='failed'||value.status==='cancelled'){polling=false;begin();message.textContent=value.message||'Sign-in did not complete.';return;}
      if(value.status==='challenge'){
        if(promptId!==value.challenge.id){promptId=value.challenge.id;question(value.challenge.text,value.challenge.prompts,async answers=>{await send('/api/login/answer',{id:promptId,answers});});}
      }else message.textContent=value.message||'Checking your server account…';
    }
    async function send(path,data){const r=await raw(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});const value=await r.json();if(!r.ok)throw Error(value.detail||'Sign-in failed.');polling=true;await apply(value);}
    function begin(){question('Sign in with your server account.',[['Username',true],['Password',false]],async values=>send('/api/login',{username:values[0].trim(),password:values[1]}));}begin();
    while(!done){if(failure)throw failure;if(polling){try{const r=await raw('/api/login');await apply(await r.json());}catch(error){message.textContent=error.message;}}await new Promise(r=>setTimeout(r,400));}
  }
  async function recover(renew=false){
    if(recovery)return recovery;
    recovery=(async()=>{
      const stop=new AbortController();recoveryStop=stop;
      const first=await check();if(stop.signal.aborted)throw aborted();
      abortPrompt=()=>{stop.abort();if(native)control('cancel',{}).catch(()=>{});};
      if(native){
        if(first.status!=='connected')await control('recover',{});
        const deadline=Date.now()+300000;
        for(;;){
          if(stop.signal.aborted)throw Error('Reconnection cancelled. Your flights remain open.');
          const info=await control('state');if(stop.signal.aborted)throw aborted();show(info.status);
          if(info.status==='connected')break;
          if(info.challenge&&info.challenge.id!==promptId){promptId=info.challenge.id;question(info.challenge.text,info.challenge.prompts,values=>control('answer',{id:info.challenge.id,values}));}
          if(info.status==='signin_required'&&!info.challenge&&!modal.open){question('The server needs you to sign in again.',[],()=>control('recover',{}));}
          if(Date.now()>deadline)throw Error('The server is still unavailable. Check the VPN, then retry. Your flights remain open.');
          await new Promise(r=>setTimeout(r,400));
        }
        clearPrompt();
        // A fresh Unix-identity ticket renews an expired/restarted server
        // session on the existing origin, without navigating to /signin.
        if(renew||first.status!=='connected'){
          const identity=await control('identity',{});
          if(identity.token){const reply=await raw('/api/session',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({token:identity.token})});if(!reply.ok)throw Error('Could not reopen your server workspace. Retry the action.');}
        }
      }else{
        let info=first;
        for(let i=0;info.status==='reconnecting'&&i<15;i++){if(stop.signal.aborted)throw aborted();await new Promise(r=>setTimeout(r,2000));info=await check();}
        if(stop.signal.aborted)throw aborted();
        if(info.status==='reconnecting')throw Error('Could not reach BalloonPredictor. Check the VPN and server. Your saved flights remain available.');
        if(info.status==='signin_required'||renew)await webLogin();
      }
      show('connected');
    })().finally(()=>{clearPrompt();abortPrompt=null;recovery=null;recoveryStop=null;});
    return recovery;
  }
  async function request(path,options={}){
    const recoverForRequest=async renew=>{const stop=()=>cancel();options.signal?.addEventListener('abort',stop,{once:true});try{await waitFor(recover(renew),options.signal);}finally{options.signal?.removeEventListener('abort',stop);}if(options.signal?.aborted)throw aborted();};
    let response;
    try{response=await raw(path,{...options,timeout:45000});}
    catch(error){if(options.signal?.aborted)throw aborted();await recoverForRequest();if((options.method||'GET').toUpperCase()!=='GET'&&!options.headers?.['X-BP-Request-ID'])throw Error('The connection is restored. Repeat that action; your current flights remain open.');return raw(path,{...options,timeout:45000});}
    if(response.status===401){let value;try{value=await response.clone().json();}catch{}if(value?.login_required){await recoverForRequest(true);return raw(path,{...options,timeout:45000});}}
    if(response.status===503&&response.headers.get('X-Balloon-Reconnect')==='yes'){await recoverForRequest();return raw(path,{...options,timeout:45000});}
    return response;
  }
  window.BalloonNetwork={request,check,recover,control,get native(){return native;},get state(){return state;},get username(){return username;}};
  check();setInterval(check,20000);window.addEventListener('online',()=>check(true));document.addEventListener('visibilitychange',()=>{if(!document.hidden)check();});
})();
