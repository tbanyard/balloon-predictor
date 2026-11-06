/* Independent map/profile/sounding panels. Geometry and locks are retained. */
(() => {
  "use strict";
  const area = document.getElementById("window-area"), key = "balloon-predictor-window-layout";
  let saved = {};
  try { saved = JSON.parse(localStorage.getItem(key)) || {}; } catch {}
  const panels = new Map(); let z = 10;
  const mobile = () => matchMedia("(max-width: 760px)").matches;
  const change = () => document.dispatchEvent(new CustomEvent("bp:layout"));
  function persist() {
    const value = {};
    for (const [id, p] of panels) value[id] = {x:p.x,y:p.y,w:p.w,h:p.h,locked:p.locked,minimised:p.minimised,docked:p.docked};
    try {localStorage.setItem(key, JSON.stringify(value));} catch {}
  }
  function defaults(id) {
    const w = Math.max(280, area.clientWidth), h = Math.max(420, area.clientHeight);
    if (id === "map-panel") return {x:10,y:10,w:w-20,h:Math.max(220,h-320),locked:true,minimised:false,docked:true};
    if (id === "profile-panel") return {x:10,y:Math.max(160,h-300),w:w-20,h:290,locked:false,minimised:false,docked:true};
    return {x:Math.max(10,w-Math.min(710,w-30)-18),y:28,w:Math.min(710,w-30),h:Math.min(660,h-60),locked:false,minimised:true,docked:false};
  }
  function place(p) {
    if(!mobile()){
      const w = Math.max(240, area.clientWidth), h = Math.max(240, area.clientHeight);
      p.w = Math.min(Math.max(240,p.w),w-12); p.h = Math.min(Math.max(180,p.h),h-12);
      p.x = Math.max(6,Math.min(p.x,w-p.w-6)); p.y = Math.max(6,Math.min(p.y,h-p.h-6));
      Object.assign(p.el.style,{left:p.x+"px",top:p.y+"px",width:p.w+"px",height:p.h+"px"});
    }
    p.el.classList.toggle("window-locked",p.locked);
    p.lock.setAttribute("aria-pressed",String(p.locked));
    p.lock.textContent=p.locked?"Unlock":"Lock";
    p.lock.title=p.locked?"Unlock moving and resizing":"Lock this position and size";
    p.el.dataset.locked=String(p.locked);
    for(const button of document.querySelectorAll('[data-window-open="'+p.id+'"]')) button.setAttribute("aria-pressed",String(!p.el.hidden));
  }
  function focus(p) {p.el.style.zIndex=String(++z);}
  function show(id) {
    const p=panels.get(id);if(!p)return;
    p.el.hidden=false;p.minimised=false;focus(p);place(p);persist();change();
    document.dispatchEvent(new CustomEvent("bp:panelshown",{detail:{id}}));
  }
  function minimise(p) {p.el.hidden=true;p.minimised=true;place(p);persist();change();}
  function reset() {
    for(const [id,p] of panels){Object.assign(p,defaults(id));p.el.hidden=p.minimised;place(p);}
    persist();change();
  }
  function gesture(p,handle,resize) {
    let start=null;
    handle.addEventListener("pointerdown",e=>{
      if(p.locked||mobile()||e.button!==0||(!resize&&e.target.closest("button,a,input,select,label,summary")))return;
      e.preventDefault();focus(p);handle.setPointerCapture(e.pointerId);
      p.docked=false;
      start={x:e.clientX,y:e.clientY,px:p.x,py:p.y,w:p.w,h:p.h};p.el.classList.add("window-moving");
    });
    handle.addEventListener("pointermove",e=>{
      if(!start)return;
      const dx=e.clientX-start.x,dy=e.clientY-start.y;
      if(resize){p.w=start.w+dx;p.h=start.h+dy;}else{p.x=start.px+dx;p.y=start.py+dy;}
      place(p);change();
    });
    const finish=()=>{if(!start)return;start=null;p.el.classList.remove("window-moving");persist();change();};
    handle.addEventListener("pointerup",finish);handle.addEventListener("pointercancel",finish);handle.addEventListener("lostpointercapture",finish);
    handle.addEventListener("keydown",e=>{
      if(p.locked||mobile()||e.target!==handle||!e.key.startsWith("Arrow"))return;
      e.preventDefault();const step=e.shiftKey?30:10;
      p.docked=false;
      const dx=e.key==="ArrowRight"?step:e.key==="ArrowLeft"?-step:0;
      const dy=e.key==="ArrowDown"?step:e.key==="ArrowUp"?-step:0;
      if(resize){p.w+=dx;p.h+=dy;}else{p.x+=dx;p.y+=dy;}
      place(p);persist();change();
    });
  }
  for(const id of ["map-panel","profile-panel","sounding-panel"]){
    const el=document.getElementById(id),head=el.querySelector("[data-window-handle]");
    const initial=defaults(id),valid=saved[id];
    if(valid&&["x","y","w","h"].every(k=>Number.isFinite(valid[k]))){
      for(const k of ["x","y","w","h"])initial[k]=valid[k];initial.locked=Boolean(valid.locked);
      if(typeof valid.docked==="boolean")initial.docked=valid.docked;
      if(id!=="sounding-panel")initial.minimised=Boolean(valid.minimised);
    }
    const p={...initial,id,el};panels.set(id,p);el.hidden=p.minimised;
    if(id!=="map-panel"){
      const body=document.createElement("div");body.className="window-body";
      for(const child of [...el.children])if(child!==head)body.append(child);
      el.append(body);
    }
    head.classList.add("window-drag-handle");head.tabIndex=0;head.title="Drag to move; arrow keys move the unlocked panel";
    const controls=document.createElement("span");controls.className="window-controls";
    p.lock=document.createElement("button");p.lock.type="button";p.lock.className="window-button";
    p.lock.setAttribute("aria-label","Lock or unlock "+id.replace("-panel","")+" panel");
    p.lock.onclick=()=>{p.locked=!p.locked;place(p);persist();};
    const dock=document.createElement("button");dock.type="button";dock.className="window-button";dock.textContent="Dock";
    dock.title="Restore this panel to its default position";
    dock.onclick=()=>{const locked=p.locked;Object.assign(p,defaults(id),{locked,minimised:false});el.hidden=false;place(p);persist();change();};
    const min=document.createElement("button");min.type="button";min.className="window-button";min.textContent="−";
    min.setAttribute("aria-label","Minimise "+id.replace("-panel","")+" panel");min.title="Minimise; reopen from the map toolbar";min.onclick=()=>minimise(p);
    controls.append(p.lock,dock,min);head.append(controls);
    const resize=document.createElement("button");resize.type="button";resize.className="window-resize";
    resize.setAttribute("aria-label","Resize "+id.replace("-panel","")+" panel");resize.title="Drag to resize; arrow keys resize when unlocked";el.append(resize);
    gesture(p,head,false);gesture(p,resize,true);el.addEventListener("pointerdown",()=>focus(p));place(p);
  }
  document.querySelectorAll("[data-window-open]").forEach(button=>button.addEventListener("click",()=>show(button.dataset.windowOpen)));
  document.getElementById("reset-layout").onclick=reset;
  function reflow(){
    for(const p of panels.values()){
      if(p.docked&&!mobile()){const d=defaults(p.id);for(const k of ["x","y","w","h"])p[k]=d[k];}
      place(p);
    }
    change();
  }
  window.addEventListener("resize",reflow);
  if(window.ResizeObserver)new ResizeObserver(reflow).observe(area);
  const workspace=document.getElementById("workspace"),toggle=document.getElementById("sidebar-toggle");
  function sidebar(hidden){
    workspace.classList.toggle("sidebar-hidden",hidden);toggle.textContent=hidden?"›":"‹";
    toggle.setAttribute("aria-expanded",String(!hidden));toggle.setAttribute("aria-label",hidden?"Show flight controls":"Hide flight controls");
    if("inert" in document.getElementById("flight-sidebar"))document.getElementById("flight-sidebar").inert=hidden;
    try{localStorage.setItem("balloon-predictor-sidebar-hidden",String(hidden));}catch{}
    setTimeout(change,240);
  }
  toggle.onclick=()=>sidebar(!workspace.classList.contains("sidebar-hidden"));
  try{sidebar(localStorage.getItem("balloon-predictor-sidebar-hidden")==="true");}catch{}
  window.BalloonWindows={show,reset,minimise:id=>{const p=panels.get(id);if(p)minimise(p);}};
})();
