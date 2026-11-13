"use strict";
const $ = id => document.getElementById(id);
const STORAGE_KEY = "balloon-predictor-v1.1-setups";
const numericFields = ["launch_latitude","launch_longitude","launch_altitude","ascent_rate","descent_rate","burst_altitude","landing_altitude","time_step","max_duration_hours","ensemble_members","ascent_sigma","descent_sigma","burst_sigma","wind_sigma","seed"];
const setupFields = ["dataset","launch_datetime","flight_name","flight_mode","descent_model","landing_mode","use_vertical_wind","ground_source","ground_altitude","below_model_policy",...numericFields,"run_mode","series_duration","series_interval","series_sites","arrival_datetime","arrival_altitude","arrival_rates","arrival_rates_mode","arrival_sites"];
let demRecovery=null,preferredTrack="both",preferredEndpoint="landing";
let datasets=[], sites=[], activeId=null, result=null, jobId=null, hoverMarker=null, toastTimer, running=false, soundingToken=0;
const palette=["#28645a","#bd6a29","#7056a0","#3187a1","#a65475","#747d35"];
const map=L.map("map",{zoomControl:false,preferCanvas:true}).setView([52.817208,-4.125],7);
L.control.zoom({position:"topright"}).addTo(map);
L.control.scale({imperial:false,position:"bottomright"}).addTo(map);
const coastLayer=L.layerGroup().addTo(map);
if(window.ERA5Coastlines){
  for(const line of window.ERA5Coastlines.lines) L.polyline(line.points.map(([lon,lat])=>[lat,lon]),{color:"#8c9f91",weight:.75,interactive:false}).addTo(coastLayer);
  map.attributionControl.addAttribution("Natural Earth coastlines");
}
const tiles=L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png",{attribution:'&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',maxZoom:18,crossOrigin:true}).addTo(map);
tiles.on("tileerror",()=>map.getContainer().setAttribute("data-tiles","unavailable"));
const flightLayer=L.layerGroup().addTo(map), coverageLayer=L.layerGroup().addTo(map), siteLayer=L.layerGroup().addTo(map);
const launchMarker=L.marker([52.817208,-4.125],{draggable:true,icon:L.divIcon({className:"launch-icon",iconSize:[18,18],iconAnchor:[9,9]})}).addTo(map).bindTooltip("Active launch point · drag to move",{direction:"top"});
function status(id,message,kind=""){ $(id).textContent=message; $(id).className="status-message "+kind;if(id==="flight-status")$("mobile-flight-status").textContent=message; }
function toast(message){ $("toast").textContent=message; $("toast").hidden=false; clearTimeout(toastTimer); toastTimer=setTimeout(()=>$("toast").hidden=true,3500); }
function utcInput(value){return new Date(value).toISOString().slice(0,19);}
function minuteInput(value,ceil=false){const t=new Date(value).getTime();return new Date((ceil?Math.ceil(t/60000):Math.floor(t/60000))*60000).toISOString().slice(0,16);}
function fromInput(value){return value?new Date(value+"Z").toISOString():null;}
function duration(seconds){const m=Math.round(seconds/60);return(m>=60?Math.floor(m/60)+"h ":"")+(m%60)+"m";}
function utcText(value){return new Date(value).toISOString().replace("T"," ").replace(".000Z"," UTC");}
async function api(path,options){
  const response=await fetch(path,options); let data;
  try{data=await response.json();}catch{throw new Error("Server returned an unreadable response.");}
  if(!response.ok){const d=data.detail;throw Object.assign(new Error(Array.isArray(d)?d.map(e=>e.loc.slice(1).join(".")+": "+e.msg).join("; "):d||"Request failed."),{details:data.error_details});}
  return data;
}
async function poll(id,update){for(;;){const job=await api("/api/jobs/"+id);update(job.message,job.status);if(job.status==="failed")throw Object.assign(new Error(job.error),{details:job.error_details});if(job.status==="completed")return job.result;await new Promise(resolve=>setTimeout(resolve,500));}}
function activeSite(){return sites.find(s=>s.id===activeId);}
function readSetup(){return Object.fromEntries(setupFields.map(key=>[key,$(key).type==="checkbox"?$(key).checked:$(key).value]));}
function writeSetup(setup){for(const key of setupFields)if(setup[key]!==undefined){if($(key).type==="checkbox")$(key).checked=Boolean(setup[key]);else $(key).value=setup[key];}}
function persist(){try{localStorage.setItem(STORAGE_KEY,JSON.stringify({version:1,activeId,sites:sites.map(s=>({id:s.id,setup:s.setup}))}));}catch{}}
function saveCurrent(){const site=activeSite();if(site)site.setup=readSetup();persist();}
function newSite(setup){return{id:globalThis.crypto?.randomUUID?.()||String(Date.now())+Math.random(),setup:{...setup},revision:0,result:null,series:null,jobId:null,selectedIndex:0,batchSiteIndex:0};}
function siteEntries(site){return site.series?site.series.flights.filter(e=>e.site_index===site.batchSiteIndex):[];}
function selectedResult(site=activeSite()){if(!site)return null;if(site.series){const e=siteEntries(site).find(e=>e.index===site.selectedIndex);return e?.status==="completed"?e.result:null;}return site.result;}
function renderTabs(){
  $("flight-tabs").replaceChildren();
  for(const site of sites){const tab=document.createElement("button");tab.type="button";tab.className="flight-tab";tab.setAttribute("role","tab");tab.setAttribute("aria-selected",String(site.id===activeId));tab.textContent=site.setup.flight_name||"Launch point";tab.onclick=()=>switchSite(site.id);$("flight-tabs").append(tab);}
  $("remove-flight").disabled=sites.length<=1;
}
function switchSite(id){setDemRecovery(null);saveCurrent();activeId=id;const site=activeSite();writeSetup(site.setup);updateFields();datasetChanged(false);renderTabs();drawSelected();drawAllMap();persist();status("flight-status",site.series?"Series loaded · "+siteEntries(site).length+" launches":site.result?"Saved flight loaded.":"Ready to calculate this launch point.");}
function invalidatePrediction(){
  setDemRecovery(null);
  const site=activeSite();if(!site)return;
  site.revision++;site.result=null;site.series=null;site.jobId=null;site.setup=readSetup();
  renderTabs();drawSelected();drawAllMap();persist();status("flight-status","Setup changed. Calculate to update this launch point.");
}
function setLocation(lat,lon,invalidate=true){
  lat=Math.max(-85,Math.min(85,lat));lon=(lon+540)%360-180;
  $("launch_latitude").value=lat.toFixed(6);$("launch_longitude").value=lon.toFixed(6);launchMarker.setLatLng([lat,lon]);
  if(invalidate)invalidatePrediction();
}
map.on("click",e=>setLocation(e.latlng.lat,e.latlng.lng));
launchMarker.on("dragend",()=>{const p=launchMarker.getLatLng();setLocation(p.lat,p.lng);});
for(const key of ["launch_latitude","launch_longitude"])$(key).addEventListener("change",()=>{
  const latitude=$("launch_latitude"),longitude=$("launch_longitude");
  if(!latitude.validity.valid||!longitude.validity.valid)return;
  const lat=latitude.valueAsNumber,lon=longitude.valueAsNumber;
  if(Number.isFinite(lat)&&Number.isFinite(lon))launchMarker.setLatLng([lat,BPSensitivity.longitudeNear(lon,0)]);
});
$("map-hint").onclick=()=>toast("Click the map or drag the active green launch point.");
function updateFields(){
  $("ground-altitude-field").hidden=$("ground_source").value!=="manual";
  $("landing-field").hidden=$("landing_mode").value!=="fixed";
  const ascent=$("flight_mode").value==="ascent"||$("run_mode").value==="arrival";
  $("altitude-label").textContent=ascent?"Target altitude":"Burst altitude";
  $("descent_rate").disabled=ascent;$("descent_model").disabled=ascent;
  $("landing_mode").disabled=ascent;$("landing_altitude").disabled=ascent;
  for(const id of ["descent-model-field","descent-rate-field","landing-surface-field","landing-field"])$(id).classList.toggle("is-inactive",ascent);
  $("landing-surface-note").textContent=ascent?"Not used for ascent-only flights or coordinated arrivals.":"Landing terrain is independent of launch ground. Use a DEM for realistic landings near coarse model coastlines.";
  const track=$("track-display"),endpoint=$("endpoint-focus"),left=endpoint.querySelector("[data-left]");
  left.setAttribute("aria-label",ascent?"Target position":"Burst position");
  for(const icon of left.querySelectorAll("svg"))icon.toggleAttribute("hidden",icon.hasAttribute("data-target")!==ascent);
  track.set(ascent?"ascent":preferredTrack,ascent);
  endpoint.set(ascent?"burst":preferredEndpoint,ascent);
  $("schedule-fields").hidden=$("run_mode").value!=="series";
  const arrival=$("run_mode").value==="arrival";
  $("arrival-fields").hidden=!arrival;$("arrival_datetime").required=arrival;
  $("arrival-rates-field").hidden=$("arrival_rates_mode").value==="saved";
  const count=Math.max(1,Math.ceil(Number($("series_duration").value)/Number($("series_interval").value)-1e-10));
  $("schedule-note").textContent=count+" launches per point · first at launch time · end of window excluded.";
  $("predict-button").querySelector("span").textContent=$("run_mode").value==="arrival"?"Plan coordinated arrivals":$("run_mode").value==="series"?"Calculate launch series":"Calculate flight";
}
$("landing_mode").onchange=updateFields;$("flight_mode").onchange=updateFields;$("run_mode").onchange=updateFields;$("arrival_rates_mode").onchange=updateFields;
$("descent_model").onchange=()=>{document.querySelector('label[for="descent_rate"]').firstChild.textContent=$("descent_model").value==="density"?"Descent at sea level ":"Descent ";};
$("flight-form").addEventListener("input",()=>{updateFields();invalidatePrediction();});
$("flight-form").addEventListener("change",()=>{updateFields();invalidatePrediction();});
function addSite(duplicate){saveCurrent();if(sites.length>=20){toast("Up to 20 launch points can be saved.");return;}const setup={...activeSite().setup,flight_name:"Launch "+(sites.length+1)};if(!duplicate)setup.launch_longitude=String(Number(setup.launch_longitude)+.1);const site=newSite(setup);sites.push(site);switchSite(site.id);}
$("add-flight").onclick=()=>addSite(false);$("duplicate-flight").onclick=()=>addSite(true);
$("remove-flight").onclick=()=>{if(sites.length<=1)return;const id=activeId;sites=sites.filter(s=>s.id!==id);activeId=sites[0].id;writeSetup(sites[0].setup);updateFields();datasetChanged(false);renderTabs();drawSelected();drawAllMap();persist();};
function coverageText(info){
  if(info.source==="demo")return"Synthetic winds · any date · flat terrain";
  if(info.native&&!info.available)return"No local files available · "+info.root+" · open Manage weather to rescan or download.";
  const top=info.minimum_top_altitude!=null?" · top ≥ "+Math.floor(info.minimum_top_altitude).toLocaleString()+" m":" · model top checked along each flight";
  return info.start?utcText(info.start)+" → "+utcText(info.end)+(info.time_bounds_exact===false?" · exact times from files":"")+top:"No available times.";
}
async function loadDatasets(selectId){
  const response=await api("/api/weather");datasets=response.datasets;
  $("archive-paths").textContent="ERA5: "+response.defaults.era5_root+" · WRF: "+response.defaults.wrf_root+" · column cache: "+response.defaults.cache_mb+" MB. Edit config.yml to change these paths.";
  const defaultId=datasets.some(d=>d.id===response.defaults.dataset)?response.defaults.dataset:"demo";
  const selected=selectId||$("dataset").value||defaultId;$("dataset").replaceChildren();$("dataset-list").replaceChildren();
  for(const info of [...datasets.filter(i=>i.id==="era5"),...datasets.filter(i=>i.id!=="era5"&&i.id!=="demo"),...datasets.filter(i=>i.id==="demo")]){
    const option=document.createElement("option");option.value=info.id;option.textContent=info.label;$("dataset").append(option);
    const card=document.createElement("div");card.className="dataset-card";
    const tag=document.createElement("span");tag.className="source-tag";tag.textContent=info.source;
    const text=document.createElement("div"),name=document.createElement("strong"),desc=document.createElement("small");name.textContent=info.label;desc.textContent=coverageText(info);text.append(name,desc);
    const use=document.createElement("button");use.type="button";use.className="text-button";use.textContent="Use dataset →";use.onclick=()=>{$("dataset").value=info.id;datasetChanged(true);invalidatePrediction();$("weather-dialog").close();};
    card.append(tag,text,use);$("dataset-list").append(card);
  }
  for(const [value,label] of [["online-ifs","Online · ECMWF operational IFS…"],["online-gfs","Online · NOAA GFS…"]]){const option=document.createElement("option");option.value=value;option.textContent=label;$("dataset").append(option);}
  $("dataset").value=datasets.some(d=>d.id===selected)||["online-ifs","online-gfs"].includes(selected)?selected:defaultId;datasetChanged(false);
}
let timesToken=0;
function datasetChanged(adjustDate){
  ++timesToken;
  const online=$("dataset").value.startsWith("online-");$("ground-button").disabled=online;
  $("predict-button").disabled=running||online||!sites.length;
  if(online){$("coverage").textContent="Download this operational forecast, then it will be selected for predictions.";$("available-days-field").hidden=true;$("model-badge").textContent=$("dataset").value.endsWith("gfs")?"GFS · ONLINE":"ECMWF IFS · ONLINE";if(adjustDate)prepareForecast($("dataset").value.slice(7));return;}
  const info=datasets.find(d=>d.id===$("dataset").value);if(!info)return;
  $("model-badge").textContent=info.source==="demo"?"DEMO WEATHER":info.source.toUpperCase()+" · "+(info.native?"LOCAL FILES":info.id);
  $("demo-notice").hidden=info.source!=="demo";$("coverage").textContent=coverageText(info);coverageLayer.clearLayers();
  if(info.area){const[n,w,s,e]=info.area;L.rectangle([[s,w],[n,e]],{color:"#688371",weight:1,dashArray:"5,6",fill:false,interactive:false}).addTo(coverageLayer);}
  if(adjustDate&&info.start&&info.id!=="era5"){const chosen=fromInput($("launch_datetime").value);if(!chosen||chosen<info.start||chosen>info.end)$("launch_datetime").value=utcInput(info.start);}
  if(info.terrain_available===false){$("landing_mode").value="fixed";updateFields();}
  if(!info.vertical_wind_available)$("use_vertical_wind").checked=false;
  $("available-days-field").hidden=!info.dates?.length;
  const blank=new Option("No archive match","");blank.disabled=true;
  $("available-day").replaceChildren(blank,...(info.dates||[]).map(date=>new Option(date,date)));
  launchMarker.setLatLng([Number($("launch_latitude").value),Number($("launch_longitude").value)]);
  syncArchiveDate();
}
async function syncArchiveDate(){
  const token=++timesToken,day=$("launch_datetime").value.slice(0,10),dataset=$("dataset").value;
  const dates=datasets.find(d=>d.id===dataset)?.dates;
  $("available-day").value=dates?.includes(day)?day:"";
  if(!dates?.length)return;
  if(!dates.includes(day)){
    $("available-times").textContent=day?"No archive records on "+day+". Pick an archive date to change the launch day.":"Enter a launch time or pick an archive date.";
    return;
  }
  $("available-times").textContent="Checking records…";
  try{
    const {times}=await api("/api/weather/"+encodeURIComponent(dataset)+"/times?date="+day);
    if(token!==timesToken)return;
    $("available-times").textContent=times.length?"Records: "+times.map(t=>t.datetime.slice(11,16)).join(", ")+" UTC · interpolation between adjacent records":"No records on this date.";
  }catch(e){if(token===timesToken)$("available-times").textContent=e.message;}
}
$("available-day").onchange=()=>{
  const day=$("available-day").value;
  if(day){$("launch_datetime").value=day+"T"+($("launch_datetime").value.slice(11)||"00:00");invalidatePrediction();}
  syncArchiveDate();
};
$("launch_datetime").addEventListener("change",syncArchiveDate);
$("dataset").onchange=()=>datasetChanged(true);
$("ground-button").onclick=async()=>{
  $("ground-settings").open=true;
  const query=new URLSearchParams({latitude:$("launch_latitude").value,longitude:$("launch_longitude").value,when:fromInput($("launch_datetime").value)});
  const messages=["Launch: "+$("launch_altitude").value+" m AMSL"];
  try{const data=await api("/api/weather/"+encodeURIComponent($("dataset").value)+"/terrain?"+query);messages.push("Model ground: "+data.altitude.toFixed(1)+" m");if($("ground_source").value==="manual"&&$("ground_altitude").value!=="")messages.push("Known ground: "+Number($("ground_altitude").value).toFixed(1)+" m · model mismatch "+(data.altitude-Number($("ground_altitude").value)).toFixed(1)+" m");}catch(e){messages.push("Model ground: "+e.message);}
  if($("ground_source").value==="dem")try{const data=await api("/api/terrain/sample?"+query);messages.push("Physical DEM: "+data.altitude.toFixed(1)+" m · "+data.tile);}catch(e){messages.push(e.message);}
  $("ground-comparison").textContent=messages.join(" · ");
};
function requestFromSetup(setup){const body={dataset:setup.dataset,launch_datetime:fromInput(setup.launch_datetime),flight_name:setup.flight_name||"Launch",flight_mode:setup.run_mode==="arrival"?"ascent":setup.flight_mode||"full",descent_model:setup.descent_model,landing_mode:setup.landing_mode,use_vertical_wind:Boolean(setup.use_vertical_wind),ground_source:setup.ground_source||"manual",ground_altitude:setup.ground_altitude===""||setup.ground_altitude==null?null:Number(setup.ground_altitude),below_model_policy:setup.below_model_policy||"bridge"};for(const key of numericFields)body[key]=Number(setup[key]);return body;}
function flightRequest(){return requestFromSetup(readSetup());}
function arrivalRates(){
  if($("arrival_rates_mode").value==="saved")return null;
  const rates=$("arrival_rates").value.trim().split(/[,;\s]+/).map(Number);
  if(!rates.length||rates.length>20||rates.some(v=>!Number.isFinite(v)||v<=0||v>30))throw new Error("Enter 1–20 ascent rates greater than zero and at most 30 m/s, separated by commas.");
  return rates;
}
function setDemRecovery(details,selected=activeId){
  demRecovery=details?.code==="missing_dem"&&details.downloadable?{details,selected}:null;
  $("flight-dem-action").hidden=$("mobile-dem-download").hidden=!demRecovery;
  if(!demRecovery)return;
  const label=details.phase==="launch"?"Download launch DEM and retry":"Download required descent DEM and retry";
  for(const id of ["flight-dem-download","mobile-dem-download"]){$(id).textContent=label;$(id).title=details.tile;}
}
function terrainJob(details){
  return api("/api/terrain/download",{method:"POST",headers:{"Content-Type":"application/json"},
    body:JSON.stringify({latitude:details.latitude,longitude:details.longitude,verify_pixel:true})});
}
async function recoverDem(){
  const recovery=demRecovery;
  if(!recovery||running||activeId!==recovery.selected)return;
  for(const id of ["flight-dem-download","mobile-dem-download"])$(id).disabled=true;
  try{
    status("flight-status","Fetching the required DEM tile…","working");
    const job=await terrainJob(recovery.details);
    await poll(job.id,message=>{if(demRecovery===recovery)status("flight-status",message,"working");});
    if(demRecovery!==recovery||activeId!==recovery.selected)return;
    setDemRecovery(null);
    status("flight-status","DEM tile saved. Retrying the flight…","working");
    $("flight-form").requestSubmit();
  }catch(error){
    if(demRecovery!==recovery)return;
    status("flight-status",error.message,"error");
    if(error.details?.code==="missing_dem"&&!error.details.downloadable)setDemRecovery(null);
  }finally{
    for(const id of ["flight-dem-download","mobile-dem-download"])$(id).disabled=false;
  }
}
$("flight-dem-download").onclick=$("mobile-dem-download").onclick=recoverDem;
$("flight-form").onsubmit=async e=>{
  e.preventDefault();if(running)return;setDemRecovery(null);saveCurrent();
  const mode=$("run_mode").value,isSeries=mode!=="single",isArrival=mode==="arrival";
  const all=isArrival?$("arrival_sites").value==="all":isSeries&&$("series_sites").value==="all";
  const chosen=all?[...sites]:[activeSite()],revisions=chosen.map(site=>site.revision);
  running=true;window.BalloonUI?.predictionBusy(true);$("predict-button").disabled=true;document.dispatchEvent(new CustomEvent("bp:predictionstarted"));
  try{
    if(chosen.some(site=>site.setup.dataset.startsWith("online-")))throw new Error("Download the selected online forecast in Manage weather first.");
    let body,path;
    if(isArrival){if(!$("arrival_datetime").value)throw new Error("Choose a common arrival date and UTC time.");body={flights:chosen.map(site=>requestFromSetup(site.setup)),arrival_datetime:fromInput($("arrival_datetime").value),target_altitude:Number($("arrival_altitude").value),ascent_rates:arrivalRates()};path="/api/predictions/arrival";}
    else if(isSeries){body={flights:chosen.map(site=>requestFromSetup(site.setup)),duration_hours:Number($("series_duration").value),interval_hours:Number($("series_interval").value)};path="/api/predictions/series";}
    else{body=flightRequest();path="/api/predictions";}
    status("flight-status",isArrival?"Solving launch times and predicted arrival positions…":"Preparing weather and calculating…","working");
    const job=await api(path,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
    const completed=await poll(job.id,message=>status("flight-status",message,"working"));let accepted=0;
    chosen.forEach((site,i)=>{if(!sites.includes(site)||site.revision!==revisions[i])return;site.jobId=job.id;if(isSeries){site.series=completed;site.batchSiteIndex=i;site.selectedIndex=completed.flights.find(x=>x.site_index===i&&x.status==="completed")?.index??-1;site.result=null;}else{site.result=completed;site.series=null;}accepted++;});
    if(isArrival)$("endpoint-focus").set("burst",true);
    drawSelected();drawAllMap();fitFlight();
    if(accepted&&window.BalloonWindows)window.BalloonWindows.show("profile-panel");
    status("flight-status",!accepted?"Setup changed during calculation. Calculate again.":isSeries?completed.completed+" of "+completed.total+" balloons completed"+(completed.failed?" · "+completed.failed+" unavailable; see launch list.":""):"Flight complete · "+completed.weather.label);
    const failure=isSeries&&completed.flights.find(entry=>entry.error_details?.code==="missing_dem");
    if(accepted&&failure){
      status("flight-status",$("flight-status").textContent+" "+failure.error,"error");
      setDemRecovery(failure.error_details);
    }
  }catch(error){
    status("flight-status",error.message,"error");
    if(chosen.includes(activeSite())&&chosen.every((site,i)=>site.revision===revisions[i]))setDemRecovery(error.details);
  }finally{running=false;window.BalloonUI?.predictionBusy(false);$("predict-button").disabled=$("dataset").value.startsWith("online-");}
};
function unwrapped(points){let previous=null;return points.map(p=>{let lon=p.longitude;if(previous!==null)lon=previous+((lon-previous+540)%360-180);previous=lon;return[p.latitude,lon];});}
function allFlights(){return sites.flatMap((site,si)=>site.series?siteEntries(site).filter(e=>e.status==="completed").map(e=>({site,si,index:e.index,result:e.result})):site.result?[{site,si,index:0,result:site.result}]:[]);}
function endpoint(data){return preferredEndpoint==="burst"||!data.summary.landing?data.summary.burst:data.summary.landing;}
function drawAllMap(){
  flightLayer.clearLayers();siteLayer.clearLayers();
  const coordinated=new Map();
  let showDots=false,showRegion=false;
  for(const entry of allFlights())if(entry.site.series?.schedule_type==="coordinated"){if(!coordinated.has(entry.site.jobId))coordinated.set(entry.site.jobId,[]);coordinated.get(entry.site.jobId).push(entry);}
  for(const entries of coordinated.values())if(entries.length>1){const points=entries.sort((a,b)=>a.result.request.ascent_rate-b.result.request.ascent_rate).map(entry=>entry.result.summary.burst);L.polyline(unwrapped(points),{color:"#ce8b35",weight:2,dashArray:"3,6",opacity:.8}).addTo(flightLayer).bindTooltip("Predicted formation at the shared arrival time · guide, not a flight path");}
  for(const site of sites)if(site.id!==activeId){const marker=L.circleMarker([Number(site.setup.launch_latitude),Number(site.setup.launch_longitude)],{radius:6,color:"#28645a",weight:2,fillOpacity:.5}).addTo(siteLayer);marker.bindTooltip(document.createTextNode(site.setup.flight_name));marker.on("click",e=>{L.DomEvent.stopPropagation(e.originalEvent||e);switchSite(site.id);});}
  for(const entry of allFlights()){
    const selected=entry.site.id===activeId&&(!entry.site.series||entry.index===entry.site.selectedIndex);const data=entry.result;const color=palette[(entry.si+entry.index)%palette.length];
    for(const stage of data.prediction){if(stage.stage==="descent"&&preferredTrack==="ascent")continue;const line=L.polyline(unwrapped(stage.trajectory),{color:selected?(stage.stage==="ascent"?"#d75b2c":"#337fba"):color,weight:selected?3.5:1.8,opacity:selected?.98:.5,dashArray:stage.stage==="descent"?"5,4":null}).addTo(flightLayer);line.bindTooltip(document.createTextNode(entry.site.setup.flight_name+" · "+utcText(data.summary.launch.datetime)));line.on("click",e=>{L.DomEvent.stopPropagation(e.originalEvent||e);switchSite(entry.site.id);entry.site.selectedIndex=entry.index;drawSelected();drawAllMap();});}
    const p=endpoint(data),longitude=BPSensitivity.longitudeNear(p.longitude,map.getCenter().lng);
    L.circleMarker([p.latitude,longitude],{radius:selected?6:3.5,color:selected?"white":color,weight:1.5,fillColor:selected?"#d75b2c":color,fillOpacity:.9}).addTo(flightLayer).bindTooltip(document.createTextNode((p===data.summary.burst?(data.summary.landing?"Burst":"Target"):"Landing")+" · "+Math.round(p.altitude).toLocaleString()+" m · "+utcText(p.datetime)));
    const members=data.ensemble||[];
    if($("sensitivity-display").value==="region"&&members.length>=2){
      showRegion=true;
      const ring=BPSensitivity.endpointRegion({...p,longitude},members.map(endpoint));
      L.polygon(ring,{color,weight:2,opacity:.24,fillOpacity:.21,smoothFactor:0,lineJoin:"round"}).addTo(flightLayer)
        .bringToBack().bindTooltip("Sensitivity region · nominal + "+members.length+" members · 1 km margin");
    }else{
      showDots||=members.length>0;
      for(const member of members){const q=endpoint(member);L.circleMarker([q.latitude,BPSensitivity.longitudeNear(q.longitude,longitude)],{radius:2.8,color,weight:.8,fillOpacity:.4}).addTo(flightLayer).bindTooltip("Sensitivity member "+member.member);}
    }
  }
  $("region-legend").hidden=!showRegion;
  $("member-legend").hidden=!showDots;
}
$("sensitivity-display").onchange=drawAllMap;
function fitFlight(){const points=allFlights().flatMap(e=>e.result.prediction.filter(s=>preferredTrack!=="ascent"||s.stage==="ascent").flatMap(s=>s.trajectory));if(points.length)map.fitBounds(L.latLngBounds(unwrapped(points)),{padding:[45,45],maxZoom:11});else map.setView(launchMarker.getLatLng(),8);}
$("fit-button").onclick=fitFlight;$("track-display").onchange=()=>{preferredTrack=$("track-display").value;drawAllMap();};$("endpoint-focus").onchange=()=>{preferredEndpoint=$("endpoint-focus").value;drawSelected();drawAllMap();};
function drawSeries(site){
  $("series-launches").replaceChildren();$("series-summary").hidden=!site?.series;$("series-launches").hidden=!site?.series;
  if(!site?.series)return;const entries=siteEntries(site);$("series-summary").textContent=entries.filter(e=>e.status==="completed").length+" / "+entries.length+" launches available · select a launch below to inspect its profile.";
  for(const entry of entries){const button=document.createElement("button");button.type="button";button.className="series-launch "+(entry.status==="failed"?"failed":"")+(site.selectedIndex===entry.index?" selected":"");button.textContent=(entry.ascent_rate?entry.ascent_rate+" m/s · ":"")+entry.launch_datetime.slice(5,16).replace("T"," ")+" UTC"+(entry.status==="failed"?" · unavailable":"");button.disabled=entry.status==="failed";button.title=entry.error||"Inspect this launch";button.onclick=()=>{site.selectedIndex=entry.index;drawSelected();drawAllMap();};$("series-launches").append(button);if(entry.status==="failed"){const note=document.createElement("span");note.className="launch-error";note.textContent=entry.error;$("series-launches").append(note);}}
}
function updateSondeLink(){
  const site=activeSite(),available=Boolean(selectedResult(site)&&site?.jobId);
  $("netcdf-exports").hidden=!available;
  if(available){refreshExportCard("model");refreshExportCard("sonde");}
}
$("sonde-scope").onchange=()=>refreshExportCard("sonde");
$("model-box-selection").onchange=()=>refreshExportCard("model");
$("model-box-scope").onchange=()=>refreshExportCard("model");
function drawArrivals(site){
  const area=$("arrival-results");area.replaceChildren();area.hidden=site?.series?.schedule_type!=="coordinated";
  if(area.hidden)return;
  const plan=site.series,title=document.createElement("h3"),note=document.createElement("p");
  title.textContent="Together at "+Number(plan.target_altitude).toLocaleString()+" m AMSL · "+utcText(plan.arrival_datetime);
  note.textContent="Launch order below · click a balloon to inspect its ascent and download its sonde NetCDF. The dotted map guide connects arrival positions; it does not imply a straight-line guarantee.";
  const wrap=document.createElement("div");wrap.className="arrival-table-wrap";const table=document.createElement("table"),head=document.createElement("thead"),header=document.createElement("tr");
  for(const label of ["Balloon","Ascent","Launch UTC","Arrival UTC","Position at altitude"]){const cell=document.createElement("th");cell.textContent=label;header.append(cell);}head.append(header);table.append(head);
  const body=document.createElement("tbody");
  for(const entry of [...plan.flights].sort((a,b)=>a.launch_datetime.localeCompare(b.launch_datetime))){
    const row=document.createElement("tr"),balloon=document.createElement("td"),button=document.createElement("button");button.type="button";button.textContent=entry.flight_name;button.disabled=entry.status!=="completed";
    button.onclick=()=>{const selected=sites.find(s=>s.jobId===site.jobId&&s.batchSiteIndex===entry.site_index);if(!selected)return;switchSite(selected.id);selected.selectedIndex=entry.index;drawSelected();drawAllMap();};balloon.append(button);row.append(balloon);
    const endpoint=entry.result?.summary.burst;
    const columns=[entry.ascent_rate+" m/s",utcText(entry.launch_datetime),endpoint?utcText(endpoint.datetime):"Unavailable",endpoint?endpoint.latitude.toFixed(4)+"°, "+endpoint.longitude.toFixed(4)+"°":entry.error];
    for(const text of columns){const cell=document.createElement("td");cell.textContent=text;row.append(cell);}if(site.selectedIndex===entry.index)row.className="selected";body.append(row);
  }
  table.append(body);wrap.append(table);const timetable=document.createElement("a");timetable.className="text-button";timetable.textContent="Download launch timetable CSV ↓";timetable.href="/api/predictions/"+site.jobId+"/export/schedule";timetable.setAttribute("download","");area.append(title,note,timetable,wrap);
}
function drawSelected(){
  const site=activeSite();result=selectedResult(site);jobId=site?.jobId;drawSeries(site);drawArrivals(site);renderTabs();
  if(hoverMarker){map.removeLayer(hoverMarker);hoverMarker=null;}
  const offline=$("offline-pack"),packKey=jobId?jobId+":"+(site.series?site.selectedIndex:0):"";
  const packAvailable=result&&["era5","era5t","gfs","ifs"].includes(result.weather.source)&&result.weather.native!==true;
  offline.disabled=!packAvailable||offline.dataset.working===packKey;
  if(offline.dataset.flight!==packKey){offline.dataset.flight=packKey;$("offline-pack-status").replaceChildren();}
  $("exports").hidden=!jobId;$("flight-result").hidden=!result;$("empty-result").hidden=Boolean(result);$("sounding-open").disabled=!result?.sounding;$("restore-sounding").disabled=!result?.sounding;updateSondeLink();
  document.querySelectorAll("[data-format]").forEach(link=>{link.href="/api/predictions/"+jobId+"/export/"+link.dataset.format;link.setAttribute("download","");});
  if(!result){soundingToken++;$("sounding-panel").hidden=true;$("sounding-panel").classList.remove("open");$("altitude-chart").replaceChildren();return;}
  const s=result.summary,p=endpoint(result);$("metric-endpoint-label").textContent=p===s.burst?(s.landing?"BURST":"TARGET"):"LANDING";
  $("metric-landing").textContent=p.latitude.toFixed(4)+"°, "+p.longitude.toFixed(4)+"°";$("metric-landing-time").textContent=utcText(p.datetime);
  $("metric-duration").textContent=duration(s.duration_seconds);$("metric-stages").textContent=duration(s.ascent_seconds)+" up"+(s.landing?" / "+duration(s.descent_seconds)+" down":" · ascent only");
  $("metric-drift").textContent=(p===s.burst?s.burst_drift_km:s.drift_km).toFixed(1)+" km";$("metric-burst").textContent=(s.landing?"Burst":"Target")+" at "+Math.round(s.burst.altitude).toLocaleString()+" m AMSL";
  $("warning-list").replaceChildren();const notes=[...result.warnings];if(result.ensemble_failed?.length)notes.push(result.ensemble_failed.length+" sensitivity members failed coverage or physics checks; see JSON.");
  const members=(result.ensemble||[]).map(endpoint);if(members.length){const lats=members.map(p=>p.latitude),lons=members.map(p=>p.longitude);notes.push("Sensitivity endpoint range: "+Math.min(...lats).toFixed(4)+" to "+Math.max(...lats).toFixed(4)+"°N, "+Math.min(...lons).toFixed(4)+" to "+Math.max(...lons).toFixed(4)+"°E ("+members.length+" members).");}
  for(const warning of notes){const li=document.createElement("li");li.textContent=warning;$("warning-list").append(li);}$("data-origins").textContent=JSON.stringify({model:result.weather.provenance,physical_ground:result.weather.physical_ground,below_model:result.weather.below_model},null,2);drawChart(result);
  if(!$("sounding-panel").hidden)refreshSounding();
  requestAnimationFrame(()=>map.invalidateSize());
}
function svgElement(name,attrs,text){const el=document.createElementNS("http://www.w3.org/2000/svg",name);for(const[key,value]of Object.entries(attrs))el.setAttribute(key,value);if(text!==undefined)el.textContent=text;return el;}
function drawChart(data) {
  const svg = $("altitude-chart"); svg.replaceChildren();
  const left=54,right=883,top=12,bottom=119,total=data.summary.duration_seconds;
  const maxAlt = Math.max(data.summary.burst.altitude,100) * 1.05, minAlt = Math.min(data.summary.launch.altitude,(data.summary.landing?.altitude ?? data.summary.launch.altitude),0);
  const x = seconds => left + seconds/Math.max(total,1)*(right-left);
  const y = altitude => bottom - (altitude-minAlt)/(maxAlt-minAlt)*(bottom-top);
  for (let i=0;i<=3;i++) {
    const altitude = minAlt + (maxAlt-minAlt)*i/3;
    svg.append(svgElement("line",{x1:left,x2:right,y1:y(altitude),y2:y(altitude),class:"chart-grid"}));
    svg.append(svgElement("text",{x:left-9,y:y(altitude)+3,"text-anchor":"end",class:"chart-label"},(altitude/1000).toFixed(0)+" km"));
  }
  const ticks=BPSensitivity.elapsedTicks(total);
  for(const time of ticks){
    const at=x(time);
    svg.append(svgElement("line",{x1:at,x2:at,y1:top,y2:bottom,class:"chart-grid"}));
    svg.append(svgElement("text",{x:at,y:141,"text-anchor":time===0?"start":at>right-24?"end":"middle",class:"chart-label"},total<180?time+" s":time/60+" min"));
  }
  for (const stage of data.prediction) {
    const points = stage.trajectory.map(p=>x(p.elapsed_seconds).toFixed(2)+","+y(p.altitude).toFixed(2)).join(" ");
    svg.append(svgElement("polyline",{points,fill:"none",stroke:stage.stage==="ascent"?"#d75b2c":"#337fba","stroke-width":2.5}));
  }
  const cursor = svgElement("line",{x1:left,x2:left,y1:top,y2:bottom,stroke:"#9caaa0","stroke-dasharray":"3,3",visibility:"hidden"});
  svg.append(cursor);
  const points = data.prediction.flatMap(s => s.trajectory), locations = unwrapped(points);
  svg.onpointermove = event => {
    const box = svg.getBoundingClientRect(), sx = (event.clientX-box.left)/box.width*900;
    const time = Math.max(0,Math.min(total,(sx-left)/(right-left)*total));
    let lo=0,hi=points.length-1;
    while (lo<hi) { const mid=(lo+hi)>>1; if(points[mid].elapsed_seconds<time)lo=mid+1;else hi=mid; }
    const p=points[lo]; cursor.setAttribute("x1",x(p.elapsed_seconds));cursor.setAttribute("x2",x(p.elapsed_seconds));cursor.setAttribute("visibility","visible");
    $("chart-hover").textContent = duration(p.elapsed_seconds)+" · "+Math.round(p.altitude).toLocaleString()+" m AMSL";
    if (!hoverMarker) hoverMarker = L.circleMarker(locations[lo],{radius:5,color:"#263f33",weight:2,fillColor:"white",fillOpacity:1}).addTo(map);
    else hoverMarker.setLatLng(locations[lo]);
  };
  svg.onpointerleave = () => {cursor.setAttribute("visibility","hidden");$("chart-hover").textContent="";if(hoverMarker){map.removeLayer(hoverMarker);hoverMarker=null;}};
}
function prepareForecast(source){
  $("download-source").value=source;$("download-source").onchange();
  let start=new Date(fromInput($("launch_datetime").value)||Date.now()),end;
  if($("run_mode").value==="arrival"&&$("arrival_datetime").value){
    end=new Date(fromInput($("arrival_datetime").value));let rates;try{rates=arrivalRates()||sites.map(site=>Number(site.setup.ascent_rate));}catch{rates=[2];}
    const lowest=Math.min(...rates),height=Math.max(0,Number($("arrival_altitude").value)-Number($("launch_altitude").value));
    start=new Date(end.getTime()-height/lowest*1000-3600000);end=new Date(end.getTime()+3600000);
  }else{
    if(start.getTime()<Date.now()-86400000)start=new Date(Date.now()+3600000);
    const hours=$("run_mode").value==="series"?Number($("series_duration").value)+Number($("max_duration_hours").value):Math.max(6,Number($("max_duration_hours").value));
    end=new Date(start.getTime()+hours*3600000);
  }
  $("download-start").value=minuteInput(start);$("download-end").value=minuteInput(end,true);
  const lat=Number($("launch_latitude").value),lon=(Number($("launch_longitude").value)+540)%360-180;
  for(const[id,value]of Object.entries({"area-n":Math.min(90,lat+5),"area-s":Math.max(-90,lat-5),"area-w":Math.max(-180,lon-8),"area-e":Math.min(180,lon+8)}))$(id).value=value.toFixed(2);
  $("download-name").value=source+"-"+start.toISOString().slice(0,16).replace(/[-:T]/g,"")+"-"+Date.now().toString(36).slice(-4);
  $("weather-dialog").showModal();loadStorage().catch(e=>status("storage-status",e.message,"error"));loadTerrain().catch(e=>status("terrain-status",e.message,"error"));
}
$("weather-open").onclick = async () => { $("weather-dialog").showModal(); try { await loadDatasets(); await loadStorage(); await loadTerrain(); } catch(e){status("weather-status",e.message,"error");} };
$("weather-close").onclick = () => $("weather-dialog").close();
$("download-source").onchange = () => {
  const source = $("download-source").value;
  const forecast=["ifs","gfs"].includes(source);$("cycle-field").hidden = !forecast; $("download-grid").disabled = forecast;$("era5-grid-field").hidden=forecast;
  let start, end;
  if (forecast) { start=new Date(); start.setUTCMinutes(0,0,0); start=new Date(start.getTime()+3600000); end=new Date(start.getTime()+12*3600000); }
  else if(source === "era5t") { start=new Date();start.setUTCDate(start.getUTCDate()-7);start.setUTCHours(0,0,0,0);end=new Date(start.getTime()+12*3600000); }
  else { start=new Date("2024-01-15T12:00:00Z");end=new Date("2024-01-16T00:00:00Z"); }
  $("download-start").value=minuteInput(start);$("download-end").value=minuteInput(end,true);
  $("download-name").value=source+"-uk-"+start.toISOString().slice(0,10).replaceAll("-","");
  $("source-note").textContent = forecast ? (source==="ifs"?"ECMWF IFS Open Data · fixed 0.25° grid: global selected fields, cropped locally. Pressure levels are sparse between 100, 50 and 10 hPa; trajectories use interpolation.":"NOAA GFS 0.25°: regional NOMADS subsets, including winds, temperature, humidity, heights and vertical motion.")+" No API key is needed. Choose a window covering the entire flight (or the earliest coordinated launch through arrival), up to 72 hours. Recent runs only; downloads are cached." : source==="era5t" ? "ERA5T is preliminary reanalysis, usually at least five days behind real time. CDS returns the available release by date; older periods may already be final." : "ERA5 is historical data. Your CDS credentials stay on the server. Accept the pressure-level and single-level dataset terms before downloading.";
};
$("weather-form").onsubmit = async e => {
  e.preventDefault(); $("download-button").disabled=true;
  try {
    const body={source:$("download-source").value,name:$("download-name").value,
      start:fromInput($("download-start").value),end:fromInput($("download-end").value),
      area:["area-n","area-w","area-s","area-e"].map(id=>Number($(id).value)),grid:["ifs","gfs"].includes($("download-source").value)?.25:Number($("download-grid").value),
      surface:$("download-surface").checked,refresh:$("download-refresh").checked};
    if(["ifs","gfs"].includes(body.source)&&$("download-cycle").value)body.cycle=fromInput($("download-cycle").value);
    $("weather-balloon").hidden=false;
    status("weather-status","Starting download…","working");
    const job=await api("/api/weather/download",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
    const info=await poll(job.id,message=>status("weather-status",message,"working"));
    await loadDatasets(info.id);await loadStorage();datasetChanged(true);invalidatePrediction();
    status("weather-status","Added "+info.label+". It is now selected for predictions.");
    toast("Weather dataset ready.");
  } catch(error){status("weather-status",error.message,"error");}
  finally{$("download-button").disabled=false;$("weather-balloon").hidden=true;}
};
$("share-button").onclick=async()=>{
  const request=flightRequest(),url=new URL(location.href);url.search="";
  for(const[key,value]of Object.entries(request))if(value!==null)url.searchParams.set(key,String(value));
  try{await navigator.clipboard.writeText(url.href);toast("Setup link copied. The recipient needs the same local weather dataset.");}
  catch{history.replaceState(null,"",url);toast("Setup saved in the address bar. Copy its URL.");}
};
$("rescan-weather").onclick=async()=>{
  $("rescan-weather").disabled=true;try{const job=await api("/api/weather/rescan",{method:"POST"});const r=await poll(job.id,message=>status("rescan-status",message,"working"));await loadDatasets();status("rescan-status","Local index refreshed."+(r.messages?.length?" "+r.messages.join(" · "):""));}catch(e){status("rescan-status",e.message,"error");}finally{$("rescan-weather").disabled=false;}
};
async function refreshSounding(){
  const token=++soundingToken,site=activeSite(),data=selectedResult(site);if(!data?.sounding)return;
  const index=site.series?site.selectedIndex:0,mode=$("sounding-mode").value;
  const base="/api/predictions/"+site.jobId+"/sounding",query=new URLSearchParams({flight_index:index,mode});
  $("sounding-popout").href="/assets/sounding.html?job="+site.jobId+"&"+query;$("sounding-csv").href=base+"/export/csv?"+query;
  try{const profile=mode==="ascent"?data.sounding:await api(base+"?"+query);if(token!==soundingToken||$("sounding-panel").hidden)return;window.BalloonPlots.render(profile,$("skewt-chart"),$("uv-chart"));$("sounding-note").textContent=profile.description+(profile.humidity_available?" Dew point from model humidity.":" Humidity is absent: temperature and winds only.");}catch(e){if(token===soundingToken)$("sounding-note").textContent=e.message;}
}
$("sounding-open").onclick=()=>{if(window.BalloonWindows)window.BalloonWindows.show("sounding-panel");else $("sounding-panel").hidden=false;refreshSounding();};
  document.addEventListener("bp:panelshown",event=>{if(event.detail.id==="sounding-panel"&&result?.sounding)refreshSounding();});
  document.addEventListener("bp:layout",()=>requestAnimationFrame(()=>map.invalidateSize({pan:false})));
$("sounding-close").onclick=()=>{soundingToken++;if(window.BalloonWindows)window.BalloonWindows.minimise("sounding-panel");else $("sounding-panel").hidden=true;};$("sounding-mode").onchange=refreshSounding;
function downloadJSON(name,value){const url=URL.createObjectURL(new Blob([JSON.stringify(value,null,2)],{type:"application/json"}));const link=document.createElement("a");link.href=url;link.download=name;document.body.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),10000);}
$("export-session").onclick=()=>{saveCurrent();downloadJSON("balloon-predictor-launch-setups.json",{application:"BalloonPredictor",version:"1.5.1",sites:sites.map(s=>({setup:s.setup}))});};
$("import-session").onchange=async()=>{
  try{const file=$("import-session").files[0];if(!file)return;if(file.size>1000000)throw new Error("Setup file must be under 1 MB.");const value=JSON.parse(await file.text());if(!Array.isArray(value.sites)||!value.sites.length||value.sites.length>20)throw new Error("Setup file must contain 1–20 launch points.");const defaults=readSetup();const added=value.sites.map(item=>{if(!item.setup||typeof item.setup!=="object")throw new Error("Invalid launch setup.");const setup={...defaults};for(const key of setupFields)if(item.setup[key]!==undefined)setup[key]=item.setup[key];if(!Number.isFinite(Number(setup.launch_latitude))||Math.abs(Number(setup.launch_latitude))>85||!Number.isFinite(Number(setup.launch_longitude)))throw new Error("Invalid launch coordinates.");return newSite(setup);});sites=added;activeId=sites[0].id;writeSetup(sites[0].setup);updateFields();datasetChanged(false);renderTabs();drawSelected();drawAllMap();persist();toast("Launch setups loaded.");}catch(e){status("flight-status",e.message,"error");}finally{$("import-session").value="";}
};
async function initialize(){
  $("launch_datetime").value=utcInput(new Date());$("arrival_datetime").value=utcInput(new Date(Date.now()+3*3600000));$("download-source").onchange();
  try{
    const params=new URLSearchParams(location.search);let saved=null;try{saved=JSON.parse(localStorage.getItem(STORAGE_KEY));}catch{}
    if(saved?.sites?.length&&saved.sites.length<=20){const defaults=readSetup();sites=saved.sites.map(s=>({...newSite({...defaults,...s.setup}),id:typeof s.id==="string"?s.id:newSite(defaults).id}));activeId=sites.some(s=>s.id===saved.activeId)?saved.activeId:sites[0].id;writeSetup(activeSite().setup);}
    await loadDatasets(params.get("dataset")||activeSite()?.setup.dataset);
    for(const[key,value]of params)if(key!=="dataset"&&setupFields.includes(key)){const field=$(key);if(key==="launch_datetime")field.value=utcInput(value);else if(field.type==="checkbox")field.checked=value==="true";else field.value=value;}
    if(!sites.length){const site=newSite(readSetup());sites=[site];activeId=site.id;}else activeSite().setup=readSetup();
    updateFields();$("descent_model").onchange();datasetChanged(false);renderTabs();drawSelected();drawAllMap();persist();
  }catch(e){status("flight-status","Could not connect to the local API: "+e.message,"error");}finally{$("predict-button").disabled=!sites.length||$("dataset").value.startsWith("online-");}
}
initialize();

