/* SVG diagnostics: hPa, °C, m AMSL, m/s. Wind barbs use 5/10/50 knots. */
"use strict";
window.BalloonPlots=(()=>{
  const NS="http://www.w3.org/2000/svg",controllers=new WeakMap();
  const PRESSURES=[1100,1050,1000,950,925,900,850,800,750,700,650,600,550,500,450,400,350,300,250,200,150,100,70,50,30,20,10,7,5,3,2,1,.5];
  const BOX={skew:{width:720,left:62,right:592,top:38,bottom:550},wind:{width:440,left:62,right:359,top:38,bottom:550}};
  const finite=Number.isFinite,clamp=(x,a,b)=>Math.max(a,Math.min(b,x));
  function el(name,attrs={},text){const node=document.createElementNS(NS,name);for(const[k,v]of Object.entries(attrs))node.setAttribute(k,v);if(text!==undefined)node.textContent=text;return node;}
  function label(svg,x,y,text,attrs={}){svg.append(el("text",{x,y,fill:"#63766d","font-size":11,"font-family":"system-ui",...attrs},text));}
  function line(svg,x1,y1,x2,y2,attrs={}){svg.append(el("line",{x1,y1,x2,y2,...attrs}));}
  function curve(group,rows,X,Y,key,color){let d="",connected=false;for(const r of rows){if(!finite(r[key])||!finite(r.pressure_hpa)||r.pressure_hpa<=0){connected=false;continue;}const x=X(r[key],r.pressure_hpa),y=Y(r.pressure_hpa);if(!finite(x+y)){connected=false;continue;}d+=(connected?"L":"M")+x.toFixed(3)+","+y.toFixed(3);connected=true;}group.append(el("path",{d,fill:"none",stroke:color,"stroke-width":2.5,"stroke-linecap":"round","stroke-linejoin":"round","data-series":key}));}
  function moistLapse(pressures,temperature){
    // RK4 integration of the liquid-saturated pseudo-adiabatic dT/dln(p).
    const slope=(t,p)=>{const e=611.2*Math.exp(17.67*(t-273.15)/(t-29.65));if(e>=p*100||t<=100)return NaN;const r=.622*e/(p*100-e);return(287.05*t+2.5e6*r)/(1004+(2.5e6)**2*r*.622/(287.05*t*t));};
    let t=temperature;const values=[t];for(let i=1;i<pressures.length;i++){const a=Math.log(pressures[i-1]),b=Math.log(pressures[i]),count=Math.max(1,Math.ceil(Math.abs(b-a)/.008)),h=(b-a)/count;for(let j=0;j<count;j++){const l=a+j*h,k1=slope(t,Math.exp(l)),k2=slope(t+h*k1/2,Math.exp(l+h/2)),k3=slope(t+h*k2/2,Math.exp(l+h/2)),k4=slope(t+h*k3,Math.exp(l+h));t+=h*(k1+2*k2+2*k3+k4)/6;}values.push(t);}return values;
  }
  function interpolate(pairs,x){
    if(!pairs.length||x<pairs[0][0]||x>pairs.at(-1)[0])return null;
    for(let i=1;i<pairs.length;i++){const[a,b]=[pairs[i-1],pairs[i]];if(x<=b[0]&&b[0]>a[0])return a[1]+(b[1]-a[1])*(x-a[0])/(b[0]-a[0]);}
    return pairs.at(-1)[1];
  }
  function heightMapping(rows){
    const valid=rows.filter(r=>finite(r.altitude)&&r.pressure_hpa>0&&finite(r.pressure_hpa));
    const heights=valid.map(r=>[r.altitude,Math.log(r.pressure_hpa)]).sort((a,b)=>a[0]-b[0]);
    const pressures=valid.map(r=>[Math.log(r.pressure_hpa),r.altitude]).sort((a,b)=>a[0]-b[0]);
    return{height:p=>p>0?interpolate(pressures,Math.log(p)):null,pressure:h=>{const v=interpolate(heights,h);return v===null?null:Math.exp(v);},min:heights[0]?.[0],max:heights.at(-1)?.[0]};
  }
  function barbParts(u,v){const knots=Math.hypot(u,v)*3600/1852,rounded=Math.round(knots/5)*5;return{knots,flags:Math.floor(rounded/50),full:Math.floor(rounded%50/10),half:rounded%10>=5,calm:rounded===0,direction:(Math.atan2(-u,-v)*180/Math.PI+360)%360};}
  function barb(svg,x,y,r){
    const parts=barbParts(r.u,r.v),speed=Math.hypot(r.u,r.v),g=el("g",{"data-wind-barb":"","data-pressure":r.pressure_hpa,stroke:"#365b50","stroke-width":1.5,fill:"#365b50"});
    g.append(el("title",{},r.pressure_hpa.toFixed(1)+" hPa · "+speed.toFixed(1)+" m/s ("+parts.knots.toFixed(0)+" kt) · from "+parts.direction.toFixed(0)+"°"));
    if(parts.calm)g.append(el("circle",{cx:x,cy:y,r:3,fill:"none"}));
    else{
      const length=29,dx=-r.u/speed,dy=r.v/speed,flip=r.latitude<0?-1:1,nx=-dy*flip,ny=dx*flip;
      line(g,x,y,x+dx*length,y+dy*length);let at=length;
      const spacing=Math.min(4.5,(length-4)/Math.max(1,parts.flags*1.8+parts.full+(parts.half?.7:0))),flagStep=spacing*1.8;
      for(let i=0;i<parts.flags;i++){const a=[x+dx*at,y+dy*at],b=[a[0]+nx*10-dx*flagStep*.6,a[1]+ny*10-dy*flagStep*.6],c=[a[0]-dx*flagStep*.75,a[1]-dy*flagStep*.75];g.append(el("polygon",{points:[a,b,c].map(p=>p.join(",")).join(" ")}));at-=flagStep;}
      if(parts.half&&!parts.full&&!parts.flags)at-=4;
      for(let i=0;i<parts.full;i++){line(g,x+dx*at,y+dy*at,x+dx*(at+spacing)+nx*10,y+dy*(at+spacing)+ny*10);at-=spacing;}
      if(parts.half)line(g,x+dx*at,y+dy*at,x+dx*(at+2)+nx*5,y+dy*(at+2)+ny*5);
    }
    svg.append(g);
  }
  function niceStep(span,count=7){const raw=Math.max(.001,span/count),power=10**Math.floor(Math.log10(raw));return[1,2,5,10].map(v=>v*power).find(v=>v>=raw)||10*power;}
  function ticks(a,b,count=7){const step=niceStep(b-a,count),out=[];for(let t=Math.ceil(a/step)*step;t<=b+step*1e-6;t+=step)out.push(Math.abs(t)<step*1e-8?0:t);return out;}
  function geometry(kind,state){const b=BOX[kind],H=b.bottom-b.top,W=b.right-b.left,span=state.logBottom-state.logTop;
    const Y=p=>b.top+(Math.log(p)-state.logTop)/span*H;
    const X=(value,p)=>b.left+(value-state.x0)/(state.x1-state.x0)*W+(kind==="skew"?Y(1000)-Y(p):0);
    return{...b,W,H,X,Y,pressure:y=>Math.exp(state.logTop+(y-b.top)/H*span)};
  }
  function render(profile,skew,wind){
    controllers.get(skew)?.dispose();
    const rows=profile.rows||[],valid=rows.filter(r=>r.pressure_hpa>0&&finite(r.pressure_hpa)),mapping=heightMapping(rows),events=[];
    if(!valid.length){for(const s of[skew,wind]){s.replaceChildren();label(s,30,50,"No valid pressure samples.");}return null;}
    const pmin=Math.min(...valid.map(r=>r.pressure_hpa)),pmax=Math.max(...valid.map(r=>r.pressure_hpa));
    const logTop=Math.log(Math.max(.05,pmin*.92)),logBottom=Math.log(Math.ceil(pmax*1.02/25)*25);
    const windMax=Math.max(10,Math.ceil(Math.max(0,...rows.flatMap(r=>[r.u,r.v]).filter(finite).map(Math.abs))/10)*10);
    const states={skew:{x0:-60,x1:50,logTop,logBottom},wind:{x0:-windMax,x1:windMax,logTop,logBottom}};
    // Fit the transformed traces. The skew is a fixed 45° screen transform
    // anchored at 1000 hPa; pressure is always logarithmic in both panels.
    for(let i=0;i<40;i++){
      const s=states.skew,g=geometry("skew",s),scale=(s.x1-s.x0)/g.W;
      const values=valid.flatMap(r=>[r.temperature_c,r.dewpoint_c].filter(finite).map(t=>t+(g.Y(1000)-g.Y(r.pressure_hpa))*scale));
      if(!values.length)break;
      const lo=Math.floor((Math.min(...values)-8)/10)*10,hi=Math.ceil((Math.max(...values)+8)/10)*10;
      if(lo>=s.x0&&hi<=s.x1)break;
      const width=Math.max(90,hi-lo);s.x0=lo;s.x1=lo+width;
    }
    const initial={skew:{...states.skew},wind:{...states.wind}},linked=document.getElementById("link-profile-zoom");
    const floor=Math.log(Math.min(.1,pmin*.1)),ceiling=Math.log(Math.max(1100,pmax*1.1));
    function listen(node,event,callback,options){node.addEventListener(event,callback,options);events.push(()=>node.removeEventListener(event,callback,options));}
    function vertical(s){const size=clamp(s.logBottom-s.logTop,.05,ceiling-floor);s.logBottom=s.logTop+size;if(s.logTop<floor){s.logTop=floor;s.logBottom=floor+size;}if(s.logBottom>ceiling){s.logBottom=ceiling;s.logTop=ceiling-size;}}
    function share(kind){if(!linked||linked.checked){const other=states[kind==="skew"?"wind":"skew"];other.logTop=states[kind].logTop;other.logBottom=states[kind].logBottom;}}
    function draw(kind){
      const svg=kind==="skew"?skew:wind,s=states[kind],g=geometry(kind,s),{X,Y,left,right,top,bottom}=g;
      svg.replaceChildren();svg.setAttribute("viewBox","0 0 "+g.width+" 620");svg.dataset.pressureTop=Math.exp(s.logTop);svg.dataset.pressureBottom=Math.exp(s.logBottom);svg.dataset.xMin=s.x0;svg.dataset.xMax=s.x1;
      const uid="plot-clip-"+svg.id,defs=el("defs"),clip=el("clipPath",{id:uid});clip.append(el("rect",{x:left,y:top,width:g.W,height:g.H}));defs.append(clip);svg.append(defs);
      svg.append(el("rect",{x:left,y:top,width:g.W,height:g.H,fill:"#fcfdfb",stroke:"#b6c8be"}));
      const data=el("g",{"clip-path":"url(#"+uid+")"});svg.append(data);
      const pBottom=Math.exp(s.logBottom),pTop=Math.exp(s.logTop);
      const candidates=[...new Set([1000,850,700,500,400,300,250,200,150,100,70,50,30,20,10,5,2,1,.5,...PRESSURES,...(s.logBottom-s.logTop<.7?ticks(pTop,pBottom):[])])];
      const visible=[];for(const p of candidates)if(Y(p)>=top&&Y(p)<=bottom&&visible.every(q=>Math.abs(Y(q)-Y(p))>=24))visible.push(p);
      let last=-Infinity;
      for(const p of visible){const y=Y(p);line(data,left,y,right,y,{stroke:"#dce5df","data-isobar":p});label(svg,left-9,y+4,String(Number(p.toFixed(2))),{"text-anchor":"end","data-pressure-tick":p});}
      const heights=ticks(mapping.height(pBottom)??mapping.min??0,mapping.height(pTop)??mapping.max??0,7);
      for(const h of heights){const p=mapping.pressure(h);if(p===null||Y(p)<top||Y(p)>bottom)continue;line(svg,right,Y(p),right+5,Y(p),{stroke:"#8a9f94"});label(svg,right+9,Y(p)+4,String(Number((h/1000).toFixed(2))),{"data-altitude-tick":h,"data-pressure":p});}
      label(svg,17,(top+bottom)/2,"Pressure (hPa)",{transform:"rotate(-90 17 "+((top+bottom)/2)+")","text-anchor":"middle","font-size":12});
      const ax=right+52;label(svg,ax,(top+bottom)/2,"Altitude (km AMSL)",{transform:"rotate(-90 "+ax+" "+((top+bottom)/2)+")","text-anchor":"middle","font-size":11});
      if(kind==="skew"){
        const temperatureLeft=s.x0-(Y(1000)-Y(pBottom))/g.W*(s.x1-s.x0),temperatureRight=temperatureLeft+s.x1-s.x0;
        for(const t of ticks(temperatureLeft-250,temperatureRight,30))line(data,X(t,pBottom),bottom,X(t,pTop),top,{stroke:t===0?"#a6bdb0":"#d9e1db","stroke-dasharray":t===0?"5,3":"","data-isotherm":t});
        for(const t of ticks(temperatureLeft,temperatureRight,8))label(svg,X(t,pBottom),bottom+23,String(t),{"text-anchor":"middle"});
        const pressures=Array.from({length:110},(_,i)=>pBottom*Math.exp(-Math.log(pBottom/pTop)*i/109));
        const guide=(points,stroke,dash)=>data.append(el("polyline",{points:points.filter(p=>p.every(finite)).map(p=>p.join(",")).join(" "),fill:"none",stroke,"stroke-width":.8,"stroke-dasharray":dash||""}));
        for(let theta=220;theta<=620;theta+=20)guide(pressures.map(p=>[X(theta*(p/1000)**.2854-273.15,p),Y(p)]),"#d6c8a2","5,4");
        for(let t=-20;t<=45;t+=5){const temp=moistLapse([1000,...pressures],t+273.15).slice(1);guide(pressures.map((p,i)=>[X(temp[i]-273.15,p),Y(p)]),"#bcd2d7","3,4");}
        for(const mixing of [.1,.2,.4,1,2,4,8,16,32])guide(pressures.filter(p=>p>=400).map(p=>{const r=mixing/1000,e=r*p*100/(.622+r),l=Math.log(e/611.2);return[X(243.5*l/(17.67-l),p),Y(p)];}),"#b9d2b5","2,4");
        curve(data,rows,X,Y,"temperature_c","#d75b2c");curve(data,rows,X,Y,"dewpoint_c","#28645a");
        label(svg,(left+right)/2,592,"Temperature (°C) at "+Number(pBottom.toFixed(0))+" hPa",{"text-anchor":"middle","font-size":12});
        label(svg,left,20,"SKEW-T / LOG-P · 45°",{"font-size":10,"letter-spacing":1});label(svg,679,20,"WIND · kt",{"text-anchor":"middle","font-size":9});
        last=-Infinity;
        for(const r of valid.filter(r=>finite(r.u)&&finite(r.v)).sort((a,b)=>Y(a.pressure_hpa)-Y(b.pressure_hpa))){const y=Y(r.pressure_hpa);if(y<top+30||y>bottom-30||y-last<28)continue;barb(svg,681,y,r);last=y;}
        if(!profile.humidity_available)label(svg,right-8,top+18,"Humidity unavailable",{"text-anchor":"end"});
      }else{
        for(const w of ticks(s.x0,s.x1,6)){const x=X(w,1000);line(data,x,top,x,bottom,{stroke:w===0?"#93aaa0":"#e4ebe5","stroke-dasharray":w===0?"4,4":""});label(svg,x,bottom+23,String(w),{"text-anchor":"middle"});}
        curve(data,rows,X,Y,"u","#337fba");curve(data,rows,X,Y,"v","#7056a0");
        label(svg,(left+right)/2,592,"Wind component (m/s)",{"text-anchor":"middle","font-size":12});label(svg,left,20,"WIND COMPONENTS",{"font-size":10,"letter-spacing":1});
      }
    }
    function drawAll(){draw("skew");draw("wind");skew.dispatchEvent(new CustomEvent("bp:plotchange",{bubbles:true}));}
    function zoom(kind,factor,x,y){
      const s=states[kind],g=geometry(kind,s),p=g.pressure(y),fraction=(y-g.top)/g.H,span=(s.logBottom-s.logTop)*factor;
      const value=s.x0+(x-g.left-(kind==="skew"?g.Y(1000)-y:0))/g.W*(s.x1-s.x0),range=clamp((s.x1-s.x0)*factor,2,600);
      s.logTop=Math.log(p)-fraction*span;s.logBottom=s.logTop+span;vertical(s);
      const next=geometry(kind,s);s.x0=value-(x-g.left-(kind==="skew"?next.Y(1000)-next.Y(p):0))/g.W*range;s.x1=s.x0+range;
      share(kind);drawAll();
    }
    function pan(kind,dx,dy){const s=states[kind],g=geometry(kind,s),range=s.x1-s.x0,delta=dy/g.H*(s.logBottom-s.logTop);s.x0-=dx/g.W*range;s.x1-=dx/g.W*range;s.logTop-=delta;s.logBottom-=delta;vertical(s);share(kind);drawAll();}
    function reset(kind){Object.assign(states[kind],initial[kind]);share(kind);drawAll();}
    if(linked)listen(linked,"change",()=>{if(linked.checked){share("skew");drawAll();}});
    for(const[kind,svg]of[["skew",skew],["wind",wind]]){
      svg.classList.add("interactive-profile");svg.tabIndex=0;svg.setAttribute("aria-label",(kind==="skew"?"Skew-T temperature, dew point and wind barbs":"Eastward and northward wind components")+"; pressure left, altitude right. Scroll or pinch to zoom, drag to pan, double-click to reset.");
      const host=svg.parentElement;host.querySelector(".plot-toolbar")?.remove();const bar=document.createElement("div");bar.className="plot-toolbar";
      for(const[value,text,title]of[["in","+","Zoom in"],["out","−","Zoom out"],["reset","Reset","Reset view"]]){const b=document.createElement("button");b.type="button";b.className="button quiet";b.textContent=text;b.dataset.plotAction=value;b.setAttribute("aria-label",title+" "+(kind==="skew"?"Skew-T":"winds"));listen(b,"click",()=>{const g=geometry(kind,states[kind]);if(value==="reset")reset(kind);else zoom(kind,value==="in"?.75:1/.75,(g.left+g.right)/2,(g.top+g.bottom)/2);});bar.append(b);}const hint=document.createElement("span");hint.textContent="Scroll / pinch · drag to pan";bar.append(hint);host.insertBefore(bar,svg);
      function point(e){const r=svg.getBoundingClientRect(),scale=Math.min(r.width/BOX[kind].width,r.height/620)||1;return{x:(e.clientX-r.left-(r.width-BOX[kind].width*scale)/2)/scale,y:(e.clientY-r.top-(r.height-620*scale)/2)/scale};}
      function inside(p){const b=BOX[kind];return p.x>=b.left&&p.x<=b.right&&p.y>=b.top&&p.y<=b.bottom;}
      listen(svg,"wheel",e=>{const p=point(e);if(!inside(p))return;e.preventDefault();zoom(kind,Math.exp(clamp(e.deltaY,-100,100)*.003),p.x,p.y);},{passive:false});
      const pointers=new Map();let previous=null;
      const center=()=>{const a=[...pointers.values()];return{x:a.reduce((s,p)=>s+p.x,0)/a.length,y:a.reduce((s,p)=>s+p.y,0)/a.length,distance:a.length>1?Math.hypot(a[0].x-a[1].x,a[0].y-a[1].y):0};};
      listen(svg,"pointerdown",e=>{const p=point(e);if(e.button!==0||!inside(p))return;e.preventDefault();svg.setPointerCapture?.(e.pointerId);pointers.set(e.pointerId,p);previous=center();svg.classList.add("plot-panning");});
      listen(svg,"pointermove",e=>{if(!pointers.has(e.pointerId))return;pointers.set(e.pointerId,point(e));const next=center();if(previous){if(next.distance&&previous.distance)zoom(kind,previous.distance/next.distance,next.x,next.y);pan(kind,next.x-previous.x,next.y-previous.y);}previous=next;});
      const finish=e=>{pointers.delete(e.pointerId);previous=pointers.size?center():null;if(!previous)svg.classList.remove("plot-panning");};
      for(const event of["pointerup","pointercancel","lostpointercapture"])listen(svg,event,finish);
      listen(svg,"dblclick",()=>reset(kind));listen(svg,"keydown",e=>{if(["+","=","-","0","Home"].includes(e.key)){e.preventDefault();const g=geometry(kind,states[kind]);if(e.key==="0"||e.key==="Home")reset(kind);else zoom(kind,e.key==="-"?1/.75:.75,(g.left+g.right)/2,(g.top+g.bottom)/2);}else if(e.key.startsWith("Arrow")){e.preventDefault();pan(kind,e.key==="ArrowLeft"?20:e.key==="ArrowRight"?-20:0,e.key==="ArrowUp"?20:e.key==="ArrowDown"?-20:0);}});
    }
    const controller={reset,states,geometry:kind=>geometry(kind,states[kind]),dispose(){events.forEach(remove=>remove());}};controllers.set(skew,controller);drawAll();return controller;
  }
  return{render,moistLapse,barbParts,heightMapping};
})();
