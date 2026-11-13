/* One row of trajectory tools; the same controls move to More at narrow widths. */
(() => {
  "use strict";
  const toolbar=document.querySelector(".map-toolbar");
  if(!toolbar)return;
  const $=id=>document.getElementById(id);
  const buttons=$("trajectory-inline"), menu=$("trajectory-menu"), more=$("trajectory-more");
  const tools=[
    [$("trajectory-panels"),280],[$("trajectory-reset"),715],[$("trajectory-toggles"),625],[$("trajectory-actions"),880]
  ];
  function open(value,restore=false){
    menu.hidden=!value;more.setAttribute("aria-expanded",String(value));
    if(restore&&!more.hidden)more.focus();
  }
  function layout(width){
    if(!width)return;
    const focused=document.activeElement;
    const focusWasInMenu=menu.contains(focused);
    for(const [item,threshold] of tools){
      const parent=width>=threshold?buttons:menu;
      if(item.parentElement!==parent){
        parent.append(item);
        if(item.contains(focused)&&!parent.hidden)focused.focus();
      }
    }
    // Preserve reading order as groups move between containers.
    for(const parent of [buttons,menu]){
      const order=tools.map(([item])=>item).filter(item=>item.parentElement===parent);
      order.forEach((item,i)=>{if(parent.children[i]!==item)parent.insertBefore(item,parent.children[i]);});
    }
    more.hidden=!menu.childElementCount;
    if(more.hidden)open(false);
    if(!menu.hidden&&focusWasInMenu&&buttons.contains(focused))focused.focus();
    else if(menu.hidden&&menu.contains(focused))more.focus();
  }
  more.onclick=()=>open(menu.hidden);
  more.onkeydown=event=>{
    if(event.key==="ArrowDown"){event.preventDefault();open(true);menu.querySelector("button:not(:disabled)")?.focus();}
  };
  document.addEventListener("pointerdown",event=>{if(!menu.hidden&&!menu.contains(event.target)&&!more.contains(event.target))open(false);});
  document.addEventListener("keydown",event=>{if(event.key==="Escape"&&!menu.hidden){event.preventDefault();open(false,true);}});
  menu.addEventListener("click",event=>{
    if(event.target.closest("button:not([role=switch])"))open(false,true);
  });
  if(window.ResizeObserver)new ResizeObserver(([entry])=>layout(entry.contentRect.width)).observe(toolbar);
  else window.addEventListener("resize",()=>layout(toolbar.clientWidth));
  layout(toolbar.clientWidth);

  const switches={
    "track-display":["ascent","both"],
    "endpoint-focus":["burst","landing"],
    "sensitivity-display":["members","region"]
  };
  for(const [id,values] of Object.entries(switches)){
    const group=$(id),button=group.querySelector("button");
    group.value=group.dataset.value;
    group.set=(value,disabled=false)=>{
      group.value=value;group.dataset.value=value;
      button.setAttribute("aria-checked",String(value===values[1]));
      button.disabled=disabled;
      const selected=group.querySelector(value===values[1]?"[data-right]":"[data-left]").getAttribute("aria-label");
      group.title=group.getAttribute("aria-label")+": "+selected+(disabled?" · only applies to full flights":"");
      button.title=group.title;
    };
    button.onclick=()=>{
      group.set(group.value===values[0]?values[1]:values[0]);
      group.dispatchEvent(new Event("change"));
    };
    group.set(group.value);
  }
})();