function bytes(value){return value>=1024**3?(value/1024**3).toFixed(2)+" GB":(value/1024**2).toFixed(1)+" MB";}
async function loadStorage(){
  const data=await api("/api/storage");
  $("storage-paths").textContent="Saved on this server: "+data.data_dir+" · Weather: "+data.weather.path+" ("+bytes(data.weather.bytes)+") · Remote file cache: "+data.cache.path+" ("+bytes(data.cache.bytes)+") · Predictions: "+data.results.path+" ("+bytes(data.results.bytes)+") · Model boxes: "+data.exports.path+" ("+bytes(data.exports.bytes)+")";
  $("storage-config").textContent="Configuration on this server: "+(data.config_path||"not set")+(data.editable?"":" · The server operator can edit data_dir or set BP_DATA_DIR.");
  $("storage-log").textContent="Processing log on this server: "+(data.log_path||"see the server console");
  $("storage-location-form").hidden=!data.editable;
  if(data.editable)$("storage-location").value=data.pending_data_dir||data.data_dir;
  if(data.pending_data_dir)status("storage-status","Restart BalloonPredictor to use "+data.pending_data_dir+".");
  $("storage-datasets").replaceChildren();
  for(const item of data.datasets){const row=document.createElement("div"),label=document.createElement("span"),button=document.createElement("button");row.className="storage-dataset";label.textContent=item.id+" · "+bytes(item.bytes)+" · "+item.dependent_predictions+" dependent predictions";button.type="button";button.className="text-button danger-action";button.textContent="Delete dataset";button.onclick=async()=>{if(!confirm("Delete weather dataset '"+item.id+"' ("+bytes(item.bytes)+")? "+item.dependent_predictions+" saved predictions depend on it; their sampled sonde/model-box exports will require the weather data again. Raw cache stays."))return;await cleanStorage({target:"dataset",name:item.id,allow_orphan:true});};row.append(label,button);$("storage-datasets").append(row);}
}
$("storage-location-form").onsubmit=async e=>{
  e.preventDefault();
  try{
    const data=await api("/api/storage/location",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({path:$("storage-location").value})});
    status("storage-status",data.restart_required?"Saved "+data.data_dir+". Quit and reopen BalloonPredictor to use it.":"The current data folder is saved.");
  }catch(error){status("storage-status",error.message,"error");}
};
async function cleanStorage(body){try{const data=await api("/api/storage/cleanup",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});status("storage-status","Removed "+data.removed_files+" files · "+bytes(data.removed_bytes));if(body.target==="results"||body.target==="dataset"){preparedExports.clear();for(const site of sites)if(body.target==="results"||site.setup.dataset===body.name){site.result=null;site.series=null;site.jobId=null;}drawSelected();drawAllMap();}await loadDatasets();await loadStorage();}catch(e){status("storage-status",e.message,"error");}}
$("clear-cache").onclick=()=>{if(confirm("Clear all cached remote GRIB files? Saved weather datasets and predictions are kept. Future matching downloads will fetch remote files again."))cleanStorage({target:"cache"});};
$("clear-results").onclick=()=>{if(confirm("Delete all saved predictions and generated exports? Weather data and saved launch setups are kept."))cleanStorage({target:"results"});};
async function loadTerrain(){const data=await api("/api/terrain");$("terrain-path").textContent="Terrain tiles on this server: "+data.path;$("terrain-status").textContent=data.tiles.length?data.tiles.map(t=>t.id+" ("+bytes(t.bytes)+")").join(" · "):"No tiles yet. Download a launch tile or add your own GeoTIFFs.";}
$("refresh-terrain").onclick=()=>loadTerrain().catch(e=>status("terrain-status",e.message,"error"));
$("download-terrain").onclick=async()=>{const button=$("download-terrain");button.disabled=true;try{const job=await api("/api/terrain/download",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({latitude:Number($("launch_latitude").value),longitude:Number($("launch_longitude").value)})});await poll(job.id,m=>status("terrain-status",m,"working"));await loadTerrain();}catch(e){status("terrain-status",e.message,"error");}finally{button.disabled=false;}};
// Download state is tied to the prediction, selected track and scope. A save
// link from another flight can never be shown as the current flight's export.
const preparedExports=new Map();
function exportIdentity(kind){
  const site=activeSite();if(!site?.jobId||!selectedResult(site))return null;
  const selection=kind==="model"?$("model-box-selection").value:"flight";
  const index=selection==="plan"?null:site.series?site.selectedIndex:0;
  const scope=$(kind==="model"?"model-box-scope":"sonde-scope").value;
  return {key:JSON.stringify([kind,site.jobId,index,selection,scope]),jobId:site.jobId,index,selection,scope};
}
function refreshExportCard(kind){
  const identity=exportIdentity(kind),button=$(kind==="model"?"model-box-button":"sonde-netcdf"),output=$(kind==="model"?"model-box-status":"sonde-status");
  const record=identity&&preparedExports.get(identity.key);button.disabled=!identity||record?.state==="working";
  button.setAttribute("aria-busy",String(record?.state==="working"));output.replaceChildren();output.classList.remove("error");output.hidden=!record;
  if(!record)return;
  if(record.state==="completed"){
    const link=document.createElement("a");link.className="download-link";link.href=record.url;link.download=record.data.filename;
    link.textContent="Save "+record.data.filename+" ("+bytes(record.data.bytes)+") ↓";
    const note=document.createElement("span");note.className="export-description";
    note.textContent=record.data.warnings?.length?record.data.warnings.join(" "):kind==="model"?"Ready · original model grid, all available levels and bracketing times.":"Ready · simulated sonde for the selected flight, sampled every second.";
    output.append(link,note);
  }else{output.textContent=record.message;if(record.state==="failed")output.classList.add("error");}
}
async function prepareExport(kind){
  const identity=exportIdentity(kind);if(!identity)return;
  if(preparedExports.get(identity.key)?.state==="working")return;
  const record={state:"working",message:"Preparing "+(kind==="model"?"model box":"sonde")+" NetCDF…"};
  preparedExports.set(identity.key,record);refreshExportCard(kind);
  const stillSelected=()=>exportIdentity(kind)?.key===identity.key;
  try{
    const body={flight_index:identity.index??0,scope:identity.scope};
    if(kind==="model")Object.assign(body,{selection:identity.selection,include_ensemble:true,margin_degrees:.5,margin_km:50});
    const job=await api("/api/predictions/"+identity.jobId+(kind==="model"?"/model-box":"/sonde-export"),{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
    const data=await poll(job.id,message=>{record.message=message;if(stillSelected())refreshExportCard(kind);});
    Object.assign(record,{state:"completed",data,url:"/api/exports/"+job.id+"/download"});
    if(stillSelected()){
      refreshExportCard(kind);
      $(kind==="model"?"model-box-status":"sonde-status").querySelector(".download-link").click();
    }else toast("NetCDF ready for the previous selection. Reselect that flight and scope to save it.");
  }catch(e){record.state="failed";record.message=e.message;if(stillSelected())refreshExportCard(kind);}
}
$("model-box-button").onclick=()=>prepareExport("model");
$("sonde-netcdf").onclick=()=>prepareExport("sonde");

$("offline-pack").onclick=async()=>{
  const button=$("offline-pack"),output=$("offline-pack-status"),site=activeSite();
  if(!site?.jobId||!selectedResult(site))return;
  const prediction=site.jobId,index=site.series?site.selectedIndex:0, key=prediction+":"+index;
  const current=()=>button.dataset.flight===key;
  button.dataset.working=key;button.disabled=true;output.textContent="Preparing the regional forecast…";
  try{
    const job=await api("/api/predictions/"+prediction+"/offline-pack?flight_index="+index,{method:"POST"});
    const data=await poll(job.id,text=>{if(current())output.textContent=text;});
    if(!current())return;
    const link=document.createElement("a");
    link.href="/api/exports/"+job.id+"/download";link.download=data.filename;
    link.textContent="Save "+data.filename+" ("+bytes(data.bytes)+") ↓";
    output.replaceChildren(link);link.click();
  }catch(error){if(current())output.textContent=error.message;}
  finally{if(button.dataset.working===key)delete button.dataset.working;if(current())button.disabled=false;}
};
