/* SVG diagnostics with a skewed temperature axis and logarithmic pressure.
   Dry adiabats use Poisson's equation. No parcel/CAPE diagnostics are implied. */
"use strict";
window.BalloonPlots=(()=>{
  const ns="http://www.w3.org/2000/svg";
  function el(name,attrs,text){const e=document.createElementNS(ns,name);for(const[k,v]of Object.entries(attrs))e.setAttribute(k,v);if(text!==undefined)e.textContent=text;return e;}
  function line(svg,x1,y1,x2,y2,attrs={}){svg.append(el("line",{x1,y1,x2,y2,...attrs}));}
  function label(svg,x,y,text,attrs={}){svg.append(el("text",{x,y,fill:"#6c7776","font-size":11,"font-family":"system-ui",...attrs},text));}
  function path(svg,rows,x,y,color,key){let d="",connected=false;for(const row of rows){if(!Number.isFinite(row[key])){connected=false;continue;}const a=x(row),b=y(row);if(!Number.isFinite(a+b)){connected=false;continue;}d+=(connected?"L":"M")+a.toFixed(2)+","+b.toFixed(2);connected=true;}svg.append(el("path",{d,fill:"none",stroke:color,"stroke-width":2.7,"stroke-linejoin":"round","stroke-linecap":"round"}));}
  function moistLapse(pressures,temperature){
    // Integrate dT/dln(p) with RK4. Liquid-water Magnus saturation, fixed Lv.
    const slope=(t,p)=>{const e=611.2*Math.exp(17.67*(t-273.15)/(t-29.65)),r=.622*e/(p*100-e);return(287.05*t+2.5e6*r)/(1004+(2.5e6)**2*r*.622/(287.05*t*t));};
    let t=temperature;const values=[t];for(let i=1;i<pressures.length;i++){const a=Math.log(pressures[i-1]),b=Math.log(pressures[i]),count=Math.max(1,Math.ceil(Math.abs(b-a)/.01)),h=(b-a)/count;for(let j=0;j<count;j++){const l=a+j*h,k1=slope(t,Math.exp(l)),k2=slope(t+h*k1/2,Math.exp(l+h/2)),k3=slope(t+h*k2/2,Math.exp(l+h/2)),k4=slope(t+h*k3,Math.exp(l+h));t+=h*(k1+2*k2+2*k3+k4)/6;}values.push(t);}return values;
  }
  function skewt(profile,svg){
    svg.replaceChildren();const rows=profile.rows.filter(r=>r.pressure_hpa>0&&Number.isFinite(r.temperature_c));if(!rows.length)return;
    const left=58,right=498,top=25,bottom=445;
    const low=Math.max(1050,Math.ceil(Math.max(...rows.map(r=>r.pressure_hpa))/50)*50), high=Math.max(1,Math.min(100,Math.min(...rows.map(r=>r.pressure_hpa))*.85));
    const ratio=Math.log(low/high),Y=p=>bottom-Math.log(low/p)/ratio*(bottom-top);
    const cold=Math.floor(Math.min(-50,...rows.map(r=>r.temperature_c))/10)*10-10;
    const warm=Math.ceil(Math.max(40,...rows.map(r=>r.temperature_c))/10)*10+10, skew=.55;
    const X=(t,p)=>left+(t-cold)/(warm-cold)*(right-left)+skew*(bottom-Y(p));
    const uid="clip-skew-"+(svg.id||"chart");const defs=el("defs",{}),clip=el("clipPath",{id:uid});clip.append(el("rect",{x:left,y:top,width:right-left,height:bottom-top}));defs.append(clip);svg.append(defs);
    svg.append(el("rect",{x:left,y:top,width:right-left,height:bottom-top,fill:"#fcfdfa",stroke:"#d6dfda"}));
    const group=el("g",{"clip-path":"url(#"+uid+")"});svg.append(group);
    for(const p of [1000,925,850,700,600,500,400,300,250,200,150,100,70,50,30,20,10,5,2,1])if(p<=low&&p>=high){line(group,left,Y(p),right,Y(p),{stroke:"#e2e8e4"});label(svg,left-9,Y(p)+4,String(p),{"text-anchor":"end"});}
    for(let t=cold-100;t<=warm;t+=10){line(group,X(t,low),bottom,X(t,high),top,{stroke:t===0?"#93aaa0":"#e8ece8","stroke-dasharray":t===0?"4,4":""});if(t>=cold)label(svg,X(t,low),bottom+19,String(t),{"text-anchor":"middle"});}
    for(let theta=230;theta<=480;theta+=20){const points=[];for(let i=0;i<=80;i++){const p=low*Math.exp(-ratio*i/80),t=theta*Math.pow(p/1000,.2854)-273.15;points.push(X(t,p)+","+Y(p));}group.append(el("polyline",{points:points.join(" "),fill:"none",stroke:"#d8d6bc","stroke-width":.8,"stroke-dasharray":"3,4"}));}
    const guides=Array.from({length:120},(_,i)=>low*Math.exp(-Math.log(low/Math.max(high,100))*i/119));
    for(let start=-10;start<=40;start+=10){const temperatures=moistLapse(guides,start+273.15);group.append(el("polyline",{points:guides.map((p,i)=>X(temperatures[i]-273.15,p)+","+Y(p)).join(" "),fill:"none",stroke:"#cbdce2","stroke-width":.8}));}
    for(const mixing of [.4,1,2,4,8,16]){const points=[];for(const p of guides){if(p<400)continue;const r=mixing/1000,e=r*p*100/(.622+r),l=Math.log(e/611.2),t=243.5*l/(17.67-l);points.push(X(t,p)+","+Y(p));}group.append(el("polyline",{points:points.join(" "),fill:"none",stroke:"#cddfc7","stroke-width":.8,"stroke-dasharray":"2,4"}));}
    path(group,rows,r=>X(r.temperature_c,r.pressure_hpa),r=>Y(r.pressure_hpa),"#d75b2c","temperature_c");
    path(group,rows,r=>X(r.dewpoint_c,r.pressure_hpa),r=>Y(r.pressure_hpa),"#28645a","dewpoint_c");
    label(svg,(left+right)/2,490,"Temperature (°C) · pressure (hPa)",{"text-anchor":"middle","font-size":12});
    label(svg,left,14,"Skew-T / log-P · dry, saturated & mixing-ratio guides",{"font-size":9});
    if(!profile.humidity_available)label(svg,right-8,top+19,"Humidity unavailable",{"text-anchor":"end","font-size":11});
  }
  function uv(profile,svg){
    svg.replaceChildren();const rows=profile.rows.filter(r=>Number.isFinite(r.u)&&Number.isFinite(r.v)&&Number.isFinite(r.altitude));if(!rows.length)return;
    const left=56,right=341,top=25,bottom=445;const maxH=Math.max(1000,Math.ceil(Math.max(...rows.map(r=>r.altitude))/1000)*1000),minH=Math.min(0,...rows.map(r=>r.altitude));
    const maxW=Math.max(5,Math.ceil(Math.max(...rows.flatMap(r=>[Math.abs(r.u),Math.abs(r.v)]))/5)*5);
    const X=w=>left+(w+maxW)/(2*maxW)*(right-left),Y=h=>bottom-(h-minH)/(maxH-minH)*(bottom-top);
    svg.append(el("rect",{x:left,y:top,width:right-left,height:bottom-top,fill:"#fcfdfa",stroke:"#d6dfda"}));
    for(let i=0;i<=5;i++){const h=minH+(maxH-minH)*i/5;line(svg,left,Y(h),right,Y(h),{stroke:"#e2e8e4"});label(svg,left-8,Y(h)+4,(h/1000).toFixed(1),{"text-anchor":"end"});const w=-maxW+2*maxW*i/5;line(svg,X(w),top,X(w),bottom,{stroke:"#eef1ed"});label(svg,X(w),bottom+19,w.toFixed(0),{"text-anchor":"middle"});}
    line(svg,X(0),top,X(0),bottom,{stroke:"#9ba99e","stroke-dasharray":"4,4"});
    path(svg,rows,r=>X(r.u),r=>Y(r.altitude),"#337fba","u");path(svg,rows,r=>X(r.v),r=>Y(r.altitude),"#7056a0","v");
    label(svg,(left+right)/2,490,"Wind component (m/s)",{"text-anchor":"middle","font-size":12});label(svg,left,14,"Altitude (km AMSL)",{"font-size":10});
  }
  function render(profile,skew,wind){skewt(profile,skew);uv(profile,wind);}
  return{render,moistLapse};
})();
