/* Deterministic pointer/keyboard regression for the actual window manager. */
const fs=require('fs'),path=require('path'),assert=require('node:assert/strict'),{JSDOM}=require('jsdom');
const root=path.resolve(__dirname,'..'),html=fs.readFileSync(path.join(root,'frontend/index.html'),'utf8').replace(/<script[^>]+src=[^>]+><\/script>/g,'');
let checks=0;
function rig(layout,old=false){
 const dom=new JSDOM(html,{url:'http://localhost/',runScripts:'outside-only',pretendToBeVisual:true}),w=dom.window;let phone=false,width=1000,height=700;
 Object.defineProperty(w.HTMLElement.prototype,'clientWidth',{get(){return this.id==='window-area'?width:400;}});
 Object.defineProperty(w.HTMLElement.prototype,'clientHeight',{get(){return this.id==='window-area'?height:40;}});
 w.matchMedia=()=>({matches:phone});const $=id=>w.document.getElementById(id),area=$('window-area');
 area.getBoundingClientRect=()=>({left:320,top:120,width,height,right:320+width,bottom:120+height});
 if(layout)w.localStorage.setItem('balloon-predictor-window-layout-v1.'+(old?'3':'4'),JSON.stringify(layout));
 w.eval(fs.readFileSync(path.join(root,'frontend/ui.js'),'utf8'));w.eval(fs.readFileSync(path.join(root,'frontend/windows.js'),'utf8'));
 function pointer(node,type,x,y,extra={}){const e=new w.MouseEvent(type,{bubbles:true,button:0,clientX:320+x,clientY:120+y,...extra});Object.defineProperty(e,'pointerId',{value:1});node.dispatchEvent(e);}
 function geom(node){return Object.fromEntries(['left','top','width','height'].map(k=>[k,parseFloat(node.style[k])]));}
 function drag(id,points,extra={}){const p=$(id),head=p.querySelector('[data-window-handle]'),g=geom(p);pointer(head,'pointerdown',g.left+Math.min(150,g.width/2),g.top+20);for(const[x,y]of points)pointer(head,'pointermove',x,y,extra);const last=points.at(-1);pointer(head,'pointerup',...last,extra);return p;}
 function arrange(id,zone){const p=$(id);p.querySelector('[aria-haspopup=menu]').click();p.querySelector('[data-arrange="'+zone+'"]').click();return p;}
 return {dom,w,$,pointer,geom,drag,arrange,mobile(v){phone=v;w.dispatchEvent(new w.Event('resize'));},resize(x,y){width=x;height=y;w.dispatchEvent(new w.Event('resize'));}};
}
function check(name,fn){const r=rig();try{fn(r);checks++;}catch(e){e.message=name+': '+e.message;throw e;}finally{r.dom.window.close();}}
check('Renamed headings, no numbers, controls positions',({$})=>{
 assert.match($('profile-panel').textContent,/FLIGHT DETAILS/);assert.match($('sounding-panel').textContent,/FLIGHT PROFILE/);
 assert.ok($('sidebar-toggle').closest('#flight-sidebar'));assert.ok($('sidebar-reopen').closest('.map-toolbar'));
 assert.ok(![...$('workspace').querySelectorAll('.eyebrow')].some(n=>/^0[1-4] \//.test(n.textContent)));
});
check('Maximise, correct two-box restore label, visibility restoration',({$,geom})=>{
 const p=$('profile-panel'),map=$('map-panel'),before=geom(p);p.querySelector('[aria-label="Maximise Flight Details panel"]').click();
 assert.equal(map.hidden,true);assert.equal(p.dataset.placement,'maximised');
 const restore=p.querySelector('[aria-label="Unmaximise Flight Details panel"]');assert.ok(restore.querySelector('svg'));assert.equal(restore.title,'Unmaximise Flight Details panel');
 restore.click();assert.deepEqual(geom(p),before);assert.equal(map.hidden,false);
});
check('Half / quarter pointer snapping respects workspace offset',({$,drag,geom})=>{
 const p=$('sounding-panel');$('restore-sounding').disabled=false;$('restore-sounding').click();
 drag('sounding-panel',[[990,350]]);assert.equal(p.dataset.placement,'right');assert.deepEqual(geom(p),{left:504,top:8,width:488,height:684});
 drag('sounding-panel',[[995,5]]);assert.equal(p.dataset.placement,'top-right');assert.deepEqual(geom(p),{left:504,top:8,width:488,height:338});
 drag('sounding-panel',[[5,695]]);assert.equal(p.dataset.placement,'bottom-left');assert.deepEqual(geom(p),{left:8,top:354,width:488,height:338});
});
check('Top edge maximises; dragging away restores floating size',({$,drag,geom})=>{
 $('restore-sounding').disabled=false;$('restore-sounding').click();const p=$('sounding-panel'),before=geom(p);
 drag('sounding-panel',[[500,3]]);assert.equal(p.dataset.placement,'maximised');
 drag('sounding-panel',[[500,260]]);assert.equal(p.dataset.placement,'floating');assert.equal(geom(p).width,before.width);assert.equal(geom(p).height,before.height);
});
check('Preview and Alt free movement',({$,pointer,geom})=>{
 const p=$('profile-panel'),head=p.querySelector('[data-window-handle]'),g=geom(p);
 pointer(head,'pointerdown',g.left+150,g.top+20);pointer(head,'pointermove',995,350);
 const preview=$('window-area').querySelector('.window-snap-preview');assert.equal(preview.hidden,false);assert.equal(preview.dataset.zone,'right');
 pointer(head,'pointermove',995,350,{altKey:true});assert.equal(preview.hidden,true);pointer(head,'pointerup',995,350,{altKey:true});assert.equal(p.dataset.placement,'floating');
});
check('Locks prevent drag and resize; menus remain explicit',({$,drag,geom,arrange,pointer})=>{
 const p=arrange('profile-panel','right');p.querySelector('.window-button').click();const before=geom(p);drag('profile-panel',[[5,20]]);assert.deepEqual(geom(p),before);
 const east=p.querySelector('.resize-e');pointer(east,'pointerdown',990,350);pointer(east,'pointermove',800,350);pointer(east,'pointerup',800,350);assert.deepEqual(geom(p),before);
 arrange('profile-panel','left');assert.equal(p.dataset.placement,'left');
});
check('Arrange menu and keyboard placement; no Dock button',({$,w,arrange})=>{
 const p=$('profile-panel'),button=p.querySelector('[aria-haspopup=menu]');assert.ok(![...p.querySelectorAll('.window-button')].some(b=>b.textContent==='Dock'));
 button.click();assert.equal(button.getAttribute('aria-expanded'),'true');assert.equal(w.document.activeElement.dataset.arrange,'left');
 p.querySelector('[role=menu]').dispatchEvent(new w.KeyboardEvent('keydown',{key:'End',bubbles:true}));assert.equal(w.document.activeElement.dataset.arrange,'stack');
 w.document.activeElement.dispatchEvent(new w.KeyboardEvent('keydown',{key:'Escape',bubbles:true}));assert.equal(p.querySelector('[role=menu]').hidden,true);
 arrange('profile-panel','top-right');assert.equal(p.dataset.placement,'top-right');arrange('profile-panel','stack');assert.equal(p.dataset.placement,'stack');
 const head=p.querySelector('[data-window-handle]');head.dispatchEvent(new w.KeyboardEvent('keydown',{key:'ArrowLeft',altKey:true,bubbles:true}));assert.equal(p.dataset.placement,'left');
});
check('Tiling scales on viewport resize and a click does not detach',({$,arrange,geom,resize,pointer})=>{
 const p=arrange('profile-panel','right');resize(1200,800);assert.deepEqual(geom(p),{left:604,top:8,width:588,height:784});
 const h=p.querySelector('[data-window-handle]');pointer(h,'pointerdown',700,28);pointer(h,'pointerup',700,28);assert.equal(p.dataset.placement,'right');
});
check('Divider and visible toolbar buttons toggle',({$,w,geom})=>{
 const map=$('map-panel'),details=$('profile-panel'),g=geom(map),divider=$('window-area').querySelector('.dock-divider');
 divider.dispatchEvent(new w.KeyboardEvent('keydown',{key:'ArrowUp',bubbles:true}));assert.ok(geom(map).height<g.height);
 const button=w.document.querySelector('[data-window-open="profile-panel"]');button.click();assert.equal(details.hidden,true);button.click();assert.equal(details.hidden,false);
});
check('Mobile left drawer, backdrop, focus and single main panel',({$,w,mobile})=>{
 mobile(true);assert.equal($('sidebar-reopen').hidden,false);assert.equal($('sidebar-toggle').getAttribute('aria-expanded'),'false');
 $('sidebar-reopen').click();assert.equal($('sidebar-backdrop').hidden,false);assert.equal(w.document.activeElement,$('sidebar-toggle'));assert.equal(w.document.querySelector('.visuals').inert,true);
 $('sidebar-toggle').click();assert.equal($('sidebar-backdrop').hidden,true);assert.equal(w.document.activeElement,$('sidebar-reopen'));
 w.document.querySelector('[data-window-open="profile-panel"]').click();assert.equal($('map-panel').hidden,true);assert.equal($('profile-panel').hidden,false);
 $('sidebar-reopen').click();w.document.dispatchEvent(new w.KeyboardEvent('keydown',{key:'Escape',bubbles:true}));assert.equal($('sidebar-backdrop').hidden,true);
});
const floating={ratio:.62,'map-panel':{x:50,y:100,w:300,h:300,locked:false,minimised:false,tile:null},'profile-panel':{x:500,y:100,w:300,h:300,locked:false,minimised:false,tile:null},'sounding-panel':{x:300,y:30,w:400,h:400,locked:false,minimised:true,tile:null}};
for(const mode of ['move','resize']){
 const r=rig(floating);try{
  const p=r.$('map-panel'),handle=p.querySelector(mode==='move'?'[data-window-handle]':'.resize-e'),startX=mode==='move'?200:350;
  r.pointer(handle,'pointerdown',startX,120);r.pointer(handle,'pointermove',startX+140,120);
  let g=r.geom(p);assert.equal(mode==='move'?g.left:g.left+g.width,mode==='move'?192:492);
  r.pointer(handle,'pointermove',startX+148,120);g=r.geom(p);assert.equal(mode==='move'?g.left:g.left+g.width,mode==='move'?192:492);
  r.pointer(handle,'pointermove',startX+175,120);g=r.geom(p);assert.equal(mode==='move'?g.left:g.left+g.width,mode==='move'?225:525);
  r.pointer(handle,'pointerup',startX+175,120);checks++;
 }finally{r.dom.window.close();}
}
{
 const old={ratio:.55,'sounding-panel':{x:100,y:40,w:510,h:420,locked:true,minimised:false,docked:false}};const r=rig(old,true);
 try{assert.deepEqual(r.geom(r.$('sounding-panel')),{left:100,top:40,width:510,height:420});assert.equal(r.$('sounding-panel').dataset.locked,'true');assert.equal(r.$('sounding-panel').hidden,false);checks++;}finally{r.dom.window.close();}
}
console.log(`Window interaction regression: PASS · ${checks} scenarios (halves/quarters, magnets/release, controls, menus, locks, restore, mobile, migration)`);
