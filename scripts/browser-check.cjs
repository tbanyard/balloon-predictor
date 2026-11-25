/* Real-browser regression: native ERA5/WRF, multiple launch points, repeated
   launches, target endpoints, soundings, exports, offline map, responsive UI. */
const fs=require('fs'),path=require('path'),os=require('os'),{spawn,spawnSync}=require('child_process');
const {chromium}=require('playwright');
const root=path.resolve(__dirname,'..'),python=process.env.BP_TEST_PYTHON||path.join(root,'.venv','bin','python');
const tmp=fs.mkdtempSync(path.join(process.env.BP_TEST_TMP||os.tmpdir(),'bp-browser-'));
const port=Number(process.env.BP_TEST_PORT||18004),base='http://127.0.0.1:'+port;
const output=process.env.BP_TEST_OUTPUT||path.join(tmp,'screenshots');fs.mkdirSync(output,{recursive:true});
const generated=spawnSync(python,['-c',`
from pathlib import Path
import sys
sys.path.insert(0,'tests')
from conftest import make_wrf_file
from scripts.make_test_data import make_file,make_topography
from netCDF4 import Dataset
p=Path(sys.argv[1]);opts=dict(lat=[64,58,53,48,40],lon=[335,345,354,358,362,370],constant=False)
make_file(p/'era5/2024/era5_2024d015.nc',date='2024-01-15',times=(21,23),**opts)
make_file(p/'era5/2024/era5_2024d016.nc',date='2024-01-16',times=(0,3),**opts)
make_topography(p/'era5/era5_topo.nc',lat=[64,58,53,48,40],lon=[335,345,354,358,362,370],heights=100)
p.joinpath('wrf').mkdir();w=p/'wrf/wrfout_d02_2024-01-15_12:00:00';make_wrf_file(w)
with Dataset(w,'a') as nc:
 nc.SIMULATION_START_DATE='2024-01-15_00:00:00'
 q=nc.createVariable('QVAPOR','f4',nc.variables['T'].dimensions);q[:]=.008
p.joinpath('config.yml').write_text(f'data_dir: {p}/data\\nera5_root: {p}/era5\\nwrf_root: {p}/wrf\\ncache_mb: 16\\n')
`,tmp],{cwd:root,encoding:'utf8'});
if(generated.status!==0){console.error(generated.stderr);process.exit(1);}
const server=spawn(python,['server.py','--config',path.join(tmp,'config.yml'),'--port',String(port)],{cwd:root,env:{...process.env,BP_DATA_DIR:path.join(tmp,'data')},stdio:['ignore','ignore','pipe']});
let logs='',browser;server.stderr.on('data',b=>logs+=b.toString());
const delay=ms=>new Promise(r=>setTimeout(r,ms));
const assert=(condition,message)=>{if(!condition)throw new Error(message);};
(async()=>{
 try{
  let ready=false;for(let i=0;i<100;i++){try{if((await fetch(base+'/api/health')).ok){ready=true;break;}}catch{}await delay(100);}
  assert(ready,'Server failed: '+logs);browser=await chromium.launch({headless:true, ...(process.env.BP_TEST_CHROME?{executablePath:process.env.BP_TEST_CHROME,args:["--no-sandbox","--disable-dev-shm-usage"]}: {})});const context=await browser.newContext({viewport:{width:1540,height:1000},deviceScaleFactor:1,acceptDownloads:true});
  await context.route('https://**/*',route=>route.abort());const page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto(base);await page.waitForFunction(()=>document.querySelector('#flight-tabs button'));
  assert(await page.inputValue('#dataset')==='era5','ERA5 is not the default');assert(await page.locator('.version').textContent()==='v1.6.2','Version badge missing');
  assert(await page.locator('#dataset-run-field').isHidden(),'ERA5 should not show a forecast-run selector');
  assert(await page.locator('#sensitivity-display button').isDisabled(),'Sensitivity view enabled with no members');
  await page.setViewportSize({width:1120,height:800});
  await page.waitForFunction(()=>document.querySelector('#trajectory-menu').contains(document.querySelector('#fit-button')));
  assert(await page.locator('.map-toolbar').evaluate(el=>el.scrollWidth<=el.clientWidth&&el.offsetHeight<62),'Toolbar wrapped or overflowed with flight controls open');
  await page.locator('#trajectory-more').click();
  assert(await page.locator('#fit-button').isVisible()&&await page.locator('#share-button').isVisible(),'Overflow actions are missing');
  await page.keyboard.press('Escape');assert(await page.locator('#trajectory-menu').isHidden(),'Escape did not close overflow');
  await page.screenshot({path:path.join(output,'laptop-toolbar.png')});
  await page.setViewportSize({width:1540,height:1000});
  await page.fill('#launch_datetime','2024-01-16T02:00');await page.locator('#launch_datetime').dispatchEvent('change');
  await page.waitForFunction(()=>document.querySelector('#available-day').value==='2024-01-16');
  await page.fill('#launch_datetime','2024-01-17T02:00');await page.locator('#launch_datetime').dispatchEvent('change');
  assert(await page.inputValue('#available-day')==='', 'Unavailable day selected the first archive date');
  await page.selectOption('#available-day','2024-01-15');
  assert((await page.inputValue('#launch_datetime')).startsWith('2024-01-15T02:00'),'Archive date changed the launch hour');
  await page.fill('#launch_datetime','2024-01-15T21:00');await page.locator('#launch_datetime').dispatchEvent('change');
  await page.evaluate(()=>{document.querySelector('#ensemble_members').value='3';document.querySelector('#time_step').value='60';});
  await page.fill('#burst_altitude','3000');await page.fill('#launch_altitude','101');await page.click('#ground-button');await page.waitForFunction(()=>document.querySelector('#ground-comparison').textContent.includes('Model ground:'));
  await page.click('#predict-button');await page.waitForFunction(()=>!document.querySelector('#flight-result').hidden,{},{timeout:30000});
  assert((await page.textContent('#metric-endpoint-label'))==='LANDING','Landing calculation failed');
  await page.locator('#sensitivity-display button').click();
  assert(await page.evaluate(()=>{const layers=flightLayer.getLayers();return layers.some(p=>p instanceof L.Polygon&&String(p.getTooltip()?.getContent()).startsWith('Sensitivity region'))&&!layers.some(p=>String(p.getTooltip()?.getContent()).startsWith('Sensitivity member'));}),'Region did not replace member dots');
  assert(await page.evaluate(()=>{const p=flightLayer.getLayers().find(layer=>layer instanceof L.Polygon);
    const before=JSON.stringify(p.getLatLngs());map.setZoom(map.getZoom()+1,{animate:false});
    return flightLayer.hasLayer(p)&&JSON.stringify(p.getLatLngs())===before;}),'The sensitivity area changed on zoom');
  await page.locator('#sensitivity-display button').click();
  const protectedFlight=await page.evaluate(()=>({job:activeSite().jobId,lat:document.querySelector('#launch_latitude').value,lon:document.querySelector('#launch_longitude').value}));
  await page.locator('#map').click({position:{x:85,y:95}});
  assert(await page.locator('#move-launch-dialog').isVisible(),'Map click did not ask before clearing tracks');
  await page.getByRole('button',{name:'Keep current location',exact:true}).click();
  assert(await page.evaluate(old=>activeSite().jobId===old.job&&document.querySelector('#launch_latitude').value===old.lat&&document.querySelector('#launch_longitude').value===old.lon,protectedFlight),'Cancelling moved the launch or discarded its flight');
  const marker=await page.locator('.launch-icon').boundingBox();await page.mouse.move(marker.x+9,marker.y+9);await page.mouse.down();await page.mouse.move(marker.x+50,marker.y+30,{steps:6});await page.mouse.up();
  assert(await page.locator('#move-launch-dialog').isVisible(),'Marker drag did not protect tracks');await page.keyboard.press('Escape');
  assert(await page.evaluate(old=>activeSite().jobId===old.job,protectedFlight),'Escape discarded the calculation');
  await page.click('#sounding-open');await page.waitForFunction(()=>document.querySelectorAll('#uv-chart path').length===2);
  assert(await page.evaluate(()=>{const a=document.querySelector('#skewt-chart'),b=document.querySelector('#uv-chart');return a.dataset.pressureTop===b.dataset.pressureTop&&a.dataset.pressureBottom===b.dataset.pressureBottom;}),'Profile plots have different pressure ranges');
  assert((await page.textContent('#sounding-note')).includes('Humidity is absent'),'Dry ERA5 invented moisture');
  assert(await page.evaluate(()=>{const panel=document.querySelector('#sounding-panel'),bar=document.querySelector('.dock-divider');
    const p=panel.getBoundingClientRect(),b=bar.getBoundingClientRect(),x=Math.max(p.left,b.left)+10,y=b.top+b.height/2;
    return y>p.top&&y<p.bottom&&document.elementFromPoint(x,y)?.closest('#sounding-panel')===panel;}),'Floating Profile is underneath the split handle');
  await page.screenshot({path:path.join(output,'era5-sounding-desktop.png')});
  const pop=context.waitForEvent('page');await page.click('#sounding-popout');const sounding=await pop;await sounding.waitForLoadState();await sounding.waitForFunction(()=>document.querySelectorAll('#uv-chart path').length===2);
  await sounding.selectOption('#sounding-mode','launch');await sounding.waitForFunction(()=>document.querySelector('#sounding-note').textContent.startsWith('Fixed column'));
  assert((await sounding.locator('#sounding-csv').getAttribute('href')).includes('mode=launch'),'Sounding CSV missing');await sounding.close();
  await page.getByRole('button',{name:'Minimise Flight Profile panel',exact:true}).click();await page.click('#duplicate-flight');await page.fill('#flight_name','WRF coastal launch');
  await page.selectOption('#weather-source','wrf');
  const wrf=await page.locator('#dataset option').evaluateAll(options=>options.find(o=>o.value.startsWith('wrf-')).value);
  await page.selectOption('#dataset',wrf);await page.fill('#launch_datetime','2024-01-15T12:00');await page.selectOption('#flight_mode','ascent');await page.fill('#launch_latitude','52');await page.fill('#launch_longitude','-5');await page.fill('#launch_altitude','100');await page.fill('#burst_altitude','1500');
  await page.click('#predict-button');await page.waitForFunction(()=>!document.querySelector('#flight-result').hidden,{},{timeout:30000});
  assert(Number(await page.inputValue('#launch_longitude'))===-5,'Editing latitude and longitude lost the entered longitude');
  assert((await page.textContent('#metric-stages')).includes('ascent only'),'Target-only flight failed');
  assert(await page.locator('#track-display button').isDisabled()&&await page.locator('#endpoint-focus button').isDisabled(),'Ascent-only controls were not locked');
  assert(await page.locator('#endpoint-focus [data-target]').isVisible()&&await page.locator('#sensitivity-display button').isEnabled(),'Target icon or sensitivity switch incorrect');
  assert(await page.locator('#track-display').getAttribute('data-value')==='ascent','Target-only flight should show ascent');
  assert(await page.locator('#descent-model-field').evaluate(e=>e.classList.contains('is-inactive')&&e.querySelector('select').disabled),'Descent model lacks inactive styling');
  await page.click('#sounding-open');await page.waitForFunction(()=>document.querySelector('#sounding-note').textContent.includes('Dew point from model humidity'));
  assert(await page.locator('#skewt-chart path[stroke="#28645a"]').getAttribute('d'),'WRF dew point missing');await page.screenshot({path:path.join(output,'wrf-target-desktop.png')});
  await page.getByRole('button',{name:'Minimise Flight Profile panel',exact:true}).click();await page.locator('#flight-tabs button').first().click();assert(!(await page.locator('#flight-result').isHidden()),'Switching launch tabs lost the original flight');
  await page.locator('#schedule-details summary').click();await page.selectOption('#run_mode','series');await page.fill('#series_duration','12');await page.fill('#series_interval','3');await page.selectOption('#series_sites','all');
  await page.click('#predict-button');await page.waitForFunction(()=>!document.querySelector('#series-summary').hidden,{},{timeout:30000});
  assert(await page.locator('#series-launches button').count()===4,'Expected four repeated launches per point');assert(await page.locator('#series-launches button.failed').count()===2,'Missing ERA5 coverage not reported');
  await page.locator('#series-launches button:not(.failed)').nth(1).click();assert((await page.textContent('#metric-landing-time')).includes('2024-01-16'),'Series launch selector did not change profile');
  if(await page.locator('#track-display button').isEnabled() && await page.locator('#track-display').getAttribute('data-value')!=='ascent')await page.locator('#track-display button').click();
  if(await page.locator('#endpoint-focus button').isEnabled() && await page.locator('#endpoint-focus').getAttribute('data-value')!=='burst')await page.locator('#endpoint-focus button').click();
  assert(await page.textContent('#metric-endpoint-label')==='BURST','Burst endpoint inspection failed');
  const exportURL=await page.locator('[data-format=csv]').getAttribute('href');const exported=await (await fetch(base+exportURL)).text();assert(exported.startsWith('flight_index,flight_name'),'Series CSV export failed');
  await page.screenshot({path:path.join(output,'multi-launch-series-desktop.png')});
  const otherJobs=await page.evaluate(()=>sites.filter(s=>s.id!==activeId).map(s=>({id:s.id,job:s.jobId})));
  await page.locator('#map').click({position:{x:90,y:100}});await page.locator('#confirm-launch-move').click();
  await page.waitForFunction(()=>!activeSite().result&&!activeSite().series);
  assert(await page.evaluate(others=>!activeSite().result&&!activeSite().series&&others.every(old=>sites.find(s=>s.id===old.id).jobId===old.job),otherJobs),'Confirmed move affected another launch point');
  // Inspect the actual exported Blob in Chromium. Installed app tests cover
  // writing files to disk, which a headless browser sandbox can disallow.
  await page.evaluate(()=>{const create=URL.createObjectURL,click=HTMLAnchorElement.prototype.click;
    URL.createObjectURL=blob=>{window.__setupExport=blob.text();return create(blob);};
    HTMLAnchorElement.prototype.click=function(){if(!this.href.startsWith('blob:'))return click.call(this);window.__setupFilename=this.download;};
  });
  await page.click('#export-session');
  const setup=await page.evaluate(()=>window.__setupExport),setupPath=path.join(tmp,'setups.json');
  assert(JSON.parse(setup).sites.length===2,'Setup export lost launch points');
  assert(await page.evaluate(()=>window.__setupFilename)==='balloon-predictor-launch-setups.json','Setup filename missing');
  fs.writeFileSync(setupPath,setup);
  await page.reload();await page.waitForFunction(()=>document.querySelectorAll('#flight-tabs button').length===2);assert((await page.locator('#flight-tabs button').last().textContent())==='WRF coastal launch','Saved setup names changed');
  await page.setInputFiles('#import-session',setupPath);await page.waitForFunction(()=>document.querySelectorAll('#flight-tabs button').length===2);
  await page.selectOption('#weather-source','demo');
  if(!await page.locator('#run_mode').isVisible())await page.locator('#schedule-details summary').click();
  await page.selectOption('#run_mode','arrival');
  await page.fill('#arrival_datetime','2024-01-16T18:00');await page.fill('#arrival_altitude','1000');await page.fill('#arrival_rates','5,4,3,2');await page.selectOption('#arrival_sites','current');
  await page.click('#predict-button');await page.waitForFunction(()=>!document.querySelector('#arrival-results').hidden,{},{timeout:30000});
  assert(await page.locator('#arrival-results tbody tr').count()===4,'Coordinated arrival table missing');
  await page.locator('#sonde-netcdf').waitFor({state:'visible'});
  await page.click('#sidebar-toggle');assert(await page.locator('#sidebar-reopen').isVisible(),'Sidebar reopen tab missing');await page.click('#sidebar-reopen');
  const profile=page.locator('#profile-panel'),profileHead=profile.locator('[data-window-handle]');
  await profileHead.focus();await profileHead.press('ArrowLeft');
  await profile.locator('.window-button').first().click();const locked=await profile.getAttribute('style');await profileHead.focus();await profileHead.press('ArrowRight');assert(await profile.getAttribute('style')===locked,'Locked panel moved');
  await profile.locator('.window-button').first().click();
  await profile.getByRole('button',{name:'Maximise Flight Details panel',exact:true}).click();
  const restore=profile.getByRole('button',{name:'Unmaximise Flight Details panel',exact:true});
  assert(await restore.getAttribute('title')==='Unmaximise Flight Details panel','Maximise tooltip was not updated');await restore.click();
  await profile.locator('[aria-haspopup=menu]').click();await profile.locator('[data-arrange=right]').click();
  assert(await profile.getAttribute('data-placement')==='right','Right-half arrangement failed');
  const box=await page.locator('#window-area').boundingBox(),heading=await profileHead.boundingBox();
  await page.mouse.move(heading.x+50,heading.y+15);await page.mouse.down();await page.mouse.move(box.x+box.width-3,box.y+4,{steps:12});
  assert(await page.locator('.window-snap-preview').getAttribute('data-zone')==='top-right','Quarter preview missing');await page.mouse.up();
  assert(await profile.getAttribute('data-placement')==='top-right','Quarter pointer snapping failed');
  await page.screenshot({path:path.join(output,'window-quarter-desktop.png')});
  if(!await page.locator('#reset-layout').isVisible())await page.click('#trajectory-more');
  await page.click('#reset-layout');
  await page.screenshot({path:path.join(output,'coordinated-arrival-desktop.png')});
  await page.setViewportSize({width:390,height:844});await page.click('#sidebar-reopen');assert(await page.locator('#sidebar-backdrop').isVisible(),'Mobile drawer backdrop missing');await page.screenshot({path:path.join(output,'mobile-launch-controls.png'),fullPage:true});
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth+2),'Mobile page overflows horizontally');
  await page.locator('#sidebar-backdrop').click({position:{x:385,y:200}});await page.click('#weather-open');assert(await page.locator('#rescan-weather').isVisible(),'Local rescan missing');await page.selectOption('#download-source','ifs');assert(await page.locator('#cycle-field').isVisible(),'Original download window regressed');await page.click('#weather-close');
  // A separate catalogue fixture exercises many downloaded runs without making
  // network requests or replacing the real ERA5/WRF calculations above.
  const catalogue=await browser.newContext({viewport:{width:1540,height:1000}});await catalogue.route('https://**/*',r=>r.abort());
  await catalogue.route(base+'/api/weather',async route=>{const response=await route.fetch(),data=await response.json();
    for(const[source,cycles]of[['ifs',['2026-11-23T00:00:00Z','2026-11-24T12:00:00Z','2026-11-24T00:00:00Z']],['gfs',['2026-11-24T06:00:00Z','2026-11-23T18:00:00Z']]])for(const[i,cycle]of cycles.entries())data.datasets.push({id:source+'-saved-'+i,source,label:source.toUpperCase()+' regional box '+i,start:cycle,end:new Date(Date.parse(cycle)+24*3600000).toISOString(),provenance:{cycle},terrain_available:true});
    await route.fulfill({response,json:data});});
  const catalogPage=await catalogue.newPage();catalogPage.on('pageerror',e=>errors.push(e.message));await catalogPage.goto(base);await catalogPage.waitForFunction(()=>document.querySelector('#flight-tabs button'));
  await catalogPage.selectOption('#weather-source','ifs');
  assert(JSON.stringify(await catalogPage.locator('#dataset option').evaluateAll(options=>options.map(o=>o.value)))===JSON.stringify(['ifs-saved-1','ifs-saved-2','ifs-saved-0','online-ifs']),'IFS runs are not newest first, or another model leaked into the list');
  await catalogPage.selectOption('#dataset','ifs-saved-2');await catalogPage.click('#duplicate-flight');assert(await catalogPage.inputValue('#dataset')==='ifs-saved-2','Duplicating a setup changed its chosen run');
  await catalogPage.selectOption('#weather-source','gfs');assert((await catalogPage.locator('#dataset option').count())===3,'GFS runs were not separated');
  await catalogPage.screenshot({path:path.join(output,'weather-source-runs.png')});
  await catalogPage.selectOption('#weather-source','demo');assert(await catalogPage.locator('#dataset-run-field').isHidden(),'Demo has a meaningless run selector');
  await catalogPage.selectOption('#flight_mode','ascent');await catalogPage.fill('#burst_altitude','20000');await catalogPage.click('#predict-button');await catalogPage.waitForFunction(()=>!document.querySelector('#flight-result').hidden);
  await catalogPage.click('#sounding-open');await catalogPage.waitForSelector('#skewt-chart [data-wind-barb]');
  await catalogPage.getByRole('button',{name:'Maximise Flight Profile panel',exact:true}).click();
  assert(await catalogPage.evaluate(()=>{const a=[...document.querySelectorAll('#skewt-chart [data-pressure-tick]')].find(t=>document.querySelector('#uv-chart [data-pressure-tick="'+t.dataset.pressureTick+'"]')),b=a&&document.querySelector('#uv-chart [data-pressure-tick="'+a.dataset.pressureTick+'"]');return a&&b&&Math.abs(a.getBoundingClientRect().top-b.getBoundingClientRect().top)<1.5;}),'Pressure rows are not visually aligned');
  await catalogPage.screenshot({path:path.join(output,'profile-20km-desktop.png')});
  const before=await catalogPage.locator('#skewt-chart').getAttribute('data-pressure-top');await catalogPage.getByRole('button',{name:'Zoom in Skew-T',exact:true}).click();
  assert(await catalogPage.locator('#skewt-chart').getAttribute('data-pressure-top')!==before,'Skew-T zoom did nothing');
  assert(await catalogPage.locator('#uv-chart').getAttribute('data-pressure-top')===await catalogPage.locator('#skewt-chart').getAttribute('data-pressure-top'),'Linked wind zoom did not follow');
  await catalogPage.locator('#link-profile-zoom').uncheck();const untouched=await catalogPage.locator('#skewt-chart').getAttribute('data-pressure-top');await catalogPage.getByRole('button',{name:'Zoom in winds',exact:true}).click();
  assert(await catalogPage.locator('#skewt-chart').getAttribute('data-pressure-top')===untouched,'Independent wind zoom changed the Skew-T');
  await catalogPage.getByRole('button',{name:'Reset view Skew-T',exact:true}).click();
  await catalogPage.getByRole('button',{name:'Unmaximise Flight Profile panel',exact:true}).click();
  await catalogPage.waitForTimeout(100);const mapCenter=await catalogPage.evaluate(()=>({lat:map.getCenter().lat,lng:map.getCenter().lng}));
  await catalogPage.locator('#sounding-panel [aria-haspopup=menu]').click();await catalogPage.locator('#sounding-panel [data-arrange=right]').click();
  assert(await catalogPage.locator('.tree-divider').count()===2,'Three-panel layout lacks both shared dividers');
  await catalogPage.waitForFunction(center=>map.project(map.getCenter()).distanceTo(map.project(center))<2,mapCenter);
  const shared=await catalogPage.locator('[data-split-path=root]').boundingBox(),oldProfile=await catalogPage.locator('#sounding-panel').boundingBox();
  await catalogPage.mouse.move(shared.x+4,shared.y+80);await catalogPage.mouse.down();await catalogPage.mouse.move(shared.x+44,shared.y+80,{steps:5});await catalogPage.mouse.up();
  assert((await catalogPage.locator('#sounding-panel').boundingBox()).x>oldProfile.x+25,'Vertical shared divider did not resize both columns');
  const cross=await catalogPage.locator('[data-split-path=roota]').boundingBox(),oldMap=await catalogPage.locator('#map-panel').boundingBox();
  await catalogPage.mouse.move(cross.x+80,cross.y+4);await catalogPage.mouse.down();await catalogPage.mouse.move(cross.x+80,cross.y-25,{steps:5});await catalogPage.mouse.up();
  assert((await catalogPage.locator('#map-panel').boundingBox()).height<oldMap.height-15,'Horizontal shared divider did not resize its row');
  await catalogPage.screenshot({path:path.join(output,'three-panel-docking.png')});
  await catalogue.close();
  assert(!errors.length,'Browser errors: '+errors.join('\n'));console.log('Browser regression: PASS · ERA5, WRF, tabs, series, target, soundings, exports, offline coastlines, responsive layout');console.log('Screenshots: '+output);
 }catch(e){
  console.error(e.stack);if(logs)console.error(logs.slice(-5000));
  const failed=browser?.contexts()[0]?.pages()[0];if(failed){
    await failed.screenshot({path:path.join(output,'failed-state.png')}).catch(()=>{});
    console.error(await failed.evaluate(()=>({status:document.querySelector('#flight-status')?.textContent,setup:readSetup(),sites:sites.map(s=>({id:s.id,job:s.jobId,selected:s.selectedIndex,result:Boolean(s.result),flights:s.series?.flights.map(f=>({index:f.index,status:f.status,error:f.error}))}))})).catch(()=>({})));
  }process.exitCode=1;
 }
 finally{if(browser)await browser.close();server.kill();server.once('exit',()=>fs.rmSync(tmp,{recursive:true,force:true}));}
})();
