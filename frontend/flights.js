/* Portable results and durable local history. No weather grids are serialized. */
(()=>{
  'use strict';const FORMAT='BalloonPredictor saved flights',MAX=32*1024*1024;
  let db=null,chain=Promise.resolve(),native=false,loaded=null;
  function open(){if(db)return db;db=new Promise((resolve,reject)=>{if(!window.indexedDB)return reject(Error('Local storage unavailable'));const request=indexedDB.open('balloon-predictor-flights',1);request.onupgradeneeded=()=>request.result.createObjectStore('data');request.onsuccess=()=>resolve(request.result);request.onerror=()=>reject(request.error);});return db;}
  async function read(){try{const handle=await open();return await new Promise((resolve,reject)=>{const r=handle.transaction('data').objectStore('data').get('flights:'+(window.BalloonNetwork?.username||'local'));r.onsuccess=()=>resolve(r.result);r.onerror=()=>reject(r.error);});}catch{return null;}}
  async function write(value){const handle=await open();return new Promise((resolve,reject)=>{const t=handle.transaction('data','readwrite');t.objectStore('data').put(value,'flights:'+(window.BalloonNetwork?.username||'local'));t.oncomplete=resolve;t.onerror=()=>reject(t.error);});}
  async function nativeStore(data){const r=await fetch('/__bp_connection/flights',data?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)}:{cache:'no-store'});if(!r.ok)throw Error('Could not save local flight history.');return r.json();}
  async function load(){if(loaded)return loaded;await window.BalloonNetwork?.check();native=window.BalloonNetwork?.native===true;loaded=(native?await nativeStore().catch(()=>null):await read())||{workspace:null,recent:[]};return loaded;}
  function validate(doc){
    if(!doc||doc.format!==FORMAT||doc.version!==1||!Array.isArray(doc.sites)||!doc.sites.length||doc.sites.length>20)throw Error('Choose a BalloonPredictor .bpflight file with 1–20 launch points.');
    if(JSON.stringify(doc).length>MAX)throw Error('Saved flights must be under 32 MB. Save fewer launches at once.');
    if(!doc.calculations||typeof doc.calculations!=='object'||Array.isArray(doc.calculations))throw Error('Invalid saved flight calculations.');
    function flight(value){if(!value?.request||!value.summary?.launch||!value.summary?.burst||!Array.isArray(value.prediction)||!value.prediction.length||!value.weather||!Array.isArray(value.warnings))throw Error('Incomplete saved flight.');for(const stage of value.prediction){if(!['ascent','descent'].includes(stage.stage)||!Array.isArray(stage.trajectory)||!stage.trajectory.length)throw Error('Invalid saved track.');for(const p of stage.trajectory)if(!Number.isFinite(p.latitude)||Math.abs(p.latitude)>90||!Number.isFinite(p.longitude)||!Number.isFinite(p.altitude)||!Number.isFinite(Date.parse(p.datetime)))throw Error('Invalid saved track position.');}}
    for(const site of doc.sites){if(!site.setup||typeof site.setup!=='object'||!Number.isFinite(Number(site.setup.launch_latitude))||!Number.isFinite(Number(site.setup.launch_longitude)))throw Error('Invalid saved launch setup.');const calculation=doc.calculations[site.calculation];if(calculation){if(Array.isArray(calculation.flights)){for(const entry of calculation.flights)if(entry.status==='completed')flight(entry.result);}else flight(calculation);}}
    return doc;
  }
  function pack(sites,activeId){const calculations=Object.create(null);return{format:FORMAT,version:1,created:new Date().toISOString(),activeId,sites:sites.map(site=>{const value=site.series||site.result,key=value?(site.jobId||site.id):null;if(key)calculations[key]=value;return{id:site.id,setup:{...site.setup},calculation:key,jobId:site.jobId,selectedIndex:site.selectedIndex,batchSiteIndex:site.batchSiteIndex};}),calculations};}
  function update(doc,remember=false){
    const snapshot=JSON.parse(JSON.stringify(doc));
    chain=chain.catch(()=>{}).then(async()=>{
      const value=await load();value.workspace=snapshot;
      if(remember){const id=Date.now().toString(36)+'-'+Math.random().toString(36).slice(2,8);value.recent.unshift({id,label:snapshot.sites.map(s=>s.setup.flight_name||'Launch').join(', '),saved:snapshot.created,document:snapshot});}
      value.recent=value.recent.slice(0,8);while(JSON.stringify(value).length>MAX&&value.recent.length)value.recent.pop();
      try{if(native)await nativeStore(value);else await write(value);document.dispatchEvent(new Event('bp:history'));}
      catch(error){document.dispatchEvent(new CustomEvent('bp:saveerror',{detail:'Automatic saving failed. Download a .bpflight copy to keep these tracks. '+error.message}));}
    });return chain;
  }
  function download(doc,name='balloon-flight.bpflight'){validate(doc);window.__bpExportFilename=name;const url=URL.createObjectURL(new Blob([JSON.stringify(doc)],{type:'application/json'})),link=document.createElement('a');link.href=url;link.download=name;document.body.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),30000);}
  window.BalloonFlights={pack,validate,load,update,download,MAX};
})();
