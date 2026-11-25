/* Real controller + retained-page adapter, editable dates, track clicks and profiles. */
const assert=require('node:assert/strict'),fs=require('node:fs'),os=require('node:os'),path=require('node:path'),{spawn}=require('node:child_process'),{chromium}=require('playwright'),{fillDateTime}=require('./datetime-test-helper.cjs');
const root=path.resolve(__dirname,'..'),tmp=fs.mkdtempSync(path.join(process.env.BP_TEST_TMP||os.tmpdir(),'bp-v163-')),ready=path.join(tmp,'ready.json');
const host=spawn(process.env.BP_TEST_PYTHON||'python3',['desktop/host.py','--data-dir',path.join(tmp,'data'),'--ready-file',ready,'--smoke-test'],{cwd:root,stdio:['pipe','ignore','pipe']});
let logs='',browser;host.stderr.on('data',b=>logs+=b);const delay=ms=>new Promise(r=>setTimeout(r,ms));
(async()=>{try{
 for(let i=0;i<300&&!fs.existsSync(ready)&&host.exitCode===null;i++)await delay(100);assert(fs.existsSync(ready),logs);
 const info=JSON.parse(fs.readFileSync(ready,'utf8'));browser=await chromium.launch();const context=await browser.newContext({viewport:{width:1560,height:1000}});await context.route('https://**/*',r=>r.abort());
 const chooser=await context.newPage(),flight=await context.newPage(),errors=[];for(const p of[chooser,flight])p.on('pageerror',e=>errors.push(e.message));
 await chooser.goto(info.url+'?splash=done&native=1');await chooser.click('#choose-local');await chooser.waitForSelector('#connect:enabled');await chooser.click('#connect');
 const target=()=>JSON.parse(fs.readFileSync(path.join(tmp,'target.json'),'utf8'));
 for(let i=0;i<200&&!fs.existsSync(path.join(tmp,'target.json'));i++)await delay(50);assert.equal(target().reuse,false);assert.equal(target().generation,1);
 await flight.goto(target().navigation);await flight.waitForSelector('#predict-button:enabled');assert(chooser.url().includes('native=1'),'Managed chooser navigated over its own page');
 await fillDateTime(flight,'launch_datetime','2024-02-29T11:22:33');assert.equal(await flight.inputValue('#launch_datetime'),'2024-02-29T11:22:33');
 await flight.fill('#launch_datetime-date','2024-02-30');assert.equal(await flight.inputValue('#launch_datetime'),'');assert.equal(await flight.locator('#launch_datetime-date').evaluate(e=>e.validity.valid),false);assert.equal(await flight.inputValue('#launch_datetime-date'),'2024-02-30');
 await fillDateTime(flight,'launch_datetime','2024-02-29T11:22:33');await flight.click('#launch_datetime-calendar');assert(await flight.locator('.utc-calendar').isVisible());
 await flight.locator('.utc-calendar [data-next]').click();await flight.locator('.utc-calendar [data-day="2024-03-01"]').click();assert.equal(await flight.inputValue('#launch_datetime'),'2024-03-01T11:22:33');
 await flight.click('#launch_datetime-calendar');await flight.keyboard.press('ArrowRight');await flight.keyboard.press('Enter');assert.equal(await flight.inputValue('#launch_datetime'),'2024-03-02T11:22:33');
 await flight.locator('#flight-form details').evaluateAll(elements=>elements.forEach(e=>e.open=true));await flight.selectOption('#run_mode','arrival');assert(await flight.locator('#arrival_datetime-date').evaluate(e=>e.required));await fillDateTime(flight,'arrival_datetime','2024-03-02T18:00:56');assert.equal(await flight.inputValue('#arrival_datetime'),'2024-03-02T18:00:56');
 await flight.selectOption('#run_mode','series');await flight.selectOption('#flight_mode','ascent');await flight.fill('#burst_altitude','4000');await flight.fill('#series_duration','3');await flight.fill('#series_interval','1');
 await flight.evaluate(()=>BalloonWindows.show('sounding-panel'));await flight.click('#predict-button');await flight.waitForFunction(()=>!running&&activeSite().series?.completed===3);
 await flight.waitForFunction(()=>document.querySelector('#sounding-panel').dataset.flight===activeSite().jobId+':0:ascent'&&document.querySelectorAll('#skewt-chart path').length>0);
 assert.equal(await flight.evaluate(()=>activeSite().selectedIndex),0,'First successful flight was not selected by default');
 // A profile open during setup invalidation/recalculation must update without a launch-button click.
 const oldKey=await flight.locator('#sounding-panel').getAttribute('data-flight');await flight.fill('#ascent_rate','6');await flight.locator('#ascent_rate').dispatchEvent('change');await flight.click('#predict-button');await flight.waitForFunction(()=>!running&&activeSite().series?.completed===3);
 await flight.waitForFunction(key=>document.querySelector('#sounding-panel').dataset.flight!==key&&document.querySelector('#sounding-panel').dataset.flight===activeSite().jobId+':0:ascent',oldKey);
 await flight.evaluate(()=>{BalloonWindows.minimise('sounding-panel');BalloonWindows.show('map-panel');});await delay(300);
 console.log('v163: date controls and automatic series profiles passed');
 // Canvas hit regions accept clicks 5 pixels beside a visually thin track.
 const hit=await flight.evaluate(()=>{const e=allFlights()[1],s=e.result.prediction[0].trajectory,i=Math.floor(s.length*.65),a=map.latLngToContainerPoint([s[i].latitude,s[i].longitude]),b=map.latLngToContainerPoint([s[i+1].latitude,s[i+1].longitude]),dx=b.x-a.x,dy=b.y-a.y,length=Math.hypot(dx,dy)||1,r=map.getContainer().getBoundingClientRect();return{x:r.x+a.x-dy/length*5,y:r.y+a.y+dx/length*5};});
 await flight.mouse.click(hit.x,hit.y);if(await flight.locator('.flight-track-picker').count())await flight.locator('.flight-track-picker button').nth(1).click();
 assert.notEqual(await flight.evaluate(()=>activeSite().selectedIndex),0,'Clicking beside the track did not select a flight');assert(await flight.locator('#move-launch-dialog').isHidden());assert.equal(await flight.evaluate(()=>allFlights().length),3);
 // Endpoint clicks select their own flight rather than moving the launch point.
 await flight.evaluate(()=>{activeSite().selectedIndex=0;drawSelected();drawAllMap();});
 const endpoint=await flight.evaluate(()=>{const p=allFlights()[2].result.summary.burst,q=map.latLngToContainerPoint([p.latitude,p.longitude]),r=map.getContainer().getBoundingClientRect();return{x:r.x+q.x,y:r.y+q.y};});await flight.mouse.click(endpoint.x,endpoint.y);assert.equal(await flight.evaluate(()=>activeSite().selectedIndex),2);assert(await flight.locator('#move-launch-dialog').isHidden());
 // Keep the exact document, selected flight and map state through the chooser.
 await flight.evaluate(()=>{window.__retainedFlight='v163';map.stop();map.setZoom(9,{animate:false});});await flight.waitForFunction(()=>map.getZoom()===9);const selection=await flight.evaluate(()=>({index:activeSite().selectedIndex,job:activeSite().jobId,zoom:map.getZoom(),tracks:allFlights().length}));
 await chooser.goto(info.url+'?splash=done&native=1');await chooser.waitForSelector('#resume:not([hidden]):enabled');await chooser.click('#resume');for(let i=0;i<100&&target().generation<2;i++)await delay(50);
 const resumed=target();assert.equal(resumed.reuse,true);assert.equal(resumed.url,new URL(flight.url()).origin);
 // Native shells use this presentation record to show the existing view, without goto/reload.
 assert.equal(await flight.evaluate(()=>window.__retainedFlight),'v163');assert.deepEqual(await flight.evaluate(()=>({index:activeSite().selectedIndex,job:activeSite().jobId,zoom:map.getZoom(),tracks:allFlights().length})),selection);assert(chooser.url().includes('native=1'));
 await flight.evaluate(()=>BalloonWindows.show('sounding-panel'));await flight.waitForFunction(()=>document.querySelector('#sounding-panel').dataset.flight===activeSite().jobId+':2:ascent');
 assert.deepEqual(errors,[]);if(process.env.BP_TEST_OUTPUT){fs.mkdirSync(process.env.BP_TEST_OUTPUT,{recursive:true});await flight.screenshot({path:path.join(process.env.BP_TEST_OUTPUT,'v163-flight.png')});await flight.click('#launch_datetime-calendar');await flight.screenshot({path:path.join(process.env.BP_TEST_OUTPUT,'v163-calendar.png')});}
 console.log('v1.6.3 browser regression: PASS · editable UTC/calendar/seconds, first profile after series/recalculation, actual wide-track/endpoint clicks, overlapping flight choices, and retained controller return with unchanged document/selection/zoom/results.');
}catch(error){console.error(error.stack,logs.slice(-2000));process.exitCode=1;}finally{if(browser)await browser.close();host.stdin.end();const timer=setTimeout(()=>host.kill('SIGKILL'),5000);host.once('exit',()=>{clearTimeout(timer);fs.rmSync(tmp,{recursive:true,force:true});});}})();
