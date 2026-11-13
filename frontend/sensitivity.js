/* A fixed geographic envelope around the nominal endpoint and its sensitivity members. */
(function (root) {
  "use strict";
  function longitudeNear(longitude, reference) {
    return reference + ((((longitude - reference + 180) % 360) + 360) % 360 - 180);
  }
  function roundedEnvelope(centers, radius) {
    if (centers.length < 2) return [];
    const vertices = 32, padded = radius / Math.cos(Math.PI / vertices);
    const ring = Array.from({length: vertices}, (_, i) => {
      const t = (i + .5) * 2 * Math.PI / vertices;
      return [padded * Math.cos(t), padded * Math.sin(t)];
    });
    const sorted = centers.flatMap(([x,y]) => ring.map(([dx,dy]) => [x+dx,y+dy]))
      .sort((a,b) => a[0]-b[0] || a[1]-b[1]);
    const cross = (a,b,c) => (b[0]-a[0])*(c[1]-a[1]) - (b[1]-a[1])*(c[0]-a[0]);
    function half(points) {
      const out = [];
      for (const p of points) {
        while (out.length > 1 && cross(out.at(-2),out.at(-1),p) <= 0) out.pop();
        out.push(p);
      }
      return out.slice(0,-1);
    }
    return half(sorted).concat(half(sorted.reverse()));
  }
  // Work in a local metric plane, with a fixed 1 km margin around endpoints.
  // Unwrap around the nominal endpoint to keep antimeridian flights together.
  function endpointRegion(nominal,members,radius=1000){
    if(members.length<2)return [];
    const lat=nominal.latitude,lon=nominal.longitude,degree=6371008.8*Math.PI/180;
    const east=degree*Math.cos(lat*Math.PI/180);
    const points=[nominal,...members].map(p=>[(longitudeNear(p.longitude,lon)-lon)*east,(p.latitude-lat)*degree]);
    return roundedEnvelope(points,radius).map(([x,y])=>[lat+y/degree,lon+x/east]);
  }
  function elapsedTicks(total){
    const steps=total<180?[1,2,5,10,15,20,30,45,60]:[60,120,300,600,900,1200,1800,2700,3600,5400,7200,10800,14400,21600,43200];
    const step=steps.find(n=>total/n<=6)||steps.at(-1);
    return Array.from({length:Math.floor(total/step)+1},(_,i)=>i*step);
  }
  const api = {longitudeNear,roundedEnvelope,endpointRegion,elapsedTicks};
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.BPSensitivity = api;
})(globalThis);
