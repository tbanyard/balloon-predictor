"use strict";
const $ = id => document.getElementById(id);
const STORAGE_KEY = "balloon-predictor-v1.1-setups";
const numericFields = ["launch_latitude","launch_longitude","launch_altitude","ascent_rate","descent_rate","burst_altitude","landing_altitude","time_step","max_duration_hours","ensemble_members","ascent_sigma","descent_sigma","burst_sigma","wind_sigma","seed"];
const setupFields = ["dataset","launch_datetime","flight_name","flight_mode","descent_model","landing_mode","use_vertical_wind",...numericFields,"run_mode","series_duration","series_interval","series_sites"];
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
function status(id,message,kind=""){ $(id).textContent=message; $(id).className="status-message "+kind; }
function toast(message){ $("toast").textContent=message; $("toast").hidden=false; clearTimeout(toastTimer); toastTimer=setTimeout(()=>$("toast").hidden=true,3500); }
function utcInput(value){return new Date(value).toISOString().slice(0,19);}
function fromInput(value){return value?new Date(value+"Z").toISOString():null;}
function duration(seconds){const m=Math.round(seconds/60);return(m>=60?Math.floor(m/60)+"h ":"")+(m%60)+"m";}
function utcText(value){return new Date(value).toISOString().replace("T"," ").replace(".000Z"," UTC");}
async function api(path,options){
  const response=await fetch(path,options); let data;
  try{data=await response.json();}catch{throw new Error("Server returned an unreadable response.");}
  if(!response.ok){const d=data.detail;throw new Error(Array.isArray(d)?d.map(e=>e.loc.slice(1).join(".")+": "+e.msg).join("; "):d||"Request failed.");}
  return data;
}
async function poll(id,update){for(;;){const job=await api("/api/jobs/"+id);update(job.message,job.status);if(job.status==="failed")throw new Error(job.error);if(job.status==="completed")return job.result;await new Promise(resolve=>setTimeout(resolve,500));}}
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
function switchSite(id){saveCurrent();activeId=id;const site=activeSite();writeSetup(site.setup);updateFields();datasetChanged(false);renderTabs();drawSelected();drawAllMap();persist();status("flight-status",site.series?"Series loaded · "+siteEntries(site).length+" launches":site.result?"Saved flight loaded.":"Ready to calculate this launch point.");}
function invalidatePrediction(){
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
for(const key of ["launch_latitude","launch_longitude"])$(key).addEventListener("change",()=>setLocation(Number($("launch_latitude").value),Number($("launch_longitude").value)));
$("map-hint").onclick=()=>toast("Click the map or drag the active green launch point.");
function updateFields(){
  $("landing-field").hidden=$("landing_mode").value!=="fixed";
  const ascent=$("flight_mode").value==="ascent";
  $("altitude-label").textContent=ascent?"Target altitude":"Burst altitude";
  $("descent_rate").disabled=ascent;$("descent_model").disabled=ascent;
  if(ascent)$("endpoint-focus").value="burst";
  $("schedule-fields").hidden=$("run_mode").value!=="series";
  const count=Math.max(1,Math.ceil(Number($("series_duration").value)/Number($("series_interval").value)-1e-10));
  $("schedule-note").textContent=count+" launches per point · first at launch time · end of window excluded.";
  $("predict-button").querySelector("span").textContent=$("run_mode").value==="series"?"Calculate launch series":"Calculate flight";
}
$("landing_mode").onchange=updateFields;$("flight_mode").onchange=updateFields;$("run_mode").onchange=updateFields;
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
  const selected=selectId||$("dataset").value||"era5";$("dataset").replaceChildren();$("dataset-list").replaceChildren();
  for(const info of [...datasets.filter(i=>i.id==="era5"),...datasets.filter(i=>i.id!=="era5"&&i.id!=="demo"),...datasets.filter(i=>i.id==="demo")]){
    const option=document.createElement("option");option.value=info.id;option.textContent=info.label;$("dataset").append(option);
    const card=document.createElement("div");card.className="dataset-card";
    const tag=document.createElement("span");tag.className="source-tag";tag.textContent=info.source;
    const text=document.createElement("div"),name=document.createElement("strong"),desc=document.createElement("small");name.textContent=info.label;desc.textContent=coverageText(info);text.append(name,desc);
    const use=document.createElement("button");use.type="button";use.className="text-button";use.textContent="Use dataset →";use.onclick=()=>{$("dataset").value=info.id;datasetChanged(true);invalidatePrediction();$("weather-dialog").close();};
    card.append(tag,text,use);$("dataset-list").append(card);
  }
  $("dataset").value=datasets.some(d=>d.id===selected)?selected:"era5";datasetChanged(false);
}
function datasetChanged(adjustDate){
  const info=datasets.find(d=>d.id===$("dataset").value);if(!info)return;
  $("model-badge").textContent=info.source==="demo"?"DEMO WEATHER":info.source.toUpperCase()+" · "+(info.native?"LOCAL FILES":info.id);
  $("demo-notice").hidden=info.source!=="demo";$("coverage").textContent=coverageText(info);coverageLayer.clearLayers();
  if(info.area){const[n,w,s,e]=info.area;L.rectangle([[s,w],[n,e]],{color:"#688371",weight:1,dashArray:"5,6",fill:false,interactive:false}).addTo(coverageLayer);}
  if(adjustDate&&info.start){const chosen=fromInput($("launch_datetime").value);if(!chosen||chosen<info.start||chosen>info.end)$("launch_datetime").value=utcInput(info.start);}
  if(info.terrain_available===false){$("landing_mode").value="fixed";updateFields();}
  if(!info.vertical_wind_available)$("use_vertical_wind").checked=false;
  $("available-days-field").hidden=!info.dates?.length;$("available-day").replaceChildren();
  for(const date of info.dates||[]){const opt=document.createElement("option");opt.value=date;opt.textContent=date;$("available-day").append(opt);}
  const day=$("launch_datetime").value.slice(0,10);$("available-day").value=info.dates?.includes(day)?day:info.dates?.[0]||"";
  launchMarker.setLatLng([Number($("launch_latitude").value),Number($("launch_longitude").value)]);
  if(info.dates?.includes(day))updateAvailableTimes(day,false);else $("available-times").textContent=info.dates?.length?"Choose a listed date, or type any date to check coverage.":"";
}
let timesToken=0;
async function updateAvailableTimes(day,adjust){
  const token=++timesToken;try{const data=await api("/api/weather/"+encodeURIComponent($("dataset").value)+"/times?date="+day);if(token!==timesToken)return;const times=data.times.map(t=>t.datetime);$("available-times").textContent=times.length?"Records: "+times.map(t=>t.slice(11,16)).join(", ")+" UTC · interpolation between adjacent records":"No records on this date.";if(adjust&&times.length){$("launch_datetime").value=utcInput(times[0]);invalidatePrediction();}}catch(e){if(token===timesToken)$("available-times").textContent=e.message;}
}
$("available-day").onchange=()=>updateAvailableTimes($("available-day").value,true);
$("launch_datetime").addEventListener("change",()=>{const day=$("launch_datetime").value.slice(0,10);if(day&&datasets.find(d=>d.id===$("dataset").value)?.native)updateAvailableTimes(day,false);});
$("dataset").onchange=()=>datasetChanged(true);
$("ground-button").onclick=async()=>{try{const query=new URLSearchParams({latitude:$("launch_latitude").value,longitude:$("launch_longitude").value,when:fromInput($("launch_datetime").value)});const data=await api("/api/weather/"+encodeURIComponent($("dataset").value)+"/terrain?"+query);$("launch_altitude").value=Math.ceil(data.altitude+1);invalidatePrediction();toast("Launch altitude set to model ground + 1 m.");}catch(e){status("flight-status",e.message,"error");}};
function requestFromSetup(setup){const body={dataset:setup.dataset,launch_datetime:fromInput(setup.launch_datetime),flight_name:setup.flight_name||"Launch",flight_mode:setup.flight_mode||"full",descent_model:setup.descent_model,landing_mode:setup.landing_mode,use_vertical_wind:Boolean(setup.use_vertical_wind)};for(const key of numericFields)body[key]=Number(setup[key]);return body;}
function flightRequest(){return requestFromSetup(readSetup());}
$("flight-form").onsubmit=async e=>{
  e.preventDefault();if(running)return;saveCurrent();const origin=activeSite();const isSeries=$("run_mode").value==="series";
  const chosen=isSeries&&$("series_sites").value==="all"?[...sites]:[origin];const revisions=chosen.map(s=>s.revision);
  let body,path;if(isSeries){body={flights:chosen.map(s=>requestFromSetup(s.setup)),duration_hours:Number($("series_duration").value),interval_hours:Number($("series_interval").value)};path="/api/predictions/series";}else{body=flightRequest();path="/api/predictions";}
  running=true;$("predict-button").disabled=true;
  try{
    status("flight-status","Preparing local weather and calculating…","working");
    const job=await api(path,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
    const completed=await poll(job.id,message=>status("flight-status",message,"working"));let accepted=0;
    chosen.forEach((site,i)=>{if(!sites.includes(site)||site.revision!==revisions[i])return;site.jobId=job.id;if(isSeries){site.series=completed;site.batchSiteIndex=i;site.selectedIndex=completed.flights.find(x=>x.site_index===i&&x.status==="completed")?.index??-1;site.result=null;}else{site.result=completed;site.series=null;}accepted++;});
    drawSelected();drawAllMap();fitFlight();
    status("flight-status",!accepted?"Setup changed during calculation. Calculate again.":isSeries?completed.completed+" of "+completed.total+" launches completed"+(completed.failed?" · "+completed.failed+" unavailable; see launch list.":""):"Flight complete · "+completed.weather.label);
  }catch(error){status("flight-status",error.message,"error");}finally{running=false;$("predict-button").disabled=false;}
};
function unwrapped(points){let previous=null;return points.map(p=>{let lon=p.longitude;if(previous!==null)lon=previous+((lon-previous+540)%360-180);previous=lon;return[p.latitude,lon];});}
function allFlights(){return sites.flatMap((site,si)=>site.series?siteEntries(site).filter(e=>e.status==="completed").map(e=>({site,si,index:e.index,result:e.result})):site.result?[{site,si,index:0,result:site.result}]:[]);}
function endpoint(data){return $("endpoint-focus").value==="burst"||!data.summary.landing?data.summary.burst:data.summary.landing;}
function drawAllMap(){
  flightLayer.clearLayers();siteLayer.clearLayers();
  for(const site of sites)if(site.id!==activeId){const marker=L.circleMarker([Number(site.setup.launch_latitude),Number(site.setup.launch_longitude)],{radius:6,color:"#28645a",weight:2,fillOpacity:.5}).addTo(siteLayer);marker.bindTooltip(document.createTextNode(site.setup.flight_name));marker.on("click",e=>{L.DomEvent.stopPropagation(e.originalEvent||e);switchSite(site.id);});}
  for(const entry of allFlights()){
    const selected=entry.site.id===activeId&&(!entry.site.series||entry.index===entry.site.selectedIndex);const data=entry.result;const color=palette[(entry.si+entry.index)%palette.length];
    for(const stage of data.prediction){if(stage.stage==="descent"&&$("track-display").value==="ascent")continue;const line=L.polyline(unwrapped(stage.trajectory),{color:selected?(stage.stage==="ascent"?"#d75b2c":"#337fba"):color,weight:selected?3.5:1.8,opacity:selected?.98:.5,dashArray:stage.stage==="descent"?"5,4":null}).addTo(flightLayer);line.bindTooltip(document.createTextNode(entry.site.setup.flight_name+" · "+utcText(data.summary.launch.datetime)));line.on("click",e=>{L.DomEvent.stopPropagation(e.originalEvent||e);switchSite(entry.site.id);entry.site.selectedIndex=entry.index;drawSelected();drawAllMap();});}
    const p=endpoint(data);L.circleMarker([p.latitude,p.longitude],{radius:selected?6:3.5,color:selected?"white":color,weight:1.5,fillColor:selected?"#d75b2c":color,fillOpacity:.9}).addTo(flightLayer).bindTooltip(document.createTextNode((p===data.summary.burst?"Target / burst":"Landing")+" · "+Math.round(p.altitude).toLocaleString()+" m · "+utcText(p.datetime)));
    for(const member of data.ensemble||[]){const q=endpoint(member);L.circleMarker([q.latitude,q.longitude],{radius:2.8,color:color,weight:.8,fillOpacity:.4}).addTo(flightLayer).bindTooltip("Sensitivity member "+member.member);}
  }
}
function fitFlight(){const points=allFlights().flatMap(e=>e.result.prediction.filter(s=>$("track-display").value!=="ascent"||s.stage==="ascent").flatMap(s=>s.trajectory));if(points.length)map.fitBounds(L.latLngBounds(unwrapped(points)),{padding:[45,45],maxZoom:11});else map.setView(launchMarker.getLatLng(),8);}
$("fit-button").onclick=fitFlight;$("track-display").onchange=()=>{drawAllMap();};$("endpoint-focus").onchange=()=>{drawSelected();drawAllMap();};
function drawSeries(site){
  $("series-launches").replaceChildren();$("series-summary").hidden=!site?.series;$("series-launches").hidden=!site?.series;
  if(!site?.series)return;const entries=siteEntries(site);$("series-summary").textContent=entries.filter(e=>e.status==="completed").length+" / "+entries.length+" launches available · select a launch below to inspect its profile.";
  for(const entry of entries){const button=document.createElement("button");button.type="button";button.className="series-launch "+(entry.status==="failed"?"failed":"")+(site.selectedIndex===entry.index?" selected":"");button.textContent=entry.launch_datetime.slice(5,16).replace("T"," ")+" UTC"+(entry.status==="failed"?" · unavailable":"");button.disabled=entry.status==="failed";button.title=entry.error||"Inspect this launch";button.onclick=()=>{site.selectedIndex=entry.index;drawSelected();drawAllMap();};$("series-launches").append(button);if(entry.status==="failed"){const note=document.createElement("span");note.className="launch-error";note.textContent=entry.error;$("series-launches").append(note);}}
}
function drawSelected(){
  const site=activeSite();result=selectedResult(site);jobId=site?.jobId;drawSeries(site);renderTabs();
  if(hoverMarker){map.removeLayer(hoverMarker);hoverMarker=null;}
  $("exports").hidden=!jobId;$("flight-result").hidden=!result;$("empty-result").hidden=Boolean(result);$("sounding-open").disabled=!result?.sounding;
  document.querySelectorAll("[data-format]").forEach(link=>{link.href="/api/predictions/"+jobId+"/export/"+link.dataset.format;link.setAttribute("download","");});
  if(!result){soundingToken++;$("sounding-panel").hidden=true;$("sounding-panel").classList.remove("open");$("altitude-chart").replaceChildren();return;}
  const s=result.summary,p=endpoint(result);$("metric-endpoint-label").textContent=p===s.burst?"TARGET / BURST":"LANDING";
  $("metric-landing").textContent=p.latitude.toFixed(4)+"°, "+p.longitude.toFixed(4)+"°";$("metric-landing-time").textContent=utcText(p.datetime);
  $("metric-duration").textContent=duration(s.duration_seconds);$("metric-stages").textContent=duration(s.ascent_seconds)+" up"+(s.landing?" / "+duration(s.descent_seconds)+" down":" · ascent only");
  $("metric-drift").textContent=(p===s.burst?s.burst_drift_km:s.drift_km).toFixed(1)+" km";$("metric-burst").textContent=(s.landing?"Burst":"Target")+" at "+Math.round(s.burst.altitude).toLocaleString()+" m AMSL";
  $("warning-list").replaceChildren();const notes=[...result.warnings];if(result.ensemble_failed?.length)notes.push(result.ensemble_failed.length+" sensitivity members failed coverage or physics checks; see JSON.");
  const members=(result.ensemble||[]).map(endpoint);if(members.length){const lats=members.map(p=>p.latitude),lons=members.map(p=>p.longitude);notes.push("Sensitivity endpoint range: "+Math.min(...lats).toFixed(4)+" to "+Math.max(...lats).toFixed(4)+"°N, "+Math.min(...lons).toFixed(4)+" to "+Math.max(...lons).toFixed(4)+"°E ("+members.length+" members).");}
  for(const warning of notes){const li=document.createElement("li");li.textContent=warning;$("warning-list").append(li);}$("provenance").textContent=JSON.stringify(result.weather.provenance,null,2);drawChart(result);
  if(!$("sounding-panel").hidden)refreshSounding();
  requestAnimationFrame(()=>map.invalidateSize());
}
function svgElement(name,attrs,text){const el=document.createElementNS("http://www.w3.org/2000/svg",name);for(const[key,value]of Object.entries(attrs))el.setAttribute(key,value);if(text!==undefined)el.textContent=text;return el;}
function drawChart(data) {
  const svg = $("altitude-chart"); svg.replaceChildren();
  const left=54,right=883,top=12,bottom=119,total=data.summary.duration_seconds;
  const maxAlt = Math.max(data.summary.burst.altitude,100) * 1.05, minAlt = Math.min(data.summary.launch.altitude,(data.summary.landing?.altitude ?? data.summary.launch.altitude),0);
  const x = seconds => left + seconds/total*(right-left);
  const y = altitude => bottom - (altitude-minAlt)/(maxAlt-minAlt)*(bottom-top);
  for (let i=0;i<=3;i++) {
    const altitude = minAlt + (maxAlt-minAlt)*i/3;
    svg.append(svgElement("line",{x1:left,x2:right,y1:y(altitude),y2:y(altitude),class:"chart-grid"}));
    svg.append(svgElement("text",{x:left-9,y:y(altitude)+3,"text-anchor":"end",class:"chart-label"},(altitude/1000).toFixed(0)+" km"));
    const time = total*i/3;
    svg.append(svgElement("text",{x:x(time),y:141,"text-anchor":i===0?"start":i===3?"end":"middle",class:"chart-label"},Math.round(time/60)+" min"));
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
$("weather-open").onclick = async () => { $("weather-dialog").showModal(); try { await loadDatasets(); } catch(e){status("weather-status",e.message,"error");} };
$("weather-close").onclick = () => $("weather-dialog").close();
$("download-source").onchange = () => {
  const source = $("download-source").value;
  $("cycle-field").hidden = source !== "ifs"; $("download-grid").disabled = source === "ifs";
  let start, end;
  if (source === "ifs") { start=new Date(); start.setUTCMinutes(0,0,0); start=new Date(start.getTime()+3600000); end=new Date(start.getTime()+12*3600000); }
  else if(source === "era5t") { start=new Date();start.setUTCDate(start.getUTCDate()-7);start.setUTCHours(0,0,0,0);end=new Date(start.getTime()+12*3600000); }
  else { start=new Date("2024-01-15T12:00:00Z");end=new Date("2024-01-16T00:00:00Z"); }
  $("download-start").value=utcInput(start);$("download-end").value=utcInput(end);
  $("download-name").value=source+"-uk-"+start.toISOString().slice(0,10).replaceAll("-","");
  $("source-note").textContent = source==="ifs" ? "Operational IFS forecasts use ECMWF open data without CDS credentials. Global selected fields are downloaded and cropped locally. High-altitude pressure levels are sparse." : source==="era5t" ? "ERA5T is preliminary reanalysis, usually at least five days behind real time. CDS returns the available release by date; older periods may already be final." : "ERA5 is historical data. Your CDS credentials stay on the server. Accept the pressure-level and single-level dataset terms before downloading.";
};
$("weather-form").onsubmit = async e => {
  e.preventDefault(); $("download-button").disabled=true;
  try {
    const body={source:$("download-source").value,name:$("download-name").value,
      start:fromInput($("download-start").value),end:fromInput($("download-end").value),
      area:["area-n","area-w","area-s","area-e"].map(id=>Number($(id).value)),grid:Number($("download-grid").value),
      surface:$("download-surface").checked,refresh:$("download-refresh").checked};
    if(body.source==="ifs"&&$("download-cycle").value)body.cycle=fromInput($("download-cycle").value);
    status("weather-status","Starting download…","working");
    const job=await api("/api/weather/download",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
    const info=await poll(job.id,message=>status("weather-status",message,"working"));
    await loadDatasets(info.id);datasetChanged(true);invalidatePrediction();
    status("weather-status","Added "+info.label+". It is now selected for predictions.");
    toast("Weather dataset ready.");
  } catch(error){status("weather-status",error.message,"error");}
  finally{$("download-button").disabled=false;}
};
$("share-button").onclick=async()=>{
  const request=flightRequest(),url=new URL(location.href);url.search="";
  for(const[key,value]of Object.entries(request))url.searchParams.set(key,String(value));
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
$("sounding-open").onclick=()=>{$("sounding-panel").hidden=false;refreshSounding();requestAnimationFrame(()=>map.invalidateSize());};
$("sounding-close").onclick=()=>{soundingToken++;$("sounding-panel").hidden=true;};$("sounding-mode").onchange=refreshSounding;
function downloadJSON(name,value){const url=URL.createObjectURL(new Blob([JSON.stringify(value,null,2)],{type:"application/json"}));const link=document.createElement("a");link.href=url;link.download=name;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
$("export-session").onclick=()=>{saveCurrent();downloadJSON("balloon-predictor-launch-setups.json",{application:"BalloonPredictor",version:"1.1",sites:sites.map(s=>({setup:s.setup}))});};
$("import-session").onchange=async()=>{
  try{const file=$("import-session").files[0];if(!file)return;if(file.size>1000000)throw new Error("Setup file must be under 1 MB.");const value=JSON.parse(await file.text());if(!Array.isArray(value.sites)||!value.sites.length||value.sites.length>20)throw new Error("Setup file must contain 1–20 launch points.");const defaults=readSetup();const added=value.sites.map(item=>{if(!item.setup||typeof item.setup!=="object")throw new Error("Invalid launch setup.");const setup={...defaults};for(const key of setupFields)if(item.setup[key]!==undefined)setup[key]=item.setup[key];if(!Number.isFinite(Number(setup.launch_latitude))||Math.abs(Number(setup.launch_latitude))>85||!Number.isFinite(Number(setup.launch_longitude)))throw new Error("Invalid launch coordinates.");return newSite(setup);});sites=added;activeId=sites[0].id;writeSetup(sites[0].setup);updateFields();datasetChanged(false);renderTabs();drawSelected();drawAllMap();persist();toast("Launch setups loaded.");}catch(e){status("flight-status",e.message,"error");}finally{$("import-session").value="";}
};
async function initialize(){
  $("launch_datetime").value=utcInput(new Date());$("download-source").onchange();
  try{
    const params=new URLSearchParams(location.search);let saved=null;try{saved=JSON.parse(localStorage.getItem(STORAGE_KEY));}catch{}
    if(saved?.sites?.length&&saved.sites.length<=20){const defaults=readSetup();sites=saved.sites.map(s=>({...newSite({...defaults,...s.setup}),id:typeof s.id==="string"?s.id:newSite(defaults).id}));activeId=sites.some(s=>s.id===saved.activeId)?saved.activeId:sites[0].id;writeSetup(activeSite().setup);}
    await loadDatasets(params.get("dataset")||$("dataset").value||"era5");
    for(const[key,value]of params)if(setupFields.includes(key)){const field=$(key);if(key==="launch_datetime")field.value=utcInput(value);else if(field.type==="checkbox")field.checked=value==="true";else field.value=value;}
    if(!$("dataset").value)$("dataset").value="era5";
    if(!sites.length){const site=newSite(readSetup());sites=[site];activeId=site.id;}else activeSite().setup=readSetup();
    updateFields();$("descent_model").onchange();datasetChanged(false);renderTabs();drawSelected();drawAllMap();persist();
  }catch(e){status("flight-status","Could not connect to the local API: "+e.message,"error");}finally{$("predict-button").disabled=!sites.length;}
}
initialize();
