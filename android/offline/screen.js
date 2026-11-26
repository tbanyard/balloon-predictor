"use strict";
const $=id=>document.getElementById(id),worker=new Worker("/offline/worker.js");
const keys=["launch_datetime","launch_latitude","launch_longitude","launch_altitude","ascent_rate","flight_mode","burst_altitude","descent_rate","descent_model","landing_mode","landing_altitude"];
const number=new Set(keys.filter(k=>!["launch_datetime","flight_mode","descent_model","landing_mode"].includes(k)));
let current=0,setup=null,flight=null,map,tracks,domain,forecastInfo=null,savedDocument=null,savedLaunches=[];
function openMap(lat,lon){
  $("offline-map-panel").hidden=false;
  if(!map){map=L.map("map").setView([lat,lon],7);window.BalloonOfflineMap?.(map);tracks=L.layerGroup().addTo(map);L.control.scale({imperial:false}).addTo(map);new ResizeObserver(()=>map.invalidateSize({animate:false})).observe($("map"));}
  requestAnimationFrame(()=>map.invalidateSize({animate:false}));
}
function status(text,error=false){$("status").textContent=text;$("status").classList.toggle("error",error);}
function update(){
  const ascent=$("flight_mode").value==="ascent",fixed=$("landing_mode").value==="fixed";
  $("descent").hidden=ascent;$("fixed").hidden=!fixed;$("landing_altitude").disabled=ascent||!fixed;
  $("height-label").textContent=(ascent?"Target":"Burst")+" altitude (m AMSL)";
}
$("flight_mode").onchange=$("landing_mode").onchange=update;
$("forecast").onchange=async()=>{
  const file=$("forecast").files[0],id=++current;
  $("flight").hidden=$("result").hidden=true;status("");
  if(!file)return;
  if(file.size>32*1024**2+65544){status("This pack exceeds the 32 MiB limit.",true);return;}
  try{
    status("Opening the saved forecast…");
    const data=await file.arrayBuffer();
    if(id===current)worker.postMessage({id,action:"load",data},[data]);
  }catch(error){if(id===current)status(error.message,true);}
};
function draw(data){
  openMap(data.launch.latitude,data.launch.longitude);
  tracks.clearLayers();
  const all=[];
  let previous=data.launch.longitude;
  for(const stage of data.prediction){
    const points=stage.trajectory.map(p=>{
      const lon=previous+(((p.longitude-previous+180)%360+360)%360-180);previous=lon;
      return [p.latitude,lon];
    });
    L.polyline(points,{color:stage.stage==="ascent"?"#d75b2c":"#337fba",weight:3}).addTo(tracks);
    all.push(...points);
  }
  for(const point of [all[0],all.at(-1)])L.circleMarker(point,{color:"#28645a",radius:5,fillOpacity:.8}).addTo(tracks);
  map.invalidateSize();map.fitBounds(all,{padding:[24,24],maxZoom:10});
}
worker.onmessage=({data})=>{
  if(data.id!==current)return;
  $("calculate").disabled=false;
  if(data.error){status(data.error,true);return;}
  if(data.info){
    forecastInfo=data.info;setup={...data.info.setup};
    if(setup.ground_source==="dem"){setup.ground_source="manual";setup.ground_altitude=null;}
    if(setup.landing_mode==="dem")setup.landing_mode=data.info.fields.some(f=>f.name==="terrain")?"terrain":"fixed";
    if(!data.info.fields.some(f=>f.name==="terrain")&&setup.landing_mode==="terrain")setup.landing_mode="fixed";
    for(const key of keys)$(key).value=key==="launch_datetime"?new Date(setup[key]).toISOString().slice(0,19):setup[key];
    const [first,last]=[data.info.time[0],data.info.time.at(-1)].map(t=>new Date(t*1000).toISOString().slice(0,16).replace("T"," "));
    $("forecast-info").textContent=data.info.label+" · "+first+" to "+last+" UTC";
    $("ground-note").textContent=data.info.setup.ground_source==="dem"||data.info.setup.landing_mode==="dem"?
      "The saved flight used physical DEM. Check the launch altitude and choose model terrain or a known landing height before calculating.":"Launch and landing heights are metres above mean sea level.";
    $("flight").hidden=false;update();status("");
    openMap(setup.launch_latitude,setup.launch_longitude);tracks.clearLayers();
    if(domain)map.removeLayer(domain);
    const lat=data.info.lat,lon=data.info.lon;
    domain=L.rectangle([[lat[0],lon[0]],[lat.at(-1),lon.at(-1)]],{color:'#28645a',weight:1,dashArray:'5 5',fillOpacity:.02,interactive:false}).addTo(map);
    requestAnimationFrame(()=>{map.invalidateSize({animate:false});map.fitBounds(domain.getBounds(),{padding:[20,20],maxZoom:9});});
  }else{
    flight=data.flight;savedDocument=offlineDocument(flight);BalloonFlights.update(savedDocument,true);$("saved-launch-label").hidden=true;$("result").hidden=false;
    const p=flight.landing||flight.burst;
    $("endpoint").textContent=(flight.landing?"Landing":"Target")+" · "+p.latitude.toFixed(5)+"°, "+p.longitude.toFixed(5)+"° · "+Math.round(p.altitude)+" m AMSL · "+p.datetime.replace("T"," ").slice(0,19)+" UTC";
    status("Flight calculated on this phone.");draw(flight);
  }
};
worker.onerror=()=>{$("calculate").disabled=false;status("The offline calculator could not start. Reopen the offline workspace.",true);};
$("flight").onsubmit=event=>{
  event.preventDefault();if(!setup)return;
  const data={...setup};
  for(const key of keys)data[key]=number.has(key)?Number($(key).value):$(key).value;
  data.launch_datetime+="Z";$("calculate").disabled=true;$("result").hidden=true;
  status("Calculating with the saved weather…");worker.postMessage({id:++current,action:"fly",data});
};
function save(name,mime,data){
  window.__bpExportFilename=name;
  const url=URL.createObjectURL(new Blob([data],{type:mime}));
  const link=document.createElement("a");link.href=url;link.download=name;document.body.append(link);link.click();link.remove();
  setTimeout(()=>URL.revokeObjectURL(url),10000);
}
$("save-csv").onclick=()=>{if(flight)save("offline-balloon-flight.csv","text/csv",
  "stage,datetime,latitude,longitude,altitude_m\n"+flight.prediction.flatMap(s=>s.trajectory.map(p=>[s.stage,p.datetime,p.latitude,p.longitude,p.altitude].join(","))).join("\n")+"\n");};
