/* Bundled Natural Earth land and coordinate grid; no tile or network requests. */
(()=>{
 'use strict';
 window.BalloonOfflineMap=function(map){
   const land=L.layerGroup().addTo(map),grid=L.layerGroup().addTo(map),labels=L.layerGroup().addTo(map);
   for(const line of window.ERA5Coastlines?.lines||[]){
     const points=line.points.map(([lon,lat])=>[lat,lon]),first=points[0],last=points.at(-1);
     if(points.length<3)continue;
     const closed=Math.abs(first[0]-last[0])<.001&&Math.abs(first[1]-last[1])<.001;
     const options={color:'#94a896',weight:1,interactive:false};
     if(closed&&line.bounds[2]-line.bounds[0]<180)L.polygon(points,{...options,fillColor:'#edf1e5',fillOpacity:1}).addTo(land);
     else L.polyline(points,options).addTo(land);
   }
   const places=[['London',51.51,-.13],['Cardiff',51.48,-3.18],['Bangor',53.23,-4.13],['Llanbedr',52.82,-4.12],['Manchester',53.48,-2.24],['Edinburgh',55.95,-3.19],['Dublin',53.35,-6.26],['Belfast',54.60,-5.93],['Paris',48.86,2.35],['Berlin',52.52,13.41],['Oslo',59.91,10.75],['Reykjavík',64.15,-21.94],['New York',40.71,-74.01],['Sydney',-33.87,151.21],['Tokyo',35.68,139.69]];
   function draw(){
     grid.clearLayers();labels.clearLayers();const b=map.getBounds(),span=b.getNorth()-b.getSouth(),step=span>100?30:span>40?10:span>15?5:span>5?2:span>2?1:.5;
     for(let lat=Math.ceil(Math.max(-85,b.getSouth())/step)*step;lat<Math.min(85,b.getNorth())&&lat<=90;lat+=step)L.polyline([[lat,b.getWest()],[lat,b.getEast()]],{color:'#8da8ae',weight:.6,opacity:.5,interactive:false}).addTo(grid);
     const start=Math.ceil(b.getWest()/step)*step,end=Math.min(b.getEast(),start+step*100);
     for(let lon=start;lon<end;lon+=step)L.polyline([[b.getSouth(),lon],[b.getNorth(),lon]],{color:'#8da8ae',weight:.6,opacity:.5,interactive:false}).addTo(grid);
     if(map.getZoom()>=5)for(const [name,lat,lon] of places)if(b.contains([lat,lon]))L.circleMarker([lat,lon],{radius:2,color:'#5d766b',weight:1,interactive:false}).bindTooltip(name,{permanent:true,direction:'right',className:'offline-place'}).addTo(labels);
   }
   map.attributionControl.addAttribution('Natural Earth · offline coastline map');map.on('moveend resize',draw);draw();
 };
})();
