/* Weather is a snapshot of the saved flight, never the current setup form. */
(() => {
  "use strict";
  const radians=Math.PI/180,earth=6371008.8;
  const mercator=lat=>Math.log(Math.tan(Math.PI/4+lat*radians/2));
  const inverseMercator=y=>(2*Math.atan(Math.exp(y))-Math.PI/2)/radians;
  const near=(lon,centre)=>centre+((lon-centre+180)%360+360)%360-180;
  function sampleGrid(data,lat,lon,fields){
    const g=data.grid,ys=g.latitude,xs=g.longitude;
    lon=near(lon,(xs[0]+xs.at(-1))/2);
    const y=(lat-ys[0])/(ys.at(-1)-ys[0])*(ys.length-1),x=(lon-xs[0])/(xs.at(-1)-xs[0])*(xs.length-1);
    if(x< -1e-8||x>xs.length-1+1e-8||y< -1e-8||y>ys.length-1+1e-8)return null;
    const ix=Math.min(xs.length-2,Math.max(0,Math.floor(x))),iy=Math.min(ys.length-2,Math.max(0,Math.floor(y)));
    const fx=Math.max(0,Math.min(1,x-ix)),fy=Math.max(0,Math.min(1,y-iy));
    const nodes=[[iy,ix,(1-fy)*(1-fx)],[iy,ix+1,(1-fy)*fx],[iy+1,ix,fy*(1-fx)],[iy+1,ix+1,fy*fx]];
    const result=[];
    for(const name of fields){let value=0;for(const[row,col,weight]of nodes){if(weight<=1e-12)continue;const v=g[name][row][col];if(v===null||!Number.isFinite(v))return null;value+=weight*v;}result.push(value);}
    return result;
  }
  function step(lat,lon,u,v,seconds){
    return [lat+v*seconds/earth/radians,lon+u*seconds/(earth*Math.max(.01,Math.cos(lat*radians)))/radians];
  }
  const colours={temperature_c:[[45,66,145],[55,157,205],[131,214,191],[247,227,138],[239,142,72],[177,41,56]],
    signed:[[41,79,161],[115,176,218],[239,243,231],[237,163,111],[183,51,55]],wind_speed:[[224,239,220],[119,199,180],[43,151,164],[49,92,174],[139,70,159],[218,94,57]]};
  function colour(stops,fraction){const p=Math.max(0,Math.min(.999999,fraction))*(stops.length-1),i=Math.floor(p),f=p-i;return stops[i].map((v,k)=>Math.round(v+(stops[i+1][k]-v)*f));}
  const labels={temperature_c:"Temperature · °C",u:"u · eastward wind · m/s",v:"v · northward wind · m/s",wind_speed:"Wind speed · m/s"};
  const formatTime=value=>new Date(value).toISOString().replace("T"," ").replace(/\.\d{3}Z$/," UTC");
  function quantile(sorted,q){if(!sorted.length)return NaN;const at=(sorted.length-1)*q,i=Math.floor(at);return sorted[i]+(sorted[Math.min(i+1,sorted.length-1)]-sorted[i])*(at-i);}
  function niceRange(low,high,signed=false){
    if(signed){high=Math.max(Math.abs(low),Math.abs(high),.1);low=-high;}
    if(!(high>low)){const pad=Math.max(.5,Math.abs(low)*.02);low-=pad;high+=pad;}
    const rough=(high-low)/6,power=10**Math.floor(Math.log10(rough)),step=([1,2,2.5,5,10].find(v=>v*power>=rough)||10)*power;
    return [Math.floor(low/step)*step,Math.ceil(high/step)*step];
  }
  function bufferedBounds(map){
    const b=map.getBounds(),height=Math.max(.01,b.getNorth()-b.getSouth()),width=Math.min(360,Math.max(.01,b.getEast()-b.getWest())),padY=Math.max(.25,height*.4),padX=Math.max(.25,width*.4);
    let south=Math.max(-85,b.getSouth()-padY),north=Math.min(85,b.getNorth()+padY);if(north<=south){north=Math.min(85,north);south=north-.25;}
    const span=Math.min(360,width+2*padX),centre=near(map.getCenter().lng,0),west=centre-span/2;
    return [Math.min(85,Math.ceil(north*1e4)/1e4),Math.floor(west*1e4)/1e4,Math.max(-85,Math.floor(south*1e4)/1e4),(span>=359.9999?Math.floor(west*1e4)/1e4+360:Math.ceil((west+span)*1e4)/1e4)];
  }
  function containsView(area,map){
    const[n,w,s,e]=area,b=map.getBounds(),west=near(b.getWest(),(w+e)/2),east=west+Math.min(360,b.getEast()-b.getWest());
    return Math.min(85,b.getNorth())<=n+1e-5&&Math.max(-85,b.getSouth())>=s-1e-5&&(e-w>=359.99||west>=w-1e-5&&east<=e+1e-5);
  }
  function regional(info){const a=info?.area;if(!a||!a.every(Number.isFinite))return false;const[n,w,s,e]=a,width=e>=w?e-w:e+360-w;return n-s<=40&&width>0&&width<=80;}
  const number=value=>Number(Number(value).toPrecision(6)).toString();
  function create(map,api,hooks={}){
    const $=id=>document.getElementById(id),panel=$("weather-panel"),status=$("weather-overlay-status"),show=$("weather-overlay-show"),field=$("weather-overlay-field"),wind=$("weather-overlay-wind"),opacity=$("weather-overlay-opacity"),time=$("weather-overlay-time"),levelMode=$("weather-level-mode"),levelValue=$("weather-level-value"),rangeMode=$("weather-range-mode"),rangeMin=$("weather-range-min"),rangeMax=$("weather-range-max");
    let selected=null,enabled=false,data=null,area=null,token=0,loadedKey=null,timer=null,mapTimer=null,renderFrame=null,image=null,flow=null,activeLoads=0;
    const cache=new Map(),pending=new Set(),coverageHints=new Map(),rangeSettings=new Map();let scale=[0,30];
    if(matchMedia("(prefers-reduced-motion: reduce)").matches)wind.checked=false;
    for(const[name,z]of[["weatherFieldPane",320],["weatherWindPane",340]]){const pane=map.createPane(name);pane.style.zIndex=String(z);pane.style.pointerEvents="none";}
    function message(text,loading=false,error=false){status.textContent=text;status.classList.toggle("weather-overlay-loading",loading);status.classList.toggle("error",error);status.setAttribute("aria-busy",String(loading));}
    function clear(){
      for(const name of ["loadedFlight","weatherTime","weatherHeight","weatherPressure","coverageMode"])delete panel.dataset[name];
      if(image){map.removeLayer(image);image=null;}if(flow){map.removeLayer(flow);flow=null;}
      $("weather-overlay-legend").hidden=true;$("weather-overlay-meta").textContent="";$("weather-overlay-warning").textContent="";data=null;loadedKey=null;
    }
    function flightCaption(){
      $("weather-overlay-flight").textContent=selected?selected.flight.request.flight_name+" · "+formatTime(selected.flight.request.launch_datetime):"Calculate a flight to show its weather.";
      if(selected&&levelMode.value==="target")levelValue.value=number(selected.flight.summary.burst.altitude);
    }
    function levelControls(){
      const pressure=levelMode.value==="pressure";levelValue.disabled=levelMode.value==="target";levelValue.min=pressure?"0.01":"-500";levelValue.max=pressure?"1100":"100000";$("weather-level-label").textContent=pressure?"Pressure · hPa":"Altitude · m AMSL";flightCaption();
    }
    function wholeModel(){return selected&&(coverageHints.get(selected.flight.request.dataset)==="model"||regional(selected.flight.weather));}
    function body(){
      if(!selected)return null;
      const value=Number(levelValue.value),mode=levelMode.value;
      if(mode!=="target"&&(!levelValue.value.trim()||!Number.isFinite(value)||(mode==="pressure"?(value<=0||value>1100):(value< -500||value>100000))))throw new Error(mode==="pressure"?"Enter a pressure above 0 and at most 1100 hPa.":"Enter an altitude from −500 to 100,000 m AMSL.");
      if(!wholeModel()&&!area)area=bufferedBounds(map);
      return {flight_index:selected.flightIndex,time_basis:time.value,coverage:"auto",area:wholeModel()?null:area,grid_size:40,...(mode==="pressure"?{pressure_hpa:value}:mode==="altitude"?{altitude_m:value}:{})};
    }
    function key(value=body()){return selected?JSON.stringify([selected.jobId,value]):null;}
    function shiftedBounds(){const[n,w,s,e]=data.bounds,delta=360*Math.round((map.getCenter().lng-(w+e)/2)/360);return [[s,w+delta],[n,e+delta]];}
    function syncVisibility(){
      const visible=Boolean(data&&show.checked);
      if(image){if(visible&&!map.hasLayer(image))image.addTo(map);else if(!visible&&map.hasLayer(image))map.removeLayer(image);}
      if(flow){if(visible&&wind.checked&&!map.hasLayer(flow))flow.addTo(map);else if((!visible||!wind.checked)&&map.hasLayer(flow))map.removeLayer(flow);else flow.resume?.();}
    }
    const Flow=L.Layer.extend({
      onAdd(){this.canvas=L.DomUtil.create("canvas","weather-wind-canvas");this.canvas.setAttribute("aria-hidden","true");map.getPane("weatherWindPane").append(this.canvas);this.ctx=this.canvas.getContext("2d");this.points=[];this.onMove=()=>this.reset();this.onVisible=()=>this.resume();map.on("moveend resize zoomend",this.onMove);map.on("movestart zoomstart",this.pause,this);document.addEventListener("visibilitychange",this.onVisible);document.addEventListener("bp:layout",this.onVisible);this.reset();},
      onRemove(){this.pause();map.off("moveend resize zoomend",this.onMove);map.off("movestart zoomstart",this.pause,this);document.removeEventListener("visibilitychange",this.onVisible);document.removeEventListener("bp:layout",this.onVisible);this.canvas.remove();},
      pause(){cancelAnimationFrame(this.frame);this.frame=null;this.previous=null;},
      visible(){return !document.hidden&&!$("map-panel").hidden&&map.hasLayer(this)&&show.checked&&wind.checked;},
      reset(){this.pause();const size=map.getSize(),ratio=Math.min(2,window.devicePixelRatio||1);this.canvas.width=Math.round(size.x*ratio);this.canvas.height=Math.round(size.y*ratio);this.canvas.style.width=size.x+"px";this.canvas.style.height=size.y+"px";this.ctx.setTransform(ratio,0,0,ratio,0,0);this.size=size;L.DomUtil.setPosition(this.canvas,map.containerPointToLayerPoint([0,0]));this.points=[];this.resume();},
      seed(){const b=map.getBounds(),[n,w,s,e]=data.bounds,west=near(b.getWest(),(w+e)/2),east=west+Math.min(360,b.getEast()-b.getWest());const lowX=Math.max(w,west),highX=Math.min(e,east),lowY=Math.max(s,b.getSouth()),highY=Math.min(n,b.getNorth());if(highX<=lowX||highY<=lowY)return null;for(let i=0;i<15;i++){const p={lat:lowY+Math.random()*(highY-lowY),lon:lowX+Math.random()*(highX-lowX),life:40+Math.random()*65};if(sampleGrid(data,p.lat,p.lon,["u","v"]))return p;}return null;},
      resume(){if(!this.visible()){this.pause();return;}if(!this.frame)this.frame=requestAnimationFrame(t=>this.tick(t));},
      tick(t){this.frame=null;if(!this.visible())return;const dt=Math.min(.08,this.previous?(t-this.previous)/1000:.033);if(this.previous&&t-this.previous<30){this.frame=requestAnimationFrame(x=>this.tick(x));return;}this.previous=t;
        const c=this.ctx,{x:w,y:h}=this.size;c.globalCompositeOperation="destination-in";c.fillStyle="rgba(0,0,0,.89)";c.fillRect(0,0,w,h);c.globalCompositeOperation="source-over";c.strokeStyle="rgba(255,255,248,.86)";c.lineWidth=1.2;c.shadowColor="rgba(25,65,57,.7)";c.shadowBlur=1.5;c.beginPath();
        const count=matchMedia("(max-width:760px)").matches?140:320;while(this.points.length<count){const p=this.seed();if(!p)break;this.points.push(p);}const centre=map.getCenter().lng;
        this.points=this.points.filter(p=>{const uv=sampleGrid(data,p.lat,p.lon,["u","v"]);if(!uv||--p.life<0)return false;const next=step(p.lat,p.lon,...uv,dt*1000);if(!sampleGrid(data,...next,["u","v"]))return false;const a=map.latLngToContainerPoint([p.lat,near(p.lon,centre)]),b=map.latLngToContainerPoint([next[0],near(next[1],centre)]);if(a.x<0||a.y<0||a.x>w||a.y>h)return false;c.moveTo(a.x,a.y);c.lineTo(b.x,b.y);p.lat=next[0];p.lon=next[1];return true;});c.stroke();this.frame=requestAnimationFrame(x=>this.tick(x));}
    });
    function visibleValues(name){
      const values=[],size=map.getSize();
      for(let y=0;y<=size.y;y+=16)for(let x=0;x<=size.x;x+=16){const ll=map.containerPointToLatLng([x,y]),v=sampleGrid(data,ll.lat,ll.lng,[name]);if(v&&Number.isFinite(v[0]))values.push(v[0]);}
      return values;
    }
    function updateScale(){
      const name=field.value;$("weather-range-error").textContent="";
      if(rangeMode.value==="fixed"){
        const low=Number(rangeMin.value),high=Number(rangeMax.value);
        if(!rangeMin.value.trim()||!rangeMax.value.trim()||!Number.isFinite(low)||!Number.isFinite(high)||low>=high){$("weather-range-error").textContent="Colour maximum must be greater than minimum.";return false;}
        scale=[low,high];
      }else{
        let values=rangeMode.value==="view"?visibleValues(name):data.grid[name].flat().filter(v=>v!==null&&Number.isFinite(v));
        if(!values.length){values=data.grid[name].flat().filter(v=>v!==null&&Number.isFinite(v));$("weather-range-error").textContent="No valid weather in the visible window; using full-layer limits.";}
        values.sort((a,b)=>a-b);scale=niceRange(quantile(values,.02),quantile(values,.98),name==="u"||name==="v");
        if(name==="wind_speed")scale[0]=Math.max(0,scale[0]);rangeMin.value=number(scale[0]);rangeMax.value=number(scale[1]);
      }
      rangeSettings.set(name,{mode:rangeMode.value,low:rangeMin.value,high:rangeMax.value});return true;
    }
    function render(){
      if(!data||!updateScale())return;const name=field.value,[low,high]=scale,stops=colours[name]||colours.signed,canvas=document.createElement("canvas"),width=384,height=384;canvas.width=width;canvas.height=height;
      const context=canvas.getContext("2d"),pixels=context.createImageData(width,height),[north,west,south,east]=data.bounds,top=mercator(north),bottom=mercator(south);
      for(let y=0;y<height;y++){const lat=inverseMercator(top+(bottom-top)*y/(height-1));for(let x=0;x<width;x++){const value=sampleGrid(data,lat,west+(east-west)*x/(width-1),[name]);if(!value)continue;const i=(y*width+x)*4;pixels.data.set([...colour(stops,(value[0]-low)/(high-low)),255],i);}}
      context.putImageData(pixels,0,0);const url=canvas.toDataURL();if(image)image.setUrl(url).setBounds(shiftedBounds());else image=L.imageOverlay(url,shiftedBounds(),{pane:"weatherFieldPane",interactive:false,className:"weather-field-image",opacity:Number(opacity.value)/100});
      if(!flow)flow=new Flow();$("weather-overlay-legend").hidden=false;$("weather-legend-title").textContent=labels[name];$("weather-legend-colours").style.background="linear-gradient(to right,"+stops.map(c=>"rgb("+c.join(",")+")").join(",")+")";$("weather-legend-low").textContent=number(low);$("weather-legend-high").textContent=number(high);
      const level=data.pressure_hpa!==null&&data.pressure_hpa!==undefined?number(data.pressure_hpa)+" hPa · model pressure surface":""+Math.round(data.altitude_m).toLocaleString()+" m AMSL · "+(data.height_kind==="custom"?"selected altitude":data.height_kind+" height");
      const pressureHeights=data.pressure_hpa!=null?"\nHeight range "+data.altitude_range_m.map(v=>Math.round(v).toLocaleString()).join("–")+" m AMSL":"";
      $("weather-overlay-meta").textContent=data.label+"\n"+level+pressureHeights+"\n"+formatTime(data.datetime)+" · "+(data.time_basis==="launch"?"selected launch time":"at target / burst")+"\n"+(data.coverage_mode==="model"?"Available model box":"Visible map + panning buffer")+" · "+data.valid_cells+" / "+data.total_cells+" valid grid points";
      $("weather-overlay-warning").textContent=data.warnings.join(" ");panel.dataset.loadedFlight=selected.jobId+":"+selected.flightIndex;panel.dataset.weatherTime=data.datetime;
      if(data.altitude_m!=null)panel.dataset.weatherHeight=String(data.altitude_m);else delete panel.dataset.weatherHeight;
      if(data.pressure_hpa!=null)panel.dataset.weatherPressure=String(data.pressure_hpa);else delete panel.dataset.weatherPressure;
      panel.dataset.coverageMode=data.coverage_mode;syncVisibility();
    }
    function scheduleRender(){if(!renderFrame)renderFrame=requestAnimationFrame(()=>{renderFrame=null;render();});}
    async function load(){
      if(!enabled||!selected||!show.checked)return;
      let request,requestKey;try{request=body();requestKey=key(request);}catch(error){message(error.message,false,true);return;}
      if(requestKey===loadedKey||pending.has(requestKey))return;if(activeLoads>=2)return;
      const mine=token,owner=selected;pending.add(requestKey);activeLoads++;message("Preparing the selected flight's weather…",true);
      try{
        let next=cache.get(requestKey);
        if(!next){let job=await api("/api/predictions/"+owner.jobId+"/weather-overlay",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(request)});
          while(true){job=await api("/api/jobs/"+job.id);if(job.status==="completed"){next=job.result;break;}if(job.status==="failed")throw new Error(job.error||job.message);if(mine===token)message(job.message||"Sampling weather…",true);await new Promise(resolve=>setTimeout(resolve,400));}
          cache.set(requestKey,next);while(cache.size>8)cache.delete(cache.keys().next().value);
        }
        if(mine!==token||!selected)return;
        if(next.coverage_mode==="model")coverageHints.set(owner.flight.request.dataset,"model");
        data=next;loadedKey=key({...request,area:next.coverage_mode==="model"?null:request.area});cache.set(loadedKey,next);while(cache.size>8)cache.delete(cache.keys().next().value);render();if(flow&&map.hasLayer(flow))flow.reset();message("Weather ready · fixed-time snapshot");maybeReloadMap();
      }catch(error){if(mine===token&&selected){if(!data)clear();message(error.message,false,true);}}
      finally{activeLoads--;pending.delete(requestKey);if(selected&&enabled&&show.checked){let current;try{current=key();}catch{}if(current&&current!==loadedKey&&!pending.has(current)&&(mine!==token||current!==requestKey))setTimeout(load,0);}}
    }
    function queue(keep=false){++token;clearTimeout(timer);if(!keep)clear();if(enabled&&selected&&show.checked)message("Preparing the selected flight's weather…",true);timer=setTimeout(load,150);}
    function maybeReloadMap(){
      if(!enabled||!selected||!show.checked||!data||data.coverage_mode==="model")return;
      const b=map.getBounds(),[n,w,s,e]=data.bounds,tooCoarse=(n-s)>Math.max(.01,b.getNorth()-b.getSouth())*4&&(e-w)>Math.max(.01,b.getEast()-b.getWest())*4;
      if(containsView(data.bounds,map)&&!tooCoarse)return;
      clearTimeout(mapTimer);mapTimer=setTimeout(()=>{area=bufferedBounds(map);queue(true);},500);
    }
    function setFlight(value){const old=selected&&selected.jobId+":"+selected.flightIndex,current=value&&value.jobId+":"+value.flightIndex;selected=value;flightCaption();if(old!==current){area=null;clearTimeout(mapTimer);queue();if(!value)message("Calculate a flight to show its weather.");}}
    function enable(){enabled=true;if(!selected){message("Calculate a flight to show its weather.");return;}load();}
    let previousField=field.value;
    field.onchange=()=>{rangeSettings.set(previousField,{mode:rangeMode.value,low:rangeMin.value,high:rangeMax.value});const saved=rangeSettings.get(field.value);rangeMode.value=saved?.mode||"auto";if(saved){rangeMin.value=saved.low;rangeMax.value=saved.high;}previousField=field.value;render();};
    opacity.oninput=()=>{$("weather-opacity-value").textContent=opacity.value+"%";image?.setOpacity(Number(opacity.value)/100);};
    show.onchange=()=>{if(show.checked)load();else{++token;clearTimeout(mapTimer);message("Weather hidden.");}syncVisibility();};wind.onchange=syncVisibility;time.onchange=()=>queue();
    levelMode.onchange=()=>{if(levelMode.value==="pressure")levelValue.value="500";else if(selected)levelValue.value=number(selected.flight.summary.burst.altitude);levelControls();queue();};levelValue.onchange=()=>queue();
    rangeMode.onchange=()=>render();for(const input of [rangeMin,rangeMax])input.onchange=()=>{const lo=Number(rangeMin.value),hi=Number(rangeMax.value);if(!rangeMin.value.trim()||!rangeMax.value.trim()||!Number.isFinite(lo)||!Number.isFinite(hi)||lo>=hi){$("weather-range-error").textContent="Colour maximum must be greater than minimum.";return;}rangeMode.value="fixed";render();};
    $("weather-overlay-map").onclick=()=>hooks.showMap?.();
    map.on("moveend resize",()=>{if(image&&data)image.setBounds(shiftedBounds());if(data&&rangeMode.value==="view")scheduleRender();maybeReloadMap();});
    levelControls();if(!panel.hidden)setTimeout(enable,0);
    return {setFlight,enable};
  }
  window.BalloonWeather={create,sampleGrid,step,mercator,inverseMercator,colour,quantile,niceRange,bufferedBounds,containsView,regional};
})();
