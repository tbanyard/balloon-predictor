/* v1.3: docked split, floating windows and a mobile control drawer. */
(() => {
  "use strict";
  const area=document.getElementById("window-area"), key="balloon-predictor-window-layout-v1.3";
  const panels=new Map(), mobile=()=>matchMedia("(max-width: 760px), (max-height: 520px) and (pointer: coarse)").matches;
  let saved={},z=10,ratio=.62,maximised=null,mobileActive="map-panel",wasMobile=mobile();
  try {saved=JSON.parse(localStorage.getItem(key))||{};if(Number.isFinite(saved.ratio))ratio=Math.max(.25,Math.min(.78,saved.ratio));}catch{}
  const change=()=>document.dispatchEvent(new CustomEvent("bp:layout"));
  function persist(){
    const state={ratio};for(const [id,p]of panels)state[id]={x:p.x,y:p.y,w:p.w,h:p.h,locked:p.locked,minimised:p.minimised,docked:p.docked};
    try{localStorage.setItem(key,JSON.stringify(state));}catch{}
  }
  function dimensions(){return {w:Math.max(260,area.clientWidth),h:Math.max(300,area.clientHeight)};}
  function defaults(id){const {w,h}=dimensions();return id==="sounding-panel"?{x:25,y:25,w:Math.min(710,w-30),h:Math.min(660,h-30),locked:false,minimised:true,docked:false}:{x:8,y:8,w:w-16,h:h-16,locked:false,minimised:false,docked:true};}
  function focus(p){p.el.style.zIndex=String(++z);}
  function draw(){
    const {w,h}=dimensions(), map=panels.get("map-panel"), profile=panels.get("profile-panel");
    const split=!mobile()&&!maximised&&map?.docked&&profile?.docked&&!map.minimised&&!profile.minimised;
    for(const [id,p]of panels){
      const isMax=maximised===id;
      p.el.hidden=mobile()?mobileActive!==id:p.minimised||Boolean(maximised&&!isMax);
      if(mobile())Object.assign(p.el.style,{left:"0px",top:"0px",width:"100%",height:"100%"});
      else {
        let g={x:p.x,y:p.y,w:p.w,h:p.h};
        if(isMax)g={x:6,y:6,w:w-12,h:h-12};
        else if(p.docked){
          if(split){const cut=Math.round((h-24)*ratio);g={x:8,y:id==="map-panel"?8:cut+16,w:w-16,h:id==="map-panel"?cut:h-cut-24};}
          else g={x:8,y:8,w:w-16,h:h-16};
        }else{
          g.w=Math.min(Math.max(240,g.w),w-12);g.h=Math.min(Math.max(160,g.h),h-12);
          g.x=Math.max(6,Math.min(g.x,w-g.w-6));g.y=Math.max(6,Math.min(g.y,h-g.h-6));
        }
        p.rendered=g;Object.assign(p.el.style,{left:g.x+"px",top:g.y+"px",width:g.w+"px",height:g.h+"px"});
      }
      p.el.classList.toggle("window-locked",p.locked);p.el.classList.toggle("window-maximised",isMax);p.el.dataset.locked=String(p.locked);
      p.lock.textContent=p.locked?"Unlock":"Lock";p.lock.setAttribute("aria-pressed",String(p.locked));
      p.max.textContent=isMax?"↙":"+";p.max.setAttribute("aria-label",(isMax?"Restore ":"Maximise ")+id.replace("-panel","")+" panel");
      for(const b of document.querySelectorAll('[data-window-open="'+id+'"]'))b.setAttribute("aria-pressed",String(!p.el.hidden));
    }
    divider.hidden=!split;divider.style.top=(Math.round((h-24)*ratio)+8)+"px";
    divider.setAttribute("aria-valuenow",String(Math.round(ratio*100)));
    divider.setAttribute("aria-disabled",String(Boolean(map?.locked||profile?.locked)));
    change();
  }
  function show(id){const p=panels.get(id);if(!p)return;maximised=null;p.minimised=false;if(mobile())mobileActive=id;focus(p);draw();persist();document.dispatchEvent(new CustomEvent("bp:panelshown",{detail:{id}}));}
  function minimise(p){if(maximised===p.id)maximised=null;if(mobile())mobileActive=null;else p.minimised=true;draw();persist();}
  function reset(){maximised=null;ratio=.62;mobileActive="map-panel";for(const[id,p]of panels)Object.assign(p,defaults(id));draw();persist();}
  function gesture(p,handle,edge){
    let start;
    handle.addEventListener("pointerdown",e=>{
      if(p.locked||mobile()||maximised||e.button!==0||(!edge&&e.target.closest("button,a,input,select,label,summary")))return;
      e.preventDefault();focus(p);handle.setPointerCapture?.(e.pointerId);
      Object.assign(p,p.rendered);p.docked=false;start={cx:e.clientX,cy:e.clientY,x:p.x,y:p.y,w:p.w,h:p.h};p.el.classList.add("window-moving");
    });
    handle.addEventListener("pointermove",e=>{
      if(!start)return;const dx=e.clientX-start.cx,dy=e.clientY-start.cy;
      if(!edge){p.x=start.x+dx;p.y=start.y+dy;}
      else {
        const right=start.x+start.w,bottom=start.y+start.h;
        if(edge.includes("e"))p.w=Math.max(240,start.w+dx);
        if(edge.includes("s"))p.h=Math.max(160,start.h+dy);
        if(edge.includes("w")){p.x=Math.max(6,Math.min(start.x+dx,right-240));p.w=right-p.x;}
        if(edge.includes("n")){p.y=Math.max(6,Math.min(start.y+dy,bottom-160));p.h=bottom-p.y;}
      }
      draw();
    });
    const finish=()=>{if(!start)return;start=null;p.el.classList.remove("window-moving");Object.assign(p,p.rendered);persist();draw();};
    for(const event of ["pointerup","pointercancel","lostpointercapture"])handle.addEventListener(event,finish);
    handle.addEventListener("keydown",e=>{
      if(p.locked||mobile()||maximised||e.target!==handle||!e.key.startsWith("Arrow"))return;
      e.preventDefault();Object.assign(p,p.rendered);p.docked=false;const s=e.shiftKey?30:10,dx=e.key==="ArrowLeft"?-s:e.key==="ArrowRight"?s:0,dy=e.key==="ArrowUp"?-s:e.key==="ArrowDown"?s:0;
      if(edge){p.w+=dx;p.h+=dy;}else{p.x+=dx;p.y+=dy;}draw();persist();
    });
  }
  const divider=document.createElement("div");divider.className="dock-divider";divider.tabIndex=0;divider.setAttribute("role","separator");divider.setAttribute("aria-label","Map and Profile split; unlock both panels to resize");divider.setAttribute("aria-orientation","horizontal");divider.setAttribute("aria-valuemin","25");divider.setAttribute("aria-valuemax","78");divider.title="Drag to change Map / Profile split (unlock both panels)";area.append(divider);
  let splitting=false;
  divider.addEventListener("pointerdown",e=>{if(e.button!==0||[...panels.values()].some(p=>p.docked&&p.locked))return;e.preventDefault();divider.setPointerCapture?.(e.pointerId);splitting=true;});
  divider.addEventListener("pointermove",e=>{if(!splitting)return;const r=area.getBoundingClientRect();ratio=Math.max(.25,Math.min(.78,(e.clientY-r.top-8)/(dimensions().h-24)));draw();});
  for(const event of ["pointerup","pointercancel","lostpointercapture"])divider.addEventListener(event,()=>{if(splitting){splitting=false;persist();}});
  divider.addEventListener("keydown",e=>{if(divider.getAttribute("aria-disabled")==="true"||!["ArrowUp","ArrowDown"].includes(e.key))return;e.preventDefault();ratio=Math.max(.25,Math.min(.78,ratio+(e.key==="ArrowUp"?-.03:.03)));draw();persist();});
  for(const id of ["map-panel","profile-panel","sounding-panel"]){
    const el=document.getElementById(id),head=el.querySelector("[data-window-handle]"),initial=defaults(id),valid=saved[id];
    if(valid&&["x","y","w","h"].every(k=>Number.isFinite(valid[k]))){for(const k of ["x","y","w","h"])initial[k]=valid[k];for(const k of ["locked","minimised","docked"])if(typeof valid[k]==="boolean")initial[k]=valid[k];}
    const p={...initial,id,el};panels.set(id,p);
    if(id!=="map-panel"){const body=document.createElement("div");body.className="window-body";for(const c of [...el.children])if(c!==head)body.append(c);el.append(body);}
    head.classList.add("window-drag-handle");head.tabIndex=0;head.title="Drag or use arrow keys to move an unlocked window";
    const controls=document.createElement("span");controls.className="window-controls";
    function button(text,label,action){const b=document.createElement("button");b.type="button";b.className="window-button";b.textContent=text;b.setAttribute("aria-label",label);b.title=label;b.onclick=action;controls.append(b);return b;}
    p.lock=button("Lock","Lock or unlock "+id.replace("-panel","")+" panel",()=>{p.locked=!p.locked;draw();persist();});
    button("Dock","Dock "+id.replace("-panel","")+" panel",()=>{maximised=null;p.docked=true;p.minimised=false;draw();persist();});
    p.max=button("+","Maximise "+id.replace("-panel","")+" panel",()=>{maximised=maximised===id?null:id;draw();});
    button("−","Minimise "+id.replace("-panel","")+" panel",()=>minimise(p));head.append(controls);
    for(const edge of ["n","s","e","w","ne","nw","se","sw"]){const r=document.createElement("button");r.type="button";r.className="resize-edge resize-"+edge+(edge==="se"?" window-resize":"");r.setAttribute("aria-label","Resize "+id.replace("-panel","")+" panel"+(edge==="se"?"":" "+edge+" edge"));r.tabIndex=edge==="se"?0:-1;el.append(r);gesture(p,r,edge);}
    gesture(p,head,null);el.addEventListener("pointerdown",()=>focus(p));
  }
  document.querySelectorAll("[data-window-open]").forEach(b=>b.addEventListener("click",()=>{const p=panels.get(b.dataset.windowOpen);if(!p.el.hidden)minimise(p);else show(p.id);}));
  document.getElementById("reset-layout").onclick=reset;
  function reflow(){if(mobile()!==wasMobile){wasMobile=mobile();if(wasMobile)mobileActive="map-panel";closeDrawer();}draw();}
  window.addEventListener("resize",reflow);if(window.ResizeObserver)new ResizeObserver(reflow).observe(area);
  const workspace=document.getElementById("workspace"),toggle=document.getElementById("sidebar-toggle"),sidebar=document.getElementById("flight-sidebar"),backdrop=document.getElementById("sidebar-backdrop");
  let desktopHidden=false;try{desktopHidden=localStorage.getItem("balloon-predictor-sidebar-hidden")==="true";}catch{}
  function sidebarState(hidden){workspace.classList.toggle("sidebar-hidden",hidden);toggle.textContent=hidden?"› Controls":"‹ Controls";toggle.setAttribute("aria-expanded",String(!hidden));toggle.setAttribute("aria-label",hidden?"Show flight controls":"Hide flight controls");sidebar.inert=hidden;document.querySelector(".visuals").inert=mobile()&&!hidden;backdrop.hidden=!mobile()||hidden;setTimeout(change,240);}
  function closeDrawer(){sidebarState(mobile()?true:desktopHidden);}
  toggle.onclick=()=>{const hidden=!workspace.classList.contains("sidebar-hidden");if(!mobile()){desktopHidden=hidden;try{localStorage.setItem("balloon-predictor-sidebar-hidden",String(hidden));}catch{}}sidebarState(hidden);if(!hidden&&mobile())sidebar.querySelector("select,input,button")?.focus();};
  backdrop.onclick=closeDrawer;document.addEventListener("keydown",e=>{if(e.key==="Escape"&&mobile()){closeDrawer();toggle.focus();}});
  document.addEventListener("bp:predictionstarted",()=>{if(mobile())closeDrawer();});
  closeDrawer();draw();
  window.BalloonWindows={show,reset,minimise:id=>{const p=panels.get(id);if(p)minimise(p);}};
})();
