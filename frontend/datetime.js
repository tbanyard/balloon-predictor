/* Plain editable UTC fields and a calendar that works in every packaged WebView. */
(function(){
  "use strict";
  const valueProperty=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,"value");
  const booleanProperties=Object.fromEntries(["required","disabled"].map(key=>[key,Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,key)]));
  const widgets=new Map(),pad=n=>String(n).padStart(2,"0");
  function validDate(value){
    if(!/^\d{4}-\d{2}-\d{2}$/.test(value)||value.slice(0,4)==="0000")return false;
    const d=new Date(value+"T00:00:00Z");return Number.isFinite(d.getTime())&&d.toISOString().slice(0,10)===value;
  }
  function validTime(value){return /^(?:[01]\d|2[0-3]):[0-5]\d(?::[0-5]\d)?$/.test(value);}
  function parts(value){const [date="",time=""]=String(value||"").split("T");return{date,time};}
  function enhance(input){
    const original=input.value,seconds=Number(input.step||60)<60;
    const label=document.querySelector('label[for="'+input.id+'"]'),name=label?.textContent.trim().replace(/\s+/g," ")||input.closest("label")?.firstChild?.textContent?.trim()||"Date and time";
    const group=document.createElement("span");group.className="utc-datetime";
    function field(kind,title,placeholder){
      const cell=document.createElement("span"),caption=document.createElement("span"),control=document.createElement("input");
      cell.className="utc-part";caption.className="utc-caption";caption.textContent=title;
      control.type="text";control.id=input.id+"-"+kind;control.placeholder=placeholder;control.autocomplete="off";control.spellcheck=false;
      control.setAttribute("aria-label",name+": "+title);control.setAttribute("inputmode","text");
      cell.append(caption,control);group.append(cell);return control;
    }
    const date=field("date","Date (YYYY-MM-DD)","YYYY-MM-DD"),time=field("time","Time (UTC)",seconds?"HH:mm:ss":"HH:mm");
    const button=document.createElement("button");button.type="button";button.className="utc-calendar-button";button.id=input.id+"-calendar";
    button.title="Choose a date";button.setAttribute("aria-label","Choose date for "+name);button.setAttribute("aria-haspopup","dialog");
    button.innerHTML='<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 3v4m12-4v4M3 10h18M5 5h14a2 2 0 0 1 2 2v13H3V7a2 2 0 0 1 2-2Z"/><path d="M7 14h2m3 0h2m3 0h1M7 17h2m3 0h2"/></svg>';
    date.parentNode.append(button);date.parentNode.classList.add("utc-date");
    input.type="hidden";input.after(group);if(label)label.htmlFor=date.id;
    const state={input,date,time,button,seconds,editing:false};widgets.set(input.id,state);
    function validate(){
      const required=input.required||Boolean(date.value||time.value);
      date.required=time.required=required;date.disabled=time.disabled=button.disabled=input.disabled;
      let dateError=date.value&&!validDate(date.value)?"Enter a valid date as YYYY-MM-DD.":"";
      let timeError=time.value&&!validTime(time.value)?"Enter a 24-hour UTC time as "+(seconds?"HH:mm:ss.":"HH:mm."):"";
      if(!timeError&&time.value){
        const [h,m,s=0]=time.value.split(":").map(Number),step=Number(input.step||60);
        if(Number.isFinite(step)&&step>0&&(h*3600+m*60+s)%step)timeError=step===3600?"Choose a whole hour (for example 12:00).":step===60?"Use whole minutes (seconds must be 00).":"Use an allowed time interval.";
      }
      if(!dateError&&!timeError&&date.value&&time.value){
        const v=date.value+"T"+time.value.padEnd(8,":00");
        if(input.min&&v<(input.min.length===16?input.min+":00":input.min))dateError="Choose a date and time on or after "+input.min.replace("T"," ")+" UTC.";
        if(input.max&&v>(input.max.length===16?input.max+":00":input.max))dateError="Choose a date and time on or before "+input.max.replace("T"," ")+" UTC.";
      }
      date.setCustomValidity(dateError);time.setCustomValidity(timeError);
      return Boolean(date.value&&time.value&&!dateError&&!timeError);
    }
    function sync(){
      if(!state.editing){const p=parts(valueProperty.get.call(input));date.value=p.date;time.value=p.time?(seconds?(p.time.length===5?p.time+":00":p.time):p.time):"";}
      validate();
    }
    Object.defineProperty(input,"value",{configurable:true,get(){return valueProperty.get.call(this);},set(value){valueProperty.set.call(this,String(value??""));sync();}});
    for(const key of ["required","disabled"]){const descriptor=booleanProperties[key];Object.defineProperty(input,key,{configurable:true,get(){return descriptor.get.call(this);},set(v){descriptor.set.call(this,v);validate();}});}
    function edited(event){
      event.stopPropagation();state.editing=true;
      const valid=validate();let v=valid?date.value+"T"+time.value:"";
      if(v.endsWith(":00")&&parts(v).time.length===8)v=v.slice(0,-3);
      input.value=v;state.editing=false;input.dispatchEvent(new Event(event.type,{bubbles:true}));
    }
    for(const control of [date,time]){
      control.addEventListener("input",edited);control.addEventListener("change",edited);
      control.addEventListener("blur",()=>{if(validate())sync();});
    }
    button.onclick=()=>openCalendar(state);
    new MutationObserver(()=>validate()).observe(input,{attributes:true,attributeFilter:["required","disabled","min","max","step"]});
    input.value=original;
  }
  let calendar,owner,month,focusDate;
  function makeCalendar(){
    calendar=document.createElement("dialog");calendar.className="utc-calendar";calendar.setAttribute("aria-label","Choose a UTC date");
    calendar.innerHTML='<div class="utc-calendar-heading"><button type="button" data-previous aria-label="Previous month">‹</button><select aria-label="Month"></select><input aria-label="Year" type="number" min="1" max="9999"><button type="button" data-next aria-label="Next month">›</button></div><div class="utc-weekdays" aria-hidden="true"><span>Mon</span><span>Tue</span><span>Wed</span><span>Thu</span><span>Fri</span><span>Sat</span><span>Sun</span></div><div class="utc-days" role="group" aria-label="Days"></div><div class="utc-calendar-actions"><button type="button" data-today>Today (UTC)</button><button type="button" data-clear>Clear</button><button type="button" data-close>Close</button></div>';
    document.body.append(calendar);
    const select=calendar.querySelector("select"),year=calendar.querySelector("input");
    for(let m=0;m<12;m++){const o=document.createElement("option");o.value=m;o.textContent=new Date(Date.UTC(2000,m,1)).toLocaleDateString("en-GB",{month:"long",timeZone:"UTC"});select.append(o);}
    select.onchange=()=>{month.setUTCMonth(Number(select.value));renderCalendar();};
    year.onchange=()=>{const v=Number(year.value);if(Number.isInteger(v)&&v>=1&&v<=9999){month.setUTCFullYear(v);renderCalendar();}};
    calendar.querySelector("[data-previous]").onclick=()=>moveMonth(-1);calendar.querySelector("[data-next]").onclick=()=>moveMonth(1);
    calendar.querySelector("[data-today]").onclick=()=>chooseDate(new Date().toISOString().slice(0,10));
    calendar.querySelector("[data-clear]").onclick=()=>{owner.input.value="";owner.input.dispatchEvent(new Event("change",{bubbles:true}));closeCalendar();};
    calendar.querySelector("[data-close]").onclick=closeCalendar;
    calendar.addEventListener("cancel",event=>{event.preventDefault();event.stopPropagation();closeCalendar();});
    calendar.addEventListener("keydown",event=>{
      if(event.key==="Escape"){event.preventDefault();event.stopPropagation();closeCalendar();return;}
      const day=event.target.dataset?.day;if(!day)return;
      let d=new Date(day+"T00:00:00Z"),delta={ArrowLeft:-1,ArrowRight:1,ArrowUp:-7,ArrowDown:7}[event.key];
      if(delta!==undefined)d.setUTCDate(d.getUTCDate()+delta);
      else if(event.key==="PageUp"||event.key==="PageDown"){
        const dayOfMonth=d.getUTCDate();d.setUTCDate(1);d.setUTCMonth(d.getUTCMonth()+(event.key==="PageUp"?-1:1));
        const last=new Date(d);last.setUTCMonth(last.getUTCMonth()+1);last.setUTCDate(0);d.setUTCDate(Math.min(dayOfMonth,last.getUTCDate()));
      }else if(event.key==="Home")d.setUTCDate(d.getUTCDate()-(d.getUTCDay()+6)%7);
      else if(event.key==="End")d.setUTCDate(d.getUTCDate()+6-(d.getUTCDay()+6)%7);
      else return;
      event.preventDefault();event.stopPropagation();if(d.getUTCFullYear()<1||d.getUTCFullYear()>9999)return;
      focusDate=d.toISOString().slice(0,10);month=new Date(d);month.setUTCDate(1);renderCalendar(true);
    });
  }
  function moveMonth(delta){const d=new Date(month);d.setUTCMonth(d.getUTCMonth()+delta);if(d.getUTCFullYear()<1||d.getUTCFullYear()>9999)return;month=d;renderCalendar();}
  function closeCalendar(){if(calendar.open&&typeof calendar.close==="function")calendar.close();else calendar.removeAttribute("open");owner?.button.focus();}
  function chooseDate(date){
    owner.date.value=date;if(!owner.time.value)owner.time.value=owner.seconds?"00:00:00":"00:00";
    owner.date.dispatchEvent(new Event("change",{bubbles:true}));closeCalendar();
  }
  function renderCalendar(focus=false){
    calendar.querySelector("select").value=month.getUTCMonth();calendar.querySelector("input").value=month.getUTCFullYear();
    const days=calendar.querySelector(".utc-days");days.replaceChildren();
    const start=(month.getUTCDay()+6)%7,last=new Date(month);last.setUTCMonth(last.getUTCMonth()+1);last.setUTCDate(0);
    for(let i=0;i<start;i++)days.append(document.createElement("span"));
    const today=new Date().toISOString().slice(0,10);
    for(let day=1;day<=last.getUTCDate();day++){
      const v=String(month.getUTCFullYear()).padStart(4,"0")+"-"+pad(month.getUTCMonth()+1)+"-"+pad(day),b=document.createElement("button");
      b.type="button";b.textContent=day;b.dataset.day=v;b.setAttribute("aria-label",v);b.setAttribute("aria-pressed",String(v===owner.date.value));
      if(v===today)b.setAttribute("aria-current","date");b.tabIndex=v===focusDate?0:-1;b.onclick=()=>chooseDate(v);days.append(b);
    }
    const selected=Array.from(days.querySelectorAll("button")).find(b=>b.dataset.day===focusDate)||days.querySelector("button");if(selected){selected.tabIndex=0;if(focus)selected.focus();}
  }
  function openCalendar(state){
    if(state.input.disabled)return;if(!calendar)makeCalendar();owner=state;
    focusDate=validDate(state.date.value)?state.date.value:new Date().toISOString().slice(0,10);month=new Date(focusDate+"T00:00:00Z");month.setUTCDate(1);
    renderCalendar();if(typeof calendar.showModal==="function")calendar.showModal();else calendar.setAttribute("open","");renderCalendar(true);
  }
  document.querySelectorAll('input[type="datetime-local"]').forEach(enhance);
  window.BalloonDateTime={enhance,validDate,validTime};
})();
