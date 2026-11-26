const assert=require('node:assert/strict'),fs=require('node:fs'),os=require('node:os'),path=require('node:path'),{spawn}=require('node:child_process');
const {chromium}=require('playwright');
const root=path.resolve(__dirname,'..'),python=process.env.BP_TEST_PYTHON||'python3';
const tmp=fs.mkdtempSync(path.join(process.env.BP_TEST_TMP||os.tmpdir(),'bp-users-'));
const ready=path.join(tmp,'ready.json');
const host=spawn(python,['scripts/shared-browser-fixture.py',tmp,ready],{cwd:root,stdio:['pipe','ignore','pipe']});
let logs='',browser;host.stderr.on('data',data=>logs+=data);
const closed=new Promise(resolve=>{host.once('close',resolve);host.on('error',e=>logs+=e.message);});
const delay=ms=>new Promise(resolve=>setTimeout(resolve,ms));
(async()=>{
  try{
    for(let i=0;i<150&&!fs.existsSync(ready)&&host.pid&&host.exitCode===null;i++)await delay(100);
    assert(fs.existsSync(ready),'The shared HTTP fixture did not start');
    const info=JSON.parse(fs.readFileSync(ready,'utf8'));
    browser=await chromium.launch();
    const alice=await browser.newContext(),bob=await browser.newContext();
    for(const context of [alice,bob])await context.route('https://**/*',route=>route.abort());
    const first=await alice.newPage(),second=await bob.newPage();
    await first.goto(info.url);
    await first.waitForSelector('#signin-form');
    assert.equal(await first.locator('input[type=password]').count(),1);
    assert(!(await first.locator('body').textContent()).includes('python -m'),'Manual command leaked into normal sign-in');
    if(process.env.BP_TEST_OUTPUT){
      fs.mkdirSync(process.env.BP_TEST_OUTPUT,{recursive:true});
      await first.screenshot({path:path.join(process.env.BP_TEST_OUTPUT,'login-desktop.png')});
      await first.setViewportSize({width:390,height:844});
      assert(await first.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'Mobile login overflowed horizontally');
      await first.screenshot({path:path.join(process.env.BP_TEST_OUTPUT,'login-mobile.png'),fullPage:true});
      await first.setViewportSize({width:1280,height:900});
    }
    // The existing native-app handoff remains compatible.
    await first.goto(info.url+'/signin#'+info.first);
    await first.waitForFunction(()=>document.querySelector('#predict-button')?.disabled===false);
    assert.equal(first.url(),info.url+'/','Sign-in token was not cleared from the browser address');
    let data=await (await alice.request.get(info.url+'/api/storage')).json();
    assert.equal(data.workspace_user,'alice');
    assert(data.data_dir.endsWith('/users/alice'));
    await first.click('#weather-open');
    await first.click('#signout-button');
    await first.waitForSelector('#signin-form');
    await first.fill('#username','alice');await first.fill('#password','incorrect-password');
    await first.click('#signin-button');
    await first.waitForFunction(()=>document.querySelector('#signin-status').textContent.includes('did not accept'));
    assert.equal(await first.inputValue('#password'),'','Password remained in the form after submitting');
    await first.fill('#password',info.password);await first.click('#signin-button');
    await first.waitForFunction(()=>document.querySelector('#predict-button')?.disabled===false);
    assert(!first.url().includes('#'),'Browser password sign-in exposed a ticket');
    await first.click('#weather-open');
    await first.click('#rescan-weather');
    await first.waitForFunction(()=>document.querySelector('#rescan-status').textContent.startsWith('Server data index refreshed'));
    await first.click('#weather-close');
    await first.selectOption('#flight_mode','ascent');await first.fill('#burst_altitude','1000');await first.click('#predict-button');
    await first.waitForFunction(()=>document.querySelector('#flight-status').textContent.startsWith('Flight complete'));
    await first.evaluate(()=>window.__reloginMarker='retained flight');
    const cookieBefore=(await alice.cookies()).find(c=>c.name.startsWith('bp_session_'));
    await alice.clearCookies({name:cookieBefore.name});
    const pending=first.evaluate(()=>api('/api/storage'));
    await first.waitForSelector('#connection-recovery[open]');
    await first.locator('#connection-recovery input[type=text]').fill('alice');
    await first.locator('#connection-recovery input[type=password]').fill(info.password);
    await first.locator('#connection-recovery button[type=submit]').click();
    assert.equal((await pending).workspace_user,'alice');
    assert.equal(await first.evaluate(()=>window.__reloginMarker),'retained flight');
    assert.equal(await first.locator('#flight-result').isVisible(),true,'Session renewal lost the flight');
    const exportURL=await first.locator('[data-format=json]').getAttribute('href');
    assert((await alice.request.get(new URL(exportURL,info.url).href)).ok(),'Own prediction export failed');
    await second.goto(info.url);
    await second.fill('#username','bob');await second.fill('#password',info.password);await second.click('#signin-button');
    await second.waitForFunction(()=>document.querySelector('#predict-button')?.disabled===false);
    data=await (await bob.request.get(info.url+'/api/storage')).json();
    assert.equal(data.workspace_user,'bob');
    assert.equal((await bob.request.get(new URL(exportURL,info.url).href)).status(),404,'Another account could read the flight');
    const cookie=(await bob.cookies()).find(c=>c.name.startsWith('bp_session_'));
    assert(cookie.httpOnly&&cookie.sameSite==='Strict','Session cookie protections are missing');
    // The Android WebView supplies this marker after a page loads. The
    // existing header must disappear while all actions remain in the DOM.
    await second.evaluate(()=>document.documentElement.dataset.host='android');
    assert(await second.locator('.topbar').isHidden());
    assert.equal(await second.locator('#weather-open').count(),1);
    // A server code is requested in the same browser, with no manual link.
    await second.evaluate(()=>document.documentElement.removeAttribute('data-host'));
    await second.click('#weather-open');await second.click('#signout-button');
    await second.waitForSelector('#signin-form');
    await second.fill('#username','otp');await second.fill('#password',info.password);await second.click('#signin-button');
    await second.waitForSelector('#signin-challenge:not([hidden])');
    await second.fill('#answer-0',info.code);await second.click('#challenge-button');
    await second.waitForFunction(()=>document.querySelector('#predict-button')?.disabled===false);
    assert.equal((await (await bob.request.get(info.url+'/api/storage')).json()).workspace_user,'otp');
    // Reconnecting a native app replaces an existing cookie correctly.
    await second.goto(info.url+'/signin#'+info.second);
    await second.waitForFunction(()=>document.querySelector('#predict-button')?.disabled===false);
    assert.equal((await (await bob.request.get(info.url+'/api/storage')).json()).workspace_user,'bob');
    console.log('Shared browser: password login, retry, code challenge, logout, native reconnect, private workspaces, rescan and Android header passed.');
  }finally{
    try{if(browser)await browser.close();}finally{
      host.stdin.end();const timeout=setTimeout(()=>host.kill('SIGKILL'),5000);
      try{await closed;}finally{clearTimeout(timeout);fs.rmSync(tmp,{recursive:true,force:true});}
    }
  }
})().catch(error=>{console.error(logs,error);process.exitCode=1;});