$("save-geojson").onclick=()=>{if(flight)save("offline-balloon-flight.geojson","application/geo+json",JSON.stringify({type:"FeatureCollection",features:
  flight.prediction.map(s=>({type:"Feature",properties:{stage:s.stage,weather:flight.weather},geometry:{type:"LineString",coordinates:s.trajectory.map(p=>[p.longitude,p.latitude,p.altitude])}}))}));};

function offlineDocument(data){
  const distance=(a,b)=>{const r=Math.PI/180,p=(b.latitude-a.latitude)*r,q=(b.longitude-a.longitude)*r;return 6371*2*Math.asin(Math.min(1,Math.sqrt(Math.sin(p/2)**2+Math.cos(a.latitude*r)*Math.cos(b.latitude*r)*Math.sin(q/2)**2)));};
  const endpoint=data.landing||data.burst,last=data.prediction.at(-1).trajectory.at(-1),request={...data.request};
  const result={request,prediction:data.prediction,summary:{launch:data.launch,burst:data.burst,landing:data.landing,endpoint,endpoint_kind:data.landing?'landing':'target',duration_seconds:last.elapsed_seconds,ascent_seconds:data.burst.elapsed_seconds,descent_seconds:data.landing?last.elapsed_seconds-data.burst.elapsed_seconds:0,drift_km:distance(data.launch,endpoint),burst_drift_km:distance(data.launch,data.burst)},
    weather:{id:request.dataset,source:forecastInfo?.source||'custom',label:data.weather,native:false,provenance:{description:'Calculated on Android from a saved regional forecast; original server source receipts are unavailable.'}},warnings:['Calculated from a saved regional forecast. Newly sampled exports need a recalculation with verified server weather.'],ensemble:[],sounding:null};
  const site={id:'offline-flight',setup:{...request,launch_datetime:new Date(request.launch_datetime).toISOString().slice(0,19),run_mode:'single'},result,series:null,jobId:null,selectedIndex:0,batchSiteIndex:0};
  return BalloonFlights.pack([site],site.id);
}
function showSavedLaunch(){
  const item=savedLaunches[Number($('saved-launch').value)];if(!item)return;
  const data=item.result;flight={request:data.request,prediction:data.prediction,weather:data.weather.label,launch:data.summary.launch,burst:data.summary.burst,landing:data.summary.landing};
  $('result').hidden=false;const p=flight.landing||flight.burst;
  $('endpoint').textContent=(flight.landing?'Landing':'Target')+' · '+p.latitude.toFixed(5)+'°, '+p.longitude.toFixed(5)+'° · '+Math.round(p.altitude)+' m AMSL · '+p.datetime.replace('T',' ').slice(0,19)+' UTC';
  draw(flight);status('Saved tracks opened. Open a .bpforecast file to calculate again.');
}
function openSavedDocument(value){
  savedDocument=BalloonFlights.validate(value);savedLaunches=[];
  for(const site of value.sites){const data=value.calculations[site.calculation];if(!data)continue;const list=data.flights?data.flights.filter(e=>e.status==='completed'&&e.site_index===(site.batchSiteIndex||0)).map(e=>e.result):[data];for(const result of list)savedLaunches.push({site,result});}
  if(!savedLaunches.length)throw Error('This file has no completed flights.');
  $('saved-launch').replaceChildren(...savedLaunches.map((entry,index)=>new Option((entry.result.request.flight_name||'Launch')+' · '+entry.result.request.launch_datetime.replace('T',' ').slice(0,19)+' UTC',index)));
  $('saved-launch-label').hidden=false;showSavedLaunch();
}
$('saved-launch').onchange=showSavedLaunch;
$('save-flight').onclick=()=>{if(savedDocument)BalloonFlights.download(savedDocument,'balloon-flight.bpflight');};
$('open-saved-flight').onchange=async()=>{try{const file=$('open-saved-flight').files[0];if(!file)return;if(file.size>BalloonFlights.MAX)throw Error('Saved flights must be under 32 MB.');openSavedDocument(JSON.parse(await file.text()));await BalloonFlights.update(savedDocument,true);}catch(error){status(error.message,true);}finally{$('open-saved-flight').value='';}};
$('open-recent-flight').onclick=async()=>{const saved=await BalloonFlights.load();if(saved.workspace){try{openSavedDocument(saved.workspace);}catch(error){status(error.message,true);}}else status('No saved flights on this phone yet.');};
document.addEventListener('bp:saveerror',event=>status(event.detail,true));
BalloonFlights.load().then(value=>{if(value.workspace)try{openSavedDocument(value.workspace);}catch{/* Keep the forecast chooser available for an older invalid snapshot. */}});
