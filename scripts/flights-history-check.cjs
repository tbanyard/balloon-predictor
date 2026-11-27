/* Native histories start as {}; imported results must remain usable offline. */
const fs=require('fs'),vm=require('vm'),assert=require('node:assert/strict');
const {JSDOM}=require('jsdom');
(async()=>{
 const dom=new JSDOM('<body></body>',{url:'http://127.0.0.1/',runScripts:'outside-only'}),w=dom.window;let stored={};
 w.BalloonNetwork={native:true,check:async()=>({status:'connected'})};
 w.fetch=async(_path,options)=>({ok:true,json:async()=>{if(options?.method==='POST')stored=JSON.parse(options.body);return stored;}});
 w.eval(fs.readFileSync('frontend/flights.js','utf8'));
 const api=w.BalloonFlights;assert.deepEqual(JSON.parse(JSON.stringify(await api.load())),{workspace:null,recent:[]});
 const doc={format:'BalloonPredictor saved flights',version:1,created:'2026-11-26T18:20:30Z',activeId:'one',sites:[{id:'one',calculation:'one',setup:{flight_name:'Llanbedr 1'}}],calculations:{one:{request:{dataset:'wrf-test',launch_datetime:'2026-11-26T06:00:00Z',ensemble_members:50},weather:{source:'wrf',provenance:{domain:'02',run:'2026-11-24_12:00:00'}}}}};
 await api.update(doc,true);assert.equal(stored.recent.length,1);assert.equal(stored.workspace.sites[0].id,'one');
 const name=api.filename(doc,new Date('2026-11-26T18:20:30Z'));assert.equal(name,'20261126_182030_WRF-D02_INIT-20261124_120000Z_VALID-20261126_060000Z_S00-S50_Llanbedr-1.bpflight');
 const entry={index:0,status:'completed',site_index:0,result:doc.calculations.one};doc.calculations.one={flights:[entry,{...entry,index:1,result:{...entry.result,request:{...entry.result.request,launch_datetime:'2026-11-26T09:00:00Z'}}}]};
 assert(api.filename(doc).includes('VALID-20261126_060000Z--20261126_090000Z_S00-S50_Llanbedr-1'));
 for(const e of doc.calculations.one.flights)e.result.weather={source:'era5'};assert(api.filename(doc).includes('INIT-NA'));
 for(const broken of [null,{},[],{recent:null},{recent:{}},{recent:[null,{}, {document:doc}]}])assert(Array.isArray(api.history(broken).recent));
 assert.equal(api.history({workspace:doc,recent:'wrong'}).workspace,doc);
 dom.window.close();console.log('Flight history: PASS · empty Android/native history, remembered import, malformed legacy lists, model/cycle/valid range/member/site UTC filenames.');
})().catch(e=>{console.error(e);process.exitCode=1;});
