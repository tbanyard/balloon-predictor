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
p=Path(sys.argv[1]);opts=dict(lat=[55,53,51],lon=[354,356,358],constant=False)
make_file(p/'era5/2024/era5_2024d015.nc',date='2024-01-15',times=(21,23),**opts)
make_file(p/'era5/2024/era5_2024d016.nc',date='2024-01-16',times=(0,3),**opts)
make_topography(p/'era5/era5_topo.nc',lat=[55,53,51],lon=[354,356,358],heights=100)
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
  assert(ready,'Server failed: '+logs);browser=await chromium.launch({headless:true, ...(process.env.BP_TEST_CHROME?{executablePath:process.env.BP_TEST_CHROME,args:["--no-sandbox","--disable-dev-shm-usage"]}: {})});const context=await browser.newContext({viewport:{width:1540,height:1000},deviceScaleFactor:1});
  await context.route('https://**/*',route=>route.abort());const page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto(base);await page.waitForFunction(()=>document.querySelector('#flight-tabs button'));
  assert(await page.inputValue('#dataset')==='era5','ERA5 is not the default');assert(await page.locator('.version').textContent()==='v1.1','Version badge missing');
  await page.selectOption('#available-day','2024-01-15');await page.waitForFunction(()=>document.querySelector('#launch_datetime').value.startsWith('2024-01-15T21:00'));
  await page.fill('#burst_altitude','25000');await page.click('#ground-button');await page.waitForFunction(()=>Number(document.querySelector('#launch_altitude').value)>100);
  await page.click('#predict-button');await page.waitForFunction(()=>!document.querySelector('#flight-result').hidden,{},{timeout:30000});
  assert((await page.textContent('#metric-endpoint-label'))==='LANDING','Landing calculation failed');
  await page.click('#sounding-open');await page.waitForFunction(()=>document.querySelectorAll('#uv-chart path').length===2);
  assert((await page.textContent('#sounding-note')).includes('Humidity is absent'),'Dry ERA5 invented moisture');
  await page.screenshot({path:path.join(output,'era5-sounding-desktop.png')});
  const pop=context.waitForEvent('page');await page.click('#sounding-popout');const sounding=await pop;await sounding.waitForLoadState();await sounding.waitForFunction(()=>document.querySelectorAll('#uv-chart path').length===2);
  await sounding.selectOption('#sounding-mode','launch');await sounding.waitForFunction(()=>document.querySelector('#sounding-note').textContent.startsWith('Fixed column'));
  assert((await sounding.locator('#sounding-csv').getAttribute('href')).includes('mode=launch'),'Sounding CSV missing');await sounding.close();
  await page.click('#sounding-close');await page.click('#duplicate-flight');await page.fill('#flight_name','WRF coastal launch');
  const wrf=await page.locator('#dataset option').evaluateAll(options=>options.find(o=>o.value.startsWith('wrf-')).value);
  await page.selectOption('#dataset',wrf);await page.selectOption('#flight_mode','ascent');await page.fill('#launch_latitude','52');await page.fill('#launch_longitude','-5');await page.fill('#launch_altitude','100');await page.fill('#burst_altitude','1500');
  await page.click('#predict-button');await page.waitForFunction(()=>!document.querySelector('#flight-result').hidden,{},{timeout:30000});
  assert((await page.textContent('#metric-stages')).includes('ascent only'),'Target-only flight failed');
  await page.click('#sounding-open');await page.waitForFunction(()=>document.querySelector('#sounding-note').textContent.includes('Dew point from model humidity'));
  assert(await page.locator('#skewt-chart path[stroke="#28645a"]').getAttribute('d'),'WRF dew point missing');await page.screenshot({path:path.join(output,'wrf-target-desktop.png')});
  await page.click('#sounding-close');await page.locator('#flight-tabs button').first().click();assert(!(await page.locator('#flight-result').isHidden()),'Switching launch tabs lost the original flight');
  await page.locator('#schedule-details summary').click();await page.selectOption('#run_mode','series');await page.fill('#series_duration','12');await page.fill('#series_interval','3');await page.selectOption('#series_sites','all');
  await page.click('#predict-button');await page.waitForFunction(()=>!document.querySelector('#series-summary').hidden,{},{timeout:30000});
  assert(await page.locator('#series-launches button').count()===4,'Expected four repeated launches per point');assert(await page.locator('#series-launches button.failed').count()===2,'Missing ERA5 coverage not reported');
  await page.locator('#series-launches button:not(.failed)').nth(1).click();assert((await page.textContent('#metric-landing-time')).includes('2024-01-16'),'Series launch selector did not change profile');
  await page.selectOption('#track-display','ascent');await page.selectOption('#endpoint-focus','burst');
  assert(await page.textContent('#metric-endpoint-label')==='TARGET / BURST','Burst endpoint inspection failed');
  const exportURL=await page.locator('[data-format=csv]').getAttribute('href');const exported=await (await fetch(base+exportURL)).text();assert(exported.startsWith('flight_index,flight_name'),'Series CSV export failed');
  await page.screenshot({path:path.join(output,'multi-launch-series-desktop.png')});
  const download=page.waitForEvent('download');await page.click('#export-session');const file=await download,setupPath=path.join(tmp,'setups.json');await file.saveAs(setupPath);assert(JSON.parse(fs.readFileSync(setupPath)).sites.length===2,'Setup export lost launch points');
  await page.reload();await page.waitForFunction(()=>document.querySelectorAll('#flight-tabs button').length===2);assert((await page.locator('#flight-tabs button').last().textContent())==='WRF coastal launch','Saved setup names changed');
  await page.setInputFiles('#import-session',setupPath);await page.waitForFunction(()=>document.querySelectorAll('#flight-tabs button').length===2);
  await page.setViewportSize({width:390,height:844});await page.screenshot({path:path.join(output,'mobile-launch-controls.png'),fullPage:true});
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth+2),'Mobile page overflows horizontally');
  await page.click('#weather-open');assert(await page.locator('#rescan-weather').isVisible(),'Local rescan missing');await page.selectOption('#download-source','ifs');assert(await page.locator('#cycle-field').isVisible(),'Original download window regressed');await page.click('#weather-close');
  assert(!errors.length,'Browser errors: '+errors.join('\n'));console.log('Browser regression: PASS · ERA5, WRF, tabs, series, target, soundings, exports, offline coastlines, responsive layout');console.log('Screenshots: '+output);
 }catch(e){console.error(e.stack);if(logs)console.error(logs.slice(-5000));process.exitCode=1;}
 finally{if(browser)await browser.close();server.kill();server.once('exit',()=>fs.rmSync(tmp,{recursive:true,force:true}));}
})();
