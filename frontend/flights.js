/* Portable results and durable local history. No weather grids are serialized. */
(()=>{
  'use strict';const FORMAT='BalloonPredictor saved flights',MAX=32*1024*1024;
  let db=null,chain=Promise.resolve(),native=false,loaded=null,loading=null;
  function open(){if(db)return db;db=new Promise((resolve,reject)=>{if(!window.indexedDB)return reject(Error('Local storage unavailable'));const request=indexedDB.open('balloon-predictor-flights',1);request.onupgradeneeded=()=>request.result.createObjectStore('data');request.onsuccess=()=>resolve(request.result);request.onerror=()=>reject(request.error);});return db;}
  async function read(){try{const handle=await open();return await new Promise((resolve,reject)=>{const r=handle.transaction('data').objectStore('data').get('flights:'+(window.BalloonNetwork?.username||'local'));r.onsuccess=()=>resolve(r.result);r.onerror=()=>reject(r.error);});}catch{return null;}}
  async function write(value){const handle=await open();return new Promise((resolve,reject)=>{const t=handle.transaction('data','readwrite');t.objectStore('data').put(value,'flights:'+(window.BalloonNetwork?.username||'local'));t.oncomplete=resolve;t.onerror=()=>reject(t.error);});}
  async function nativeStore(data){const r=await fetch('/__bp_connection/flights',data?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)}:{cache:'no-store'});if(!r.ok)throw Error('Could not save local flight history.');return r.json();}
  function history(value){
    // Native stores return {} before the first save; older stores can also
    // lack recent. Never let a history problem prevent opening a local file.
    const source=value&&typeof value==='object'?value:{};
    return {workspace:source.workspace&&typeof source.workspace==='object'?source.workspace:null,
      recent:Array.isArray(source.recent)?source.recent.filter(e=>e&&typeof e==='object'&&e.document).slice(0,8):[]};
  }
  async function load(){if(loaded)return loaded;if(loading)return loading;loading=(async()=>{await window.BalloonNetwork?.check();native=window.BalloonNetwork?.native===true;loaded=history(native?await nativeStore().catch(()=>null):await read());return loaded;})().finally(()=>loading=null);return loading;}
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
  function filename(doc,now=new Date()){
    const stamp=value=>{const date=new Date(typeof value==='string'?value.replace(/^(\d{4}-\d{2}-\d{2})[_ ]/,'$1T'):value);return Number.isFinite(date.getTime())?date.toISOString().slice(0,19).replace(/[-:]/g,'').replace('T','_'):null;};
    const safe=value=>String(value||'Launch').normalize('NFKD').replace(/[\u0300-\u036f]/g,'').replace(/[^A-Za-z0-9.-]+/g,'-').replace(/^-+|-+$/g,'')||'Launch';
    const flights=doc.sites.flatMap(site=>{const c=doc.calculations[site.calculation];return c?.flights?c.flights.filter(e=>e.status==='completed'&&e.site_index===(site.batchSiteIndex||0)).map(e=>e.result):c?[c]:[];});
    const models=[...new Set(flights.map(f=>safe((f.weather?.source||f.request.dataset)+(f.weather?.provenance?.domain?'-d'+f.weather.provenance.domain:'')).toUpperCase().slice(0,22)))].sort();
    const cycles=[...new Set(flights.map(f=>{const p=f.weather?.provenance||{};return stamp(p.cycle||p.parent_cycle_utc||p.forecast_reference_time||p.run)||'NA';}))].sort();
    const knownCycles=cycles.filter(c=>c!=='NA'),cycleTag=(knownCycles.length>2?knownCycles[0]+'Z--'+knownCycles.at(-1)+'Z':knownCycles.map(c=>c+'Z').join('+'))+(knownCycles.length&&cycles.includes('NA')?'+NA':'')||'NA';
    const times=flights.map(f=>stamp(f.request.launch_datetime)).filter(Boolean).sort(),first=times[0],last=times.at(-1);
    const sensitivity=Math.max(0,...flights.map(f=>Number(f.request.ensemble_members)||0));
    const prefix=stamp(now)+'_'+(models.slice(0,2).join('+')+(models.length>2?'+MIXED':'')||'SETUP')+'_INIT-'+cycleTag+'_VALID-'+(first?first+'Z'+(first!==last?'--'+last+'Z':''):'NA')+'_S00'+(sensitivity?'-S'+String(sensitivity).padStart(2,'0'):'')+'_';
    const names=[...new Set(doc.sites.map(s=>safe(s.setup.flight_name)))].join('+');
    return prefix+names.slice(0,Math.max(12,220-prefix.length-9))+'.bpflight';
  }
  function download(doc){validate(doc);const name=filename(doc);window.__bpExportFilename=name;const url=URL.createObjectURL(new Blob([JSON.stringify(doc)],{type:'application/json'})),link=document.createElement('a');link.href=url;link.download=name;document.body.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),30000);}
  window.BalloonFlights={pack,validate,load,update,download,filename,history,MAX};
})();
