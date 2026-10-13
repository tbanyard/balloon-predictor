"use strict";
const params=new URLSearchParams(location.search),job=params.get("job"),index=Number(params.get("flight_index")||0),$=id=>document.getElementById(id);let exportURL=null;
$("sounding-mode").value=params.get("mode")==="launch"?"launch":"ascent";
async function load(){
  const query=new URLSearchParams({flight_index:index,mode:$("sounding-mode").value});
  try{if(!/^[a-f0-9]{32}$/.test(job||""))throw new Error("Open a sounding from a completed flight.");const response=await fetch("/api/predictions/"+job+"/sounding?"+query),profile=await response.json();if(!response.ok)throw new Error(profile.detail||"Sounding unavailable.");window.BalloonPlots.render(profile,$("skewt-chart"),$("uv-chart"));$("sounding-note").textContent=profile.description+(profile.humidity_available?" Dew point from model humidity.":" Humidity is absent: temperature and winds only.");$("sounding-csv").href="/api/predictions/"+job+"/sounding/export/csv?"+query;
    if(exportURL)URL.revokeObjectURL(exportURL);const serializer=new XMLSerializer(),a=serializer.serializeToString($("skewt-chart")),b=serializer.serializeToString($("uv-chart"));const svg='<svg xmlns="http://www.w3.org/2000/svg" width="880" height="550" viewBox="0 0 880 550"><rect width="880" height="550" fill="white"/>'+a.replace('<svg','<svg x="0" y="0" width="520" height="500"')+b.replace('<svg','<svg x="520" y="0" width="360" height="500"')+'<text x="30" y="535" font-size="13" font-family="sans-serif">BalloonPredictor v1.1 · Temperature (orange), dew point (green), u (blue), v (purple)</text></svg>';exportURL=URL.createObjectURL(new Blob([svg],{type:"image/svg+xml"}));$("save-plots").href=exportURL;$("save-plots").download="balloon-sounding.svg";
  }catch(e){$("sounding-note").textContent=e.message;}
}
$("sounding-mode").onchange=load;$("print-sounding").onclick=()=>window.print();load();
