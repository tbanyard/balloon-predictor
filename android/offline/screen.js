"use strict";
const $=id=>document.getElementById(id),worker=new Worker("/offline/worker.js");
const keys=["launch_datetime","launch_latitude","launch_longitude","launch_altitude","ascent_rate","flight_mode","burst_altitude","descent_rate","descent_model","landing_mode","landing_altitude"];
const number=new Set(keys.filter(k=>!["launch_datetime","flight_mode","descent_model","landing_mode"].includes(k)));
let current=0,setup=null,flight=null,map,tracks;
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
  if(!map){
    map=L.map("map").setView([data.launch.latitude,data.launch.longitude],7);
    for(const line of window.ERA5Coastlines?.lines||[])
      L.polyline(line.points.map(([lon,lat])=>[lat,lon]),{color:"#8c9f91",weight:.9,interactive:false}).addTo(map);
    map.attributionControl.addAttribution("Natural Earth coastlines");
    tracks=L.layerGroup().addTo(map);L.control.scale({imperial:false}).addTo(map);
  }
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
  map.invalidateSize();map.fitBounds(all,{padding:[24,24],maxZoom:12});
}
worker.onmessage=({data})=>{
  if(data.id!==current)return;
  $("calculate").disabled=false;
  if(data.error){status(data.error,true);return;}
  if(data.info){
    setup={...data.info.setup};
    if(setup.ground_source==="dem"){setup.ground_source="manual";setup.ground_altitude=null;}
    if(setup.landing_mode==="dem")setup.landing_mode=data.info.fields.some(f=>f.name==="terrain")?"terrain":"fixed";
    if(!data.info.fields.some(f=>f.name==="terrain")&&setup.landing_mode==="terrain")setup.landing_mode="fixed";
    for(const key of keys)$(key).value=key==="launch_datetime"?new Date(setup[key]).toISOString().slice(0,19):setup[key];
    const [first,last]=[data.info.time[0],data.info.time.at(-1)].map(t=>new Date(t*1000).toISOString().slice(0,16).replace("T"," "));
    $("forecast-info").textContent=data.info.label+" · "+first+" to "+last+" UTC";
    $("ground-note").textContent=data.info.setup.ground_source==="dem"||data.info.setup.landing_mode==="dem"?
      "The saved flight used physical DEM. Check the launch altitude and choose model terrain or a known landing height before calculating.":"Launch and landing heights are metres above mean sea level.";
    $("flight").hidden=false;update();status("");
  }else{
    flight=data.flight;$("result").hidden=false;
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
  const url=URL.createObjectURL(new Blob([data],{type:mime}));
  const link=document.createElement("a");link.href=url;link.download=name;document.body.append(link);link.click();link.remove();
  setTimeout(()=>URL.revokeObjectURL(url),10000);
}
$("save-csv").onclick=()=>{if(flight)save("offline-balloon-flight.csv","text/csv",
  "stage,datetime,latitude,longitude,altitude_m\n"+flight.prediction.flatMap(s=>s.trajectory.map(p=>[s.stage,p.datetime,p.latitude,p.longitude,p.altitude].join(","))).join("\n")+"\n");};
$("save-geojson").onclick=()=>{if(flight)save("offline-balloon-flight.geojson","application/geo+json",JSON.stringify({type:"FeatureCollection",features:
  flight.prediction.map(s=>({type:"Feature",properties:{stage:s.stage,weather:flight.weather},geometry:{type:"LineString",coordinates:s.trajectory.map(p=>[p.longitude,p.latitude,p.altitude])}}))}));};
