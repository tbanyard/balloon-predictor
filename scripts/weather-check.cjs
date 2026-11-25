/* Real Chromium tests. OSM requests are intercepted locally, never downloaded. */
const fs=require('fs'),path=require('path'),os=require('os'),{spawn,spawnSync}=require('child_process'),assert=require('node:assert/strict');
const {chromium}=require('playwright');
const root=path.resolve(__dirname,'..'),python=process.env.BP_TEST_PYTHON||'python3',port=Number(process.env.BP_TEST_PORT||18026),base=`http://127.0.0.1:${port}`;
const tmp=fs.mkdtempSync(path.join(process.env.BP_TEST_TMP||os.tmpdir(),'bp-weather-')),output=process.env.BP_TEST_OUTPUT||path.join(tmp,'screenshots');fs.mkdirSync(output,{recursive:true});
const fixture=spawnSync(python,['-c',`from pathlib import Path
import sys
sys.path.insert(0,'tests')
from conftest import make_normalized
from balloon_predictor.store import WeatherStore
from balloon_predictor.settings import Settings
p=Path(sys.argv[1]);ds=make_normalized();ds.attrs.update(source='ifs',label='IFS saved snapshot')
for y in range(3):
 for x in range(3):
  ds['temperature'][:,:,y,x] += y*3+x
  ds['v'][:,:,y,x] += y-x
WeatherStore(Settings(p/'data',era5_root=p/'no-era5',wrf_root=p/'no-wrf')).save('ifs-snapshot',ds)
p.joinpath('config.yml').write_text(f'data_dir: {p}/data\\nera5_root: {p}/no-era5\\nwrf_root: {p}/no-wrf\\n')`,tmp],{cwd:root,env:process.env,encoding:'utf8'});assert.equal(fixture.status,0,fixture.stderr);
const server=spawn(python,['server.py','--config',path.join(tmp,'config.yml'),'--port',String(port)],{cwd:root,env:{...process.env,BP_DATA_DIR:path.join(tmp,'data')},stdio:['ignore','ignore','pipe']});
let logs='',browser;server.stderr.on('data',b=>logs+=b.toString());const delay=ms=>new Promise(r=>setTimeout(r,ms));
(async()=>{try{
 let ready=false;for(let i=0;i<100;i++){try{if((await fetch(base+'/api/health')).ok){ready=true;break;}}catch{}await delay(100);}assert.ok(ready,logs);
 browser=await chromium.launch({headless:true,...(process.env.BP_TEST_CHROME?{executablePath:process.env.BP_TEST_CHROME,args:['--no-sandbox','--disable-dev-shm-usage']}: {})});
 const context=await browser.newContext({viewport:{width:1560,height:1050},deviceScaleFactor:1}),tiles=[],posts=[],errors=[];
 await context.route('https://**/*',r=>r.abort());
 await context.route('https://tile.openstreetmap.org/**',async r=>{tiles.push({url:r.request().url(),headers:await r.request().allHeaders()});await r.fulfill({status:200,headers:{'Content-Type':'image/png','Access-Control-Allow-Origin':'*','Cache-Control':'public, max-age=604800'},body:Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/l1sAAAAASUVORK5CYII=','base64')});});
 const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));page.on('request',r=>{if(r.method()==='POST'&&r.url().endsWith('/weather-overlay'))posts.push(JSON.parse(r.postData()));});
 // Reproduce the shared server's restrictive document policy. The per-image
 // origin policy must override it without revealing the URL's session detail.
 await page.route(/\/\?private=/,r=>r.fulfill({status:200,contentType:'text/html',headers:{'Referrer-Policy':'no-referrer'},body:fs.readFileSync(path.join(root,'frontend/index.html'),'utf8')}));
 await page.goto(base+'/?private=never-send-this');await page.waitForFunction(()=>document.querySelector('#flight-tabs button'));await page.waitForFunction(()=>document.querySelectorAll('.leaflet-tile-loaded').length>0);
 assert.ok(tiles.length>0);assert.ok(tiles.every(t=>t.headers.referer===base+'/'),'OSM did not receive only the document origin');assert.ok(tiles.every(t=>!t.url.includes('?')),'OSM tile URLs should not bypass caching');
 assert.equal(await page.locator('.leaflet-tile').first().getAttribute('referrerpolicy'),'origin');assert.match(await page.locator('.leaflet-control-attribution').textContent(),/OpenStreetMap contributors/);
 assert.ok(await page.locator('#restore-weather').isDisabled());
 await page.selectOption('#weather-source','ifs');await page.selectOption('#dataset','ifs-snapshot');await page.fill('#launch_datetime','2024-01-15T12:00');await page.fill('#launch_latitude','53');await page.fill('#launch_longitude','-4');await page.fill('#launch_altitude','100');await page.fill('#burst_altitude','1500');await page.selectOption('#flight_mode','ascent');
 await page.locator('#schedule-details summary').click();await page.selectOption('#run_mode','series');await page.fill('#series_duration','2');await page.fill('#series_interval','1');await page.click('#predict-button');await page.waitForFunction(()=>!document.querySelector('#flight-result').hidden,{},{timeout:30000});
 assert.equal(await page.locator('#series-launches button').count(),2);
 await page.click('#weather-overlay-open');await page.waitForFunction(()=>document.querySelector('#weather-panel').dataset.loadedFlight,{},{timeout:30000});

 assert.match(await page.textContent('#weather-overlay-meta'),/1,500 m AMSL/);assert.match(await page.textContent('#weather-overlay-meta'),/2024-01-15 12:00:00 UTC/);assert.equal(posts.length,1);
 assert.equal(posts[0].coverage,'auto');assert.equal(posts[0].area,null);assert.equal(await page.locator('#weather-panel').getAttribute('data-coverage-mode'),'model');
 const imageBounds=await page.evaluate(()=>{const layer=Object.values(map._layers).find(l=>l.options?.className==='weather-field-image'),b=layer.getBounds();return[b.getNorth(),b.getWest(),b.getSouth(),b.getEast()];});assert.deepEqual(imageBounds,[55,-6,51,-2]);
 assert.equal(await page.locator('.weather-field-image').count(),1);assert.equal(await page.locator('.weather-wind-canvas').count(),1);assert.equal(await page.locator('.weather-map-key').count(),0);
 assert.equal(await page.locator('#weather-panel h2').count(),0);assert.equal(await page.locator('#weather-overlay-flight-area,#weather-overlay-map-area').count(),0);
 assert.ok(await page.locator('#weather-panel [data-window-handle]').evaluate(h=>{const title=h.querySelector('.eyebrow').getBoundingClientRect(),buttons=h.querySelector('.window-controls').getBoundingClientRect();return Math.abs((title.top+title.bottom)/2-(buttons.top+buttons.bottom)/2)<2;}),'Weather title shifted the window buttons');
 assert.deepEqual(await page.evaluate(()=>['weatherFieldPane','weatherWindPane','overlayPane'].map(p=>Number(getComputedStyle(map.getPane(p)).zIndex))),[320,340,400]);
 const image=await page.locator('.weather-field-image').getAttribute('src');await page.selectOption('#weather-overlay-field','temperature_c');assert.notEqual(await page.locator('.weather-field-image').getAttribute('src'),image);assert.equal(posts.length,1,'Field change requested weather again');
 await page.locator('#weather-overlay-opacity').fill('30');await page.locator('#weather-overlay-opacity').dispatchEvent('input');assert.equal(await page.locator('.weather-field-image').evaluate(el=>el.style.opacity),'0.3');assert.equal(posts.length,1);
 await page.evaluate(()=>{map.setView([53,-4],8,{animate:false});});await delay(400);assert.equal(posts.length,1,'Regional full-box coverage changed on pan/zoom');
 await delay(500);assert.ok(await page.locator('.weather-wind-canvas').evaluate(c=>c.getContext('2d').getImageData(0,0,c.width,c.height).data.some((v,i)=>i%4===3&&v>0)),'Wind particles did not render');
 await page.uncheck('#weather-overlay-wind');assert.equal(await page.locator('.weather-wind-canvas').count(),0);
 console.log('Weather stage: initial coverage');
 // A fresh layer with wind off must not try to resize an unmounted canvas.
 await page.selectOption('#weather-level-mode','altitude');await page.fill('#weather-level-value','8000');await page.locator('#weather-level-value').dispatchEvent('change');await page.waitForFunction(()=>document.querySelector('#weather-panel').dataset.weatherHeight==='8000');assert.equal(posts.at(-1).altitude_m,8000);assert.equal(await page.locator('.weather-wind-canvas').count(),0);
 await page.selectOption('#weather-level-mode','pressure');await page.waitForFunction(()=>document.querySelector('#weather-panel').dataset.weatherPressure==='500');assert.equal(posts.at(-1).pressure_hpa,500);assert.equal(posts.at(-1).altitude_m,undefined);assert.match(await page.textContent('#weather-overlay-meta'),/500 hPa/);assert.match(await page.textContent('#weather-overlay-meta'),/5,050/);
 await page.fill('#weather-level-value','1100');await page.locator('#weather-level-value').dispatchEvent('change');await page.waitForFunction(()=>document.querySelector('#weather-overlay-status').textContent.includes('No valid weather'));assert.equal(await page.locator('.weather-field-image').count(),0);
 await page.fill('#weather-level-value','500');await page.locator('#weather-level-value').dispatchEvent('change');await page.waitForFunction(()=>document.querySelector('#weather-panel').dataset.weatherPressure==='500');
 await page.selectOption('#weather-level-mode','target');await page.waitForFunction(()=>document.querySelector('#weather-panel').dataset.weatherHeight==='1500');await page.check('#weather-overlay-wind');assert.equal(await page.locator('.weather-wind-canvas').count(),1);
 const controlsPosts=posts.length;await page.uncheck('#weather-overlay-show');assert.equal(await page.locator('.weather-field-image').count(),0);await page.check('#weather-overlay-show');assert.equal(posts.length,controlsPosts);assert.equal(await page.locator('.weather-field-image').count(),1);
 console.log('Weather stage: vertical levels');
 // The reference viewer's full/view/fixed scales, including visible-window
 // recalculation with no model request while panning inside a regional grid.
 await page.locator('.weather-colour-options summary').click();await page.selectOption('#weather-range-mode','view');await page.evaluate(()=>{map.setView([51.5,-5.5],10,{animate:false});});await delay(300);
 const southwest=await page.locator('#weather-range-min').inputValue()+','+await page.locator('#weather-range-max').inputValue();
 await page.evaluate(()=>{map.setView([54.5,-2.5],10,{animate:false});});await delay(300);const northeast=await page.locator('#weather-range-min').inputValue()+','+await page.locator('#weather-range-max').inputValue();assert.notEqual(southwest,northeast,'Visible-window colour scale did not follow the map');assert.equal(posts.length,controlsPosts);
 await page.fill('#weather-range-min','-50');await page.locator('#weather-range-min').dispatchEvent('change');await page.fill('#weather-range-max','10');await page.locator('#weather-range-max').dispatchEvent('change');assert.equal(await page.inputValue('#weather-range-mode'),'fixed');
 await page.fill('#weather-range-max','-60');await page.locator('#weather-range-max').dispatchEvent('change');assert.match(await page.textContent('#weather-range-error'),/greater than/);await page.fill('#weather-range-max','10');await page.locator('#weather-range-max').dispatchEvent('change');
 await page.locator('#series-launches button').nth(1).click();await page.waitForFunction(()=>document.querySelector('#weather-panel').dataset.weatherTime==='2024-01-15T13:00:00Z');assert.equal(await page.inputValue('#weather-range-min'),'-50');assert.equal(await page.inputValue('#weather-range-max'),'10');assert.equal(await page.inputValue('#weather-range-mode'),'fixed');
 console.log('Weather stage: colour controls');
 // Preserve stale-response protection while allowing at most two active loads.
 let release;const gate=new Promise(r=>release=r);let delayed=false;
 await page.route('**/weather-overlay',async r=>{if(!delayed&&JSON.parse(r.request().postData()).time_basis==='target'){delayed=true;await gate;}await r.continue();});
 await page.selectOption('#weather-overlay-time','target');for(let i=0;i<60&&!delayed;i++)await delay(25);assert.ok(delayed);
 await page.locator('#series-launches button').first().click();await page.waitForFunction(()=>document.querySelector('#weather-panel').dataset.weatherTime?.startsWith('2024-01-15T12:04:40'));
 const current=await page.locator('#weather-panel').getAttribute('data-weather-time');release();await delay(800);assert.equal(await page.locator('#weather-panel').getAttribute('data-weather-time'),current,'Stale weather replaced the selected flight');await page.unroute('**/weather-overlay');
 await page.selectOption('#weather-overlay-time','launch');await page.waitForFunction(()=>document.querySelector('#weather-panel').dataset.weatherTime==='2024-01-15T12:00:00Z');
 console.log('Weather stage: selection guards');
 // Four panels, shared separators and a readable Weather header on the phone.
 await page.evaluate(()=>{BalloonWindows.show('sounding-panel');BalloonWindows.show('weather-panel');});await page.locator('#weather-panel [aria-haspopup=menu]').click();await page.locator('#weather-panel [data-arrange=right]').click();
 const panes=await page.locator('#window-area > .plot-window').evaluateAll(els=>els.filter(e=>!e.hidden).map(e=>({id:e.id,r:e.getBoundingClientRect().toJSON()})));assert.equal(panes.length,4);
 for(let i=0;i<panes.length;i++)for(let j=i+1;j<panes.length;j++){const a=panes[i].r,b=panes[j].r;assert.ok(a.right<=b.left+1||b.right<=a.left+1||a.bottom<=b.top+1||b.bottom<=a.top+1,'Docked weather overlapped another panel');}assert.equal(await page.locator('.tree-divider').count(),3);
 await page.selectOption('#weather-level-mode','pressure');await page.waitForFunction(()=>document.querySelector('#weather-panel').dataset.weatherPressure==='500');await page.evaluate(()=>{document.querySelector('#weather-panel .window-body').scrollTop=0;map.setView([53,-4],7,{animate:false});});await delay(300);await page.screenshot({path:path.join(output,'weather-v162-desktop.png')});
 await page.setViewportSize({width:412,height:880});await page.evaluate(()=>BalloonWindows.show('weather-panel'));assert.ok(await page.locator('#weather-level-mode').isVisible());await page.evaluate(()=>{document.querySelector('#weather-panel .window-body').scrollTop=0;});await page.screenshot({path:path.join(output,'weather-v162-phone-controls.png')});await page.click('#weather-overlay-map');assert.ok(await page.locator('#map-panel').isVisible());assert.equal(await page.locator('.weather-map-key').count(),0);await page.screenshot({path:path.join(output,'weather-v162-phone-map.png')});
 await page.evaluate(()=>BalloonWindows.show('weather-panel'));await delay(200);const paused=await page.locator('.weather-wind-canvas').evaluate(c=>c.toDataURL());await delay(400);assert.equal(await page.locator('.weather-wind-canvas').evaluate(c=>c.toDataURL()),paused);
 await page.setViewportSize({width:1560,height:1050});await page.evaluate(()=>{BalloonWindows.reset();BalloonWindows.show('weather-panel');});await page.selectOption('#weather-level-mode','target');
 console.log('Weather stage: mobile panels');
 // Completed status can be dismissed and the panels reclaim its height.
 await page.click('#sidebar-toggle');await page.waitForFunction(()=>!document.querySelector('#workspace-progress').hidden);const before=await page.locator('#window-area').evaluate(e=>e.clientHeight);await page.click('#dismiss-flight-status');assert.ok(await page.locator('#workspace-progress').isHidden());await page.waitForFunction(h=>document.querySelector('#window-area').clientHeight>h,before,{timeout:5000});
 await delay(300);assert.ok(await page.locator('#window-area').evaluate(e=>e.clientHeight)>before,'Dismissed status left empty space');await page.click('#sidebar-reopen');
 console.log('Weather stage: status banner');
 // Global data follows a buffered viewport without a manual area button.
 await page.click('#duplicate-flight');await page.selectOption('#weather-source','demo');await page.selectOption('#run_mode','single');await page.click('#predict-button');await page.waitForFunction(()=>!document.querySelector('#flight-result').hidden&&document.querySelector('#weather-panel').dataset.coverageMode==='viewport',{},{timeout:30000});
 await page.evaluate(()=>{map.setView([53,-4],7,{animate:false});});await page.waitForFunction(()=>document.querySelector('#weather-overlay-status').textContent.startsWith('Weather ready'));await delay(900);
 const globalCount=posts.length;assert.ok(posts.at(-1).area);assert.equal(posts.at(-1).coverage,'auto');assert.ok(await page.evaluate(()=>{const layer=Object.values(map._layers).find(l=>l.options?.className==='weather-field-image'),b=layer.getBounds(),v=map.getBounds();return b.contains(v);}));
 await page.evaluate(()=>{map.panBy([10,0],{animate:false});});await delay(800);assert.equal(posts.length,globalCount,'Panning within the buffer unnecessarily requested weather');
 await page.evaluate(()=>{map.setView([53,8],8,{animate:false});});for(let i=0;i<100&&posts.length===globalCount;i++)await delay(30);assert.ok(posts.length>globalCount,'Panning outside the buffer did not request weather');await page.waitForFunction(()=>document.querySelector('#weather-overlay-status').textContent.startsWith('Weather ready'));
 assert.equal(await page.locator('.weather-map-key').count(),0);
 console.log('Weather stage: global coverage');
 // New work restores a dismissed banner; edits invalidate its overlay.
 await page.click('#sidebar-toggle');assert.ok(await page.locator('#workspace-progress').isVisible());await page.click('#dismiss-flight-status');assert.ok(await page.locator('#workspace-progress').isHidden());await page.click('#sidebar-reopen');await page.fill('#ascent_rate','6');await page.locator('#ascent_rate').dispatchEvent('change');assert.equal(await page.locator('.weather-field-image').count(),0);assert.equal(await page.locator('#workspace-progress').getAttribute('hidden'),null);
 console.log('Weather stage: invalidation');
 // Download confirmation is exercised against a local intercepted endpoint.
 // No CDS/forecast request can leave this test.
 const downloads=[];await page.route('**/api/weather/download',async r=>{downloads.push(JSON.parse(r.request().postData()));await r.fulfill({status:422,contentType:'application/json',body:JSON.stringify({detail:'Fixture: confirmed download received'})});});
 await page.fill('#launch_latitude','45');await page.fill('#launch_longitude','10');await page.click('#weather-open');if(!await page.locator('details:has(#weather-form)').evaluate(e=>e.open))await page.locator('details:has(#weather-form) > summary').click();
 assert.deepEqual(await page.locator('#area-n,#area-w,#area-s,#area-e').evaluateAll(els=>els.map(e=>Number(e.value))),[55,-3,35,23]);
 await page.fill('#area-n','56');await page.click('#weather-close');await page.click('#weather-open');assert.equal(await page.inputValue('#area-n'),'56');await page.click('#download-launch-area');assert.equal(Number(await page.inputValue('#area-n')),55);
 await page.fill('#download-start','2024-01-15T00:00');await page.fill('#download-end','2024-01-21T00:00');await page.locator('#download-end').dispatchEvent('input');assert.match(await page.textContent('#download-length-note'),/144.0 hours/);
 await page.click('#download-button');assert.ok(await page.locator('#large-download-dialog').isVisible());assert.equal(downloads.length,0);await page.locator('#large-download-dialog button[value=cancel]').click();await page.waitForFunction(()=>!document.querySelector('#download-button').disabled);assert.equal(downloads.length,0);
 await page.click('#download-button');await page.locator('#large-download-dialog button[value=download]').click();await page.waitForFunction(()=>document.querySelector('#weather-status').textContent.includes('Fixture:'));assert.equal(downloads.length,1);assert.equal(downloads[0].confirm_large_download,true);
 await page.fill('#download-end','2024-01-18T00:00');await page.click('#download-button');for(let i=0;i<50&&downloads.length<2;i++)await delay(20);assert.equal(downloads.length,2);assert.ok(!downloads[1].confirm_large_download);assert.ok(await page.locator('#large-download-dialog').isHidden());
 await page.fill('#download-end','2024-01-21T01:00');await page.click('#download-button');assert.equal(downloads.length,2);assert.match(await page.textContent('#weather-status'),/144 hours/);await page.click('#weather-close');
 assert.deepEqual(errors,[]);console.log('Weather / OSM browser regression: PASS · full regional/buffered global coverage, automatic pan loading, no map label/subtitle, colour percentiles/view/fixed limits, altitude/pressure/masks, caching and stale responses, particles, four panels/mobile, dismissible status, launch-centred download defaults and 144-hour confirmation/cancel.');
}catch(e){console.error(e.stack);if(logs)console.error(logs.slice(-2000));process.exitCode=1;}finally{if(browser)await browser.close();server.kill();server.once('exit',()=>fs.rmSync(tmp,{recursive:true,force:true}));}})();
