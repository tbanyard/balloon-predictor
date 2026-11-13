/* Actual UI + API export workflow with a synthetic weather-grid fixture. */
const fs=require('fs'),path=require('path'),os=require('os'),{spawn,spawnSync}=require('child_process'),assert=require('node:assert/strict'),{JSDOM,VirtualConsole}=require('jsdom');
const root=path.resolve(__dirname,'..'),python=process.env.BP_TEST_PYTHON||'python3',port=Number(process.env.BP_TEST_PORT||18018),base=`http://127.0.0.1:${port}/`;
const data=fs.mkdtempSync(path.join(process.env.BP_TEST_TMP||os.tmpdir(),'bp-v14-exports-'));
const fixture=spawnSync(python,['-c',`from pathlib import Path
import sys
sys.path.insert(0,'tests')
from conftest import make_normalized
from balloon_predictor.store import WeatherStore
from balloon_predictor.settings import Settings
p=Path(sys.argv[1]);WeatherStore(Settings(p,era5_root=p/'no-era5',wrf_root=p/'no-wrf')).save('test-model',make_normalized())`,data],{cwd:root,env:process.env,encoding:'utf8'});
assert.equal(fixture.status,0,fixture.stderr);
const server=spawn(python,['-m','balloon_predictor','serve','--port',String(port)],{cwd:root,env:{...process.env,BP_DATA_DIR:data},stdio:['ignore','ignore','pipe']});let logs='',dom;server.stderr.on('data',b=>logs+=b.toString());
const delay=ms=>new Promise(r=>setTimeout(r,ms));
(async()=>{try{
 let ready=false;for(let i=0;i<100;i++){try{if((await fetch(base+'api/health')).ok){ready=true;break;}}catch{}await delay(100);}assert.ok(ready,logs);
 const errors=[],vc=new VirtualConsole();vc.on('jsdomError',e=>errors.push(e.message));
 const html=fs.readFileSync(path.join(root,'frontend/index.html'),'utf8').replace(/<script[^>]+src=[^>]+><\/script>/g,'');
 dom=new JSDOM(html,{url:base,runScripts:'outside-only',pretendToBeVisual:true,virtualConsole:vc});const w=dom.window,$=id=>w.document.getElementById(id),downloads=[];
 const realFetch=(url,options)=>fetch(new URL(url,base),options);w.fetch=realFetch;
 w.HTMLAnchorElement.prototype.click=function(){downloads.push({url:this.href,name:this.download});};
 w.SVGSVGElement.prototype.createSVGRect=()=>({});w.HTMLDialogElement.prototype.showModal=function(){this.open=true;};w.HTMLDialogElement.prototype.close=function(){this.open=false;};w.matchMedia=()=>({matches:false});
 Object.defineProperty(w.HTMLElement.prototype,'clientWidth',{get(){return ['map','window-area'].includes(this.id)?1000:400;}});Object.defineProperty(w.HTMLElement.prototype,'clientHeight',{get(){return this.id==='window-area'?800:600;}});
 w.eval(fs.readFileSync(path.join(root,'frontend/vendor/leaflet/leaflet.js'),'utf8'));const mapFactory=w.L.map;w.L.map=(id,o)=>mapFactory(id,{...o,preferCanvas:false});
 for(const file of ['plots.js','ui.js','windows.js','sensitivity.js','trajectory-tools.js','app.js'])w.eval(fs.readFileSync(path.join(root,'frontend',file),'utf8'));
 for(let i=0;i<100&&!$('flight-tabs').children.length;i++)await delay(20);assert.ok($('flight-tabs').children.length);
 $('dataset').value='test-model';$('dataset').onchange();$('launch_datetime').value='2024-01-15T12:00:00';$('launch_latitude').value='53';$('launch_longitude').value='-4';$('burst_altitude').value='1000';$('flight_mode').value='ascent';$('landing_mode').value='fixed';$('landing_altitude').value='3000';$('flight_mode').onchange();
 assert.ok($('landing_mode').disabled&&$('landing_altitude').disabled);
 // Hold the request to inspect loading state, then let the real API calculate.
 let release;const gate=new Promise(r=>release=r);
 w.fetch=async(url,o)=>{if(String(url)==='/api/predictions')await gate;return realFetch(url,o);};
 const calculating=$('flight-form').onsubmit({preventDefault(){}});await delay(20);
 assert.equal($('flight-form').getAttribute('aria-busy'),'true');assert.ok(w.document.querySelectorAll('[data-balloon-loader]:not([hidden])').length===2);
 assert.ok($('calculation-progress').querySelector('svg .loader-tether'));
 release();await calculating;w.fetch=realFetch;
 assert.equal($('flight-form').getAttribute('aria-busy'),'false');assert.equal(w.document.querySelectorAll('[data-balloon-loader]:not([hidden])').length,0);assert.equal($('flight-result').hidden,false,$('flight-status').textContent);
 assert.equal($('model-box-selection').closest('article').querySelector('h3').textContent,'Model box NetCDF');assert.equal($('sonde-scope').closest('article').querySelector('h3').textContent,'Sonde NetCDF');
 await $('model-box-button').onclick();let modelLink=$('model-box-status').querySelector('a.download-link');assert.ok(modelLink,$('model-box-status').textContent);const modelURL=modelLink.href;
 await $('sonde-netcdf').onclick();let sondeLink=$('sonde-status').querySelector('a.download-link');assert.ok(sondeLink,$('sonde-status').textContent);assert.notEqual(modelURL,sondeLink.href);assert.equal($('model-box-status').querySelector('a').href,modelURL);
 assert.equal(downloads.length,2);
 for(const link of [modelLink,sondeLink]){const response=await fetch(link.href);assert.ok(response.ok);assert.equal((new Uint8Array(await response.arrayBuffer()))[0],137);}
 // Scope changes clear only that export card; restoring the selection restores its link.
 $('sonde-scope').value='full';$('sonde-scope').onchange();assert.equal($('sonde-status').hidden,true);assert.equal($('model-box-status').querySelector('a').href,modelURL);
 $('sonde-scope').value='ascent';$('sonde-scope').onchange();assert.equal($('sonde-status').querySelector('a').href,sondeLink.href);
 $('run_mode').value='series';$('series_duration').value='2';$('series_interval').value='1';$('run_mode').onchange();await $('flight-form').onsubmit({preventDefault(){}});
 const launches=()=>[...$('series-launches').querySelectorAll('button')];assert.equal(launches().length,2);
 // A pending export for flight 0 must not claim to be flight 1 after switching.
 let resume;const exportGate=new Promise(r=>resume=r);w.fetch=async(url,o)=>{if(String(url).endsWith('/model-box'))await exportGate;return realFetch(url,o);};
 const exporting=$('model-box-button').onclick();await delay(20);assert.equal($('model-box-button').disabled,true);
 launches()[1].click();assert.equal($('model-box-status').hidden,true);assert.equal($('model-box-button').disabled,false);
 const before=downloads.length;resume();await exporting;w.fetch=realFetch;assert.equal($('model-box-status').hidden,true);assert.equal(downloads.length,before);
 launches()[0].click();assert.ok($('model-box-status').querySelector('a.download-link'));
 // Whole-plan exports are independent of the selected launch within the same plan.
 $('model-box-selection').value='plan';$('model-box-selection').onchange();await $('model-box-button').onclick();const planURL=$('model-box-status').querySelector('a').href;
 launches()[1].click();assert.equal($('model-box-status').querySelector('a').href,planURL);
 await $('sonde-netcdf').onclick();sondeLink=$('sonde-status').querySelector('a');assert.ok(sondeLink.download.includes('-1-ascent.nc'));
 // Rejected exports leave a readable error, allow retry, and leave the other card alone.
 $('sonde-scope').value='full';$('sonde-scope').onchange();w.fetch=async(url,o)=>String(url).endsWith('/sonde-export')?{ok:false,json:async()=>({detail:'Synthetic export failure'})}:realFetch(url,o);
 await $('sonde-netcdf').onclick();assert.match($('sonde-status').textContent,/Synthetic export failure/);assert.equal($('sonde-netcdf').disabled,false);assert.equal($('model-box-status').querySelector('a').href,planURL);
 w.fetch=realFetch;await $('sonde-netcdf').onclick();assert.ok($('sonde-status').querySelector('a.download-link'));
 // Rejected predictions must stop both loader copies, including the hidden-sidebar copy.
 w.fetch=async(url,o)=>String(url)==='/api/predictions/series'?{ok:false,json:async()=>({detail:'Synthetic prediction failure'})}:realFetch(url,o);
 await $('flight-form').onsubmit({preventDefault(){}});assert.match($('flight-status').textContent,/Synthetic prediction failure/);assert.equal($('flight-form').getAttribute('aria-busy'),'false');assert.equal(w.document.querySelectorAll('[data-balloon-loader]:not([hidden])').length,0);
 assert.deepEqual(errors,[]);console.log('NetCDF / loading workflow: PASS · real model/sonde files, independent cards/scopes, auto-download/save links, async selection guards, whole plans, failures/retry, loading lifecycle');
}catch(e){console.error(e.stack);if(logs)console.error(logs.slice(-1200));process.exitCode=1;}finally{if(dom)dom.window.close();server.kill();server.once('exit',()=>fs.rmSync(data,{recursive:true,force:true}));}})();
