"use strict";
const params=new URLSearchParams(location.search),job=params.get("job"),index=Number(params.get("flight_index")||0),$=id=>document.getElementById(id);
let exportURL=null,loadToken=0,exportFrame=0;
$("sounding-mode").value=params.get("mode")==="launch"?"launch":"ascent";
function exportPlots(){
  if(exportURL)URL.revokeObjectURL(exportURL);
  const serializer=new XMLSerializer(),a=serializer.serializeToString($("skewt-chart")),b=serializer.serializeToString($("uv-chart"));
  const svg='<svg xmlns="http://www.w3.org/2000/svg" width="1160" height="670" viewBox="0 0 1160 670"><rect width="1160" height="670" fill="white"/>'+a.replace('<svg','<svg x="0" y="0" width="720" height="620"')+b.replace('<svg','<svg x="720" y="0" width="440" height="620"')+'<text x="35" y="650" font-size="12" font-family="sans-serif" fill="#284e42">BalloonPredictor v1.6 · Temperature (orange), dew point (green), u (blue), v (purple); barbs in knots</text></svg>';
  exportURL=URL.createObjectURL(new Blob([svg],{type:"image/svg+xml"}));$("save-plots").href=exportURL;$("save-plots").download="balloon-sounding.svg";
}
document.addEventListener("bp:plotchange",()=>{cancelAnimationFrame(exportFrame);exportFrame=requestAnimationFrame(exportPlots);});
async function load(){
  const token=++loadToken;
  try{
    if(!/^[a-f0-9]{32}$/.test(job||""))throw new Error("Open a sounding from a completed flight.");
    const query=new URLSearchParams({flight_index:index,mode:$("sounding-mode").value});
    const response=await fetch("/api/predictions/"+job+"/sounding?"+query),profile=await response.json();
    if(token!==loadToken)return;
    if(!response.ok)throw new Error(profile.detail||"Sounding unavailable.");
    window.BalloonPlots.render(profile,$("skewt-chart"),$("uv-chart"));
    $("sounding-note").textContent=profile.description+(profile.humidity_available?" Dew point from model humidity.":" Humidity is absent: temperature and winds only.");
    $("sounding-csv").href="/api/predictions/"+job+"/sounding/export/csv?"+query;
    $("sounding-netcdf").href="/api/predictions/"+job+"/sonde.nc?"+new URLSearchParams({flight_index:index,scope:"ascent"});
  }catch(error){if(token===loadToken)$("sounding-note").textContent=error.message;}
}
$("sounding-mode").onchange=load;$("print-sounding").onclick=()=>window.print();load();
