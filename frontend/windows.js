/* v1.4 window workspace: floating geometry, edge tiling and gentle alignment. */
(() => {
  "use strict";
  const $=id=>document.getElementById(id),area=$("window-area"),workspace=$("workspace");
  const key="balloon-predictor-window-layout-v1.4",panels=new Map();
  const mobile=()=>matchMedia("(max-width: 760px), (max-height: 520px) and (pointer: coarse)").matches;
  const zones={left:"Left half",right:"Right half",top:"Top half",bottom:"Bottom half","top-left":"Top-left quarter","top-right":"Top-right quarter","bottom-left":"Bottom-left quarter","bottom-right":"Bottom-right quarter",full:"Maximise"};
  const tiles=new Set(["stack",...Object.keys(zones).filter(x=>x!=="full")]);
  const restoreIcon='<svg viewBox="0 0 18 18" width="15" height="15" aria-hidden="true"><path d="M6 6V3h9v9h-3M3 6h9v9H3Z" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"/></svg>';
  let saved={},z=10,ratio=.62,maximised=null,mobileActive="map-panel",wasMobile=mobile();
  try{
    saved=JSON.parse(localStorage.getItem(key))||{};
    if(!Object.keys(saved).length){
      const old=JSON.parse(localStorage.getItem("balloon-predictor-window-layout-v1.3"))||{};
      for(const[id,value]of Object.entries(old))saved[id]=id==="ratio"?value:{...value,tile:value.docked?"stack":null};
    }
    if(Number.isFinite(saved.ratio))ratio=Math.max(.25,Math.min(.78,saved.ratio));
  }catch{}
  const change=()=>document.dispatchEvent(new CustomEvent("bp:layout"));
  function dimensions(){return {w:Math.max(1,area.clientWidth||800),h:Math.max(1,area.clientHeight||600)};}
  function defaults(id){const{w,h}=dimensions();return {x:Math.max(8,w-Math.min(710,w-32)-16),y:24,w:Math.min(710,w-32),h:Math.min(660,h-40),locked:false,minimised:id==="sounding-panel",tile:id==="sounding-panel"?null:"stack"};}
  function persist(){const state={ratio};for(const[id,p]of panels)state[id]={x:p.x,y:p.y,w:p.w,h:p.h,locked:p.locked,minimised:p.minimised,tile:p.tile};try{localStorage.setItem(key,JSON.stringify(state));}catch{}}
  function clamp(g){
    const{w,h}=dimensions(),minW=Math.min(240,Math.max(1,w-12)),minH=Math.min(160,Math.max(1,h-12));
    const width=Math.min(Math.max(minW,g.w),Math.max(1,w-12)),height=Math.min(Math.max(minH,g.h),Math.max(1,h-12));
    return {x:Math.max(6,Math.min(g.x,w-width-6)),y:Math.max(6,Math.min(g.y,h-height-6)),w:width,h:height};
  }
  function tileGeometry(zone){
    const{w,h}=dimensions(),gap=8,width=w-16,height=h-16,halfW=(width-gap)/2,halfH=(height-gap)/2;
    if(zone==="full")return {x:6,y:6,w:Math.max(1,w-12),h:Math.max(1,h-12)};
    const right=zone.includes("right"),bottom=zone.includes("bottom");
    if(zone.includes("-"))return {x:right?16+halfW:8,y:bottom?16+halfH:8,w:Math.max(1,halfW),h:Math.max(1,halfH)};
    if(zone==="left"||zone==="right")return {x:right?16+halfW:8,y:8,w:Math.max(1,halfW),h:Math.max(1,height)};
    return {x:8,y:bottom?16+halfH:8,w:Math.max(1,width),h:Math.max(1,halfH)};
  }
  function draw(){
    const{w,h}=dimensions(),map=panels.get("map-panel"),details=panels.get("profile-panel");
    const split=!mobile()&&!maximised&&map?.tile==="stack"&&details?.tile==="stack"&&!map.minimised&&!details.minimised;
    for(const[id,p]of panels){
      const isMax=maximised===id;
      p.el.hidden=mobile()?mobileActive!==id:p.minimised||Boolean(maximised&&!isMax);
      let g;
      if(mobile())g={x:0,y:0,w,h};
      else if(isMax)g=tileGeometry("full");
      else if(p.tile==="stack"){
        const cut=Math.round((h-24)*ratio);
        g=split?{x:8,y:id==="map-panel"?8:cut+16,w:Math.max(1,w-16),h:Math.max(1,id==="map-panel"?cut:h-cut-24)}:{x:8,y:8,w:Math.max(1,w-16),h:Math.max(1,h-16)};
      }else g=p.tile?tileGeometry(p.tile):clamp(p);
      p.rendered=g;
      Object.assign(p.el.style,{left:g.x+"px",top:g.y+"px",width:g.w+"px",height:g.h+"px"});
      p.el.classList.toggle("window-locked",p.locked);p.el.classList.toggle("window-maximised",isMax);
      p.el.dataset.locked=String(p.locked);p.el.dataset.placement=isMax?"maximised":p.tile||"floating";
      p.lock.textContent=p.locked?"Unlock":"Lock";p.lock.setAttribute("aria-pressed",String(p.locked));
      p.lock.title=(p.locked?"Unlock ":"Lock ")+p.title+" position and size";
      p.lock.setAttribute("aria-label",p.lock.title);
      if(p.max.dataset.state!==String(isMax)){p.max.innerHTML=isMax?restoreIcon:"+";p.max.dataset.state=String(isMax);}
      const label=(isMax?"Unmaximise ":"Maximise ")+p.title+" panel";
      p.max.title=label;p.max.setAttribute("aria-label",label);p.max.setAttribute("aria-pressed",String(isMax));
      for(const button of document.querySelectorAll('[data-window-open="'+id+'"]'))button.setAttribute("aria-pressed",String(!p.el.hidden));
    }
    divider.hidden=!split;divider.style.top=(Math.round((h-24)*ratio)+8)+"px";
    divider.setAttribute("aria-valuenow",String(Math.round(ratio*100)));
    divider.setAttribute("aria-disabled",String(Boolean(map?.locked||details?.locked)));
    change();
  }
  function focus(p){p.el.style.zIndex=String(++z);}
  function closeMenus(){for(const p of panels.values()){p.menu.hidden=true;p.arrange.setAttribute("aria-expanded","false");}}
  function show(id){const p=panels.get(id);if(!p)return;maximised=null;p.minimised=false;if(mobile())mobileActive=id;focus(p);draw();persist();document.dispatchEvent(new CustomEvent("bp:panelshown",{detail:{id}}));}
  function minimise(p){if(maximised===p.id)maximised=null;if(mobile())mobileActive=null;else p.minimised=true;closeMenus();draw();persist();}
  function maximise(p){closeMenus();maximised=maximised===p.id?null:p.id;p.minimised=false;focus(p);draw();}
  function reset(){maximised=null;ratio=.62;mobileActive="map-panel";closeMenus();hidePreview();for(const[id,p]of panels)Object.assign(p,defaults(id));draw();persist();}
  function arrange(p,zone){
    closeMenus();
    if(zone==="full"){maximise(p);return;}
    maximised=null;p.minimised=false;
    if(zone==="stack"){
      for(const id of ["map-panel","profile-panel"]){const other=panels.get(id);other.tile="stack";other.minimised=false;}
    }else if(zone==="float"){
      if(p.tile){const g=p.rendered;p.w=Math.min(p.w,g.w);p.h=Math.min(p.h,g.h);p.x=g.x+14;p.y=g.y+14;}p.tile=null;
    }else p.tile=zone;
    focus(p);draw();persist();
  }
  const preview=document.createElement("div");preview.className="window-snap-preview";preview.hidden=true;preview.setAttribute("aria-hidden","true");area.append(preview);
  function hidePreview(){preview.hidden=true;delete preview.dataset.zone;}
  function showPreview(zone){
    if(!zone){hidePreview();return;}
    const g=tileGeometry(zone);Object.assign(preview.style,{left:g.x+"px",top:g.y+"px",width:g.w+"px",height:g.h+"px"});preview.dataset.zone=zone;preview.textContent=zones[zone];preview.hidden=false;
  }
  function edgeZone(e){
    if(e.altKey)return null;
    const r=area.getBoundingClientRect(),{w,h}=dimensions(),x=e.clientX-r.left,y=e.clientY-r.top;
    if(x < -40||x > w+40||y < -40||y > h+40)return null;
    const near=22,cx=Math.min(100,w*.16),cy=Math.min(90,h*.2);
    const l=x<=near,rr=x>=w-near,t=y<=near,b=y>=h-near;
    if((l&&y<cy)||(t&&x<cx))return "top-left";
    if((rr&&y<cy)||(t&&x>w-cx))return "top-right";
    if((l&&y>h-cy)||(b&&x<cx))return "bottom-left";
    if((rr&&y>h-cy)||(b&&x>w-cx))return "bottom-right";
    return l?"left":rr?"right":t?"full":b?"bottom":null;
  }
  // Magnets use the unsnapped pointer position, so pulling another 18 px always
  // releases alignment. Alt bypasses both magnets and edge tiling for free movement.
  function magnet(raw,targets,state,key,bypass){
    if(bypass){delete state[key];return raw;}
    if(state[key]!==undefined){if(Math.abs(raw-state[key])<=18)return state[key];delete state[key];}
    const nearest=targets.reduce((best,v)=>Math.abs(raw-v)<Math.abs(raw-best)?v:best,Infinity);
    if(Math.abs(raw-nearest)<=7){state[key]=nearest;return nearest;}return raw;
  }
  function targets(p,g,edge){
    const{w,h}=dimensions(),xt=[6,w-6],yt=[6,h-6];
    for(const q of panels.values()){
      if(q===p||q.el.hidden)continue;const r=q.rendered;
      if(g.y < r.y+r.h+18&&g.y+g.h > r.y-18)xt.push(r.x-8,r.x+r.w+8,r.x,r.x+r.w);
      if(g.x < r.x+r.w+18&&g.x+g.w > r.x-18)yt.push(r.y-8,r.y+r.h+8,r.y,r.y+r.h);
    }
    if(!edge)return {x:xt.flatMap(v=>[v,v-g.w]),y:yt.flatMap(v=>[v,v-g.h])};
    return {x:xt,y:yt};
  }
  function gesture(p,handle,edge){
    let start=null;
    handle.addEventListener("pointerdown",e=>{
      if(p.locked||mobile()||e.button!==0||(edge&&maximised)||(!edge&&e.target.closest("button,a,input,select,label,summary")))return;
      closeMenus();e.preventDefault();focus(p);handle.setPointerCapture?.(e.pointerId);
      start={cx:e.clientX,cy:e.clientY,g:{...p.rendered},tile:p.tile,max:maximised===p.id,moved:false,zone:null};
    });
    handle.addEventListener("pointermove",e=>{
      if(!start)return;
      if(!start.moved){
        if(Math.hypot(e.clientX-start.cx,e.clientY-start.cy)<3)return;
        start.moved=true;maximised=null;
        if(edge){Object.assign(p,start.g);p.tile=null;}
        else if(start.tile||start.max){
          const r=area.getBoundingClientRect(),fraction=Math.max(.05,Math.min(.95,(start.cx-r.left-start.g.x)/start.g.w));
          const height=Math.min(p.h,dimensions().h-24),width=Math.min(p.w,dimensions().w-24);
          Object.assign(p,{x:start.cx-r.left-width*fraction,y:start.cy-r.top-Math.min(24,start.cy-r.top-start.g.y),w:width,h:height});p.tile=null;
        }
        start.base={x:p.x,y:p.y,w:p.w,h:p.h};p.el.classList.add("window-moving");draw();
      }
      const dx=e.clientX-start.cx,dy=e.clientY-start.cy,s=start.base;
      if(!edge){
        const raw={x:s.x+dx,y:s.y+dy,w:s.w,h:s.h},t=targets(p,raw,null);
        p.x=magnet(raw.x,t.x,start,"mx",e.altKey);p.y=magnet(raw.y,t.y,start,"my",e.altKey);
        start.zone=edgeZone(e);showPreview(start.zone);
      }else{
        const minW=Math.min(240,dimensions().w-12),minH=Math.min(160,dimensions().h-12),right=s.x+s.w,bottom=s.y+s.h;
        let left=s.x,top=s.y,r=right,b=bottom;
        if(edge.includes("e"))r=Math.max(left+minW,right+dx);
        if(edge.includes("s"))b=Math.max(top+minH,bottom+dy);
        if(edge.includes("w"))left=Math.min(right-minW,s.x+dx);
        if(edge.includes("n"))top=Math.min(bottom-minH,s.y+dy);
        const t=targets(p,{x:left,y:top,w:r-left,h:b-top},edge);
        if(edge.includes("e"))r=Math.max(left+minW,magnet(r,t.x,start,"mx",e.altKey));
        if(edge.includes("w"))left=Math.min(right-minW,magnet(left,t.x,start,"mx",e.altKey));
        if(edge.includes("s"))b=Math.max(top+minH,magnet(b,t.y,start,"my",e.altKey));
        if(edge.includes("n"))top=Math.min(bottom-minH,magnet(top,t.y,start,"my",e.altKey));
        Object.assign(p,{x:left,y:top,w:r-left,h:b-top});
      }
      draw();
    });
    function finish(cancelled=false){
      if(!start)return;
      if(start.moved){
        Object.assign(p,p.rendered);
        if(!edge&&!cancelled&&start.zone){if(start.zone==="full")maximised=p.id;else p.tile=start.zone;}
      }
      start=null;p.el.classList.remove("window-moving");hidePreview();draw();persist();
    }
    handle.addEventListener("pointerup",()=>finish());handle.addEventListener("pointercancel",()=>finish(true));handle.addEventListener("lostpointercapture",()=>finish(true));
    handle.addEventListener("keydown",e=>{
      if(p.locked||mobile()||e.target!==handle||!e.key.startsWith("Arrow"))return;
      if(e.altKey&&!edge){e.preventDefault();arrange(p,{ArrowLeft:"left",ArrowRight:"right",ArrowUp:"full",ArrowDown:"bottom"}[e.key]);return;}
      if(maximised)return;
      e.preventDefault();Object.assign(p,p.rendered);p.tile=null;
      const step=e.shiftKey?30:10,dx=e.key==="ArrowLeft"?-step:e.key==="ArrowRight"?step:0,dy=e.key==="ArrowUp"?-step:e.key==="ArrowDown"?step:0;
      if(edge){p.w+=dx;p.h+=dy;}else{p.x+=dx;p.y+=dy;}draw();Object.assign(p,p.rendered);persist();
    });
  }
  const divider=document.createElement("div");divider.className="dock-divider";divider.tabIndex=0;divider.setAttribute("role","separator");divider.setAttribute("aria-label","Map and Details split; unlock both panels to resize");divider.setAttribute("aria-orientation","horizontal");divider.setAttribute("aria-valuemin","25");divider.setAttribute("aria-valuemax","78");divider.title="Drag to change Map / Details split (unlock both panels)";area.append(divider);
  let splitting=false;
  divider.addEventListener("pointerdown",e=>{if(e.button!==0||[...panels.values()].some(p=>p.tile==="stack"&&p.locked))return;e.preventDefault();divider.setPointerCapture?.(e.pointerId);splitting=true;});
  divider.addEventListener("pointermove",e=>{if(!splitting)return;const r=area.getBoundingClientRect();ratio=Math.max(.25,Math.min(.78,(e.clientY-r.top-8)/(dimensions().h-24)));draw();});
  for(const event of ["pointerup","pointercancel","lostpointercapture"])divider.addEventListener(event,()=>{if(splitting){splitting=false;persist();}});
  divider.addEventListener("keydown",e=>{if(divider.getAttribute("aria-disabled")==="true"||!["ArrowUp","ArrowDown"].includes(e.key))return;e.preventDefault();ratio=Math.max(.25,Math.min(.78,ratio+(e.key==="ArrowUp"?-.03:.03)));draw();persist();});
  for(const id of ["map-panel","profile-panel","sounding-panel"]){
    const el=$(id),head=el.querySelector("[data-window-handle]"),initial=defaults(id),valid=saved[id];
    if(valid&&["x","y","w","h"].every(k=>Number.isFinite(valid[k]))){
      for(const k of ["x","y","w","h"])initial[k]=valid[k];for(const k of ["locked","minimised"])if(typeof valid[k]==="boolean")initial[k]=valid[k];
      if(valid.tile===null||tiles.has(valid.tile))initial.tile=valid.tile;
    }
    const p={...initial,id,el,title:el.dataset.windowTitle};panels.set(id,p);
    if(id!=="map-panel"){const body=document.createElement("div");body.className="window-body";for(const child of [...el.children])if(child!==head)body.append(child);el.append(body);}
    head.classList.add("window-drag-handle");head.tabIndex=0;head.title="Drag to move; drag to workspace edges for halves or quarters. Hold Alt for free movement.";
    const controls=document.createElement("span");controls.className="window-controls";
    function button(text,label,action){const b=document.createElement("button");b.type="button";b.className="window-button";b.textContent=text;b.setAttribute("aria-label",label);b.title=label;b.onclick=action;controls.append(b);return b;}
    p.lock=button("Lock","Lock "+p.title+" position and size",()=>{p.locked=!p.locked;draw();persist();});
    p.menu=document.createElement("div");p.menu.className="window-arrange-menu";p.menu.id=id+"-arrange";p.menu.hidden=true;p.menu.setAttribute("role","menu");p.menu.setAttribute("aria-label","Arrange "+p.title);
    p.arrange=button("Arrange ▾","Arrange "+p.title+": halves, quarters or free floating",()=>{const open=p.menu.hidden;closeMenus();p.menu.hidden=!open;p.arrange.setAttribute("aria-expanded",String(open));if(open)p.menu.querySelector("button").focus();});
    p.arrange.setAttribute("aria-haspopup","menu");p.arrange.setAttribute("aria-controls",p.menu.id);p.arrange.setAttribute("aria-expanded","false");
    const options={...zones,float:"Float window",...(id!=="sounding-panel"?{stack:"Stack Map & Details"}:{})};
    for(const[zone,label]of Object.entries(options)){const b=document.createElement("button");b.type="button";b.setAttribute("role","menuitem");b.dataset.arrange=zone;b.textContent=label;b.onclick=()=>{arrange(p,zone);p.arrange.focus();};p.menu.append(b);}
    p.menu.addEventListener("keydown",e=>{const buttons=[...p.menu.querySelectorAll("button")],i=buttons.indexOf(document.activeElement);if(e.key==="Escape"){closeMenus();p.arrange.focus();}else if(["ArrowDown","ArrowUp","Home","End"].includes(e.key)){e.preventDefault();buttons[e.key==="Home"?0:e.key==="End"?buttons.length-1:(i+(e.key==="ArrowDown"?1:-1)+buttons.length)%buttons.length].focus();}});
    p.max=button("+","Maximise "+p.title+" panel",()=>maximise(p));
    button("−","Minimise "+p.title+" panel",()=>minimise(p));head.append(controls);el.append(p.menu);
    for(const edge of ["n","s","e","w","ne","nw","se","sw"]){const r=document.createElement("button");r.type="button";r.className="resize-edge resize-"+edge+(edge==="se"?" window-resize":"");r.setAttribute("aria-label","Resize "+p.title+" panel"+(edge==="se"?"":" "+edge+" edge"));r.tabIndex=edge==="se"?0:-1;el.append(r);gesture(p,r,edge);}
    gesture(p,head,null);head.addEventListener("dblclick",e=>{if(!mobile()&&!e.target.closest("button,a,input,select,label"))maximise(p);});el.addEventListener("pointerdown",()=>focus(p));
  }
  document.addEventListener("pointerdown",e=>{if(!e.target.closest(".window-arrange-menu,.window-controls"))closeMenus();});
  document.querySelectorAll("[data-window-open]").forEach(b=>b.addEventListener("click",()=>{const p=panels.get(b.dataset.windowOpen);if(!p.el.hidden)minimise(p);else show(p.id);}));
  $("reset-layout").onclick=reset;
  function reflow(){if(mobile()!==wasMobile){wasMobile=mobile();if(wasMobile)mobileActive="map-panel";closeDrawer();}hidePreview();closeMenus();draw();}
  window.addEventListener("resize",reflow);if(window.ResizeObserver)new ResizeObserver(reflow).observe(area);
  const toggle=$("sidebar-toggle"),reopen=$("sidebar-reopen"),sidebar=$("flight-sidebar"),backdrop=$("sidebar-backdrop");
  let desktopHidden=false;try{desktopHidden=localStorage.getItem("balloon-predictor-sidebar-hidden")==="true";}catch{}
  function sidebarState(hidden){
    const wasInside=sidebar.contains(document.activeElement);
    workspace.classList.toggle("sidebar-hidden",hidden);toggle.setAttribute("aria-expanded",String(!hidden));reopen.hidden=!hidden;reopen.setAttribute("aria-expanded",String(!hidden));
    sidebar.inert=hidden;document.querySelector(".visuals").inert=mobile()&&!hidden;backdrop.hidden=!mobile()||hidden;
    if(hidden&&wasInside)reopen.focus();setTimeout(change,240);
    document.dispatchEvent(new CustomEvent("bp:sidebar",{detail:{hidden}}));
  }
  function closeDrawer(){sidebarState(mobile()?true:desktopHidden);}
  toggle.onclick=()=>{if(!mobile()){desktopHidden=true;try{localStorage.setItem("balloon-predictor-sidebar-hidden","true");}catch{}}sidebarState(true);reopen.focus();};
  reopen.onclick=()=>{if(!mobile()){desktopHidden=false;try{localStorage.setItem("balloon-predictor-sidebar-hidden","false");}catch{}}sidebarState(false);toggle.focus();};
  backdrop.onclick=closeDrawer;
  document.addEventListener("keydown",e=>{if(e.key==="Escape"){closeMenus();hidePreview();if(mobile()){closeDrawer();reopen.focus();}}});
  document.addEventListener("bp:predictionstarted",()=>{if(mobile())closeDrawer();});
  closeDrawer();draw();
  window.BalloonWindows={show,reset,minimise:id=>{const p=panels.get(id);if(p)minimise(p);}};
})();
