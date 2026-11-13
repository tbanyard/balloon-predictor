/* Drive the real private controller through the desktop connection page. */
const assert = require('node:assert/strict');
const {spawn} = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const {chromium} = require('playwright');

const root = path.resolve(__dirname, '..');
const venvPython = path.join(root, '.venv', 'bin', 'python');
const python = process.env.BP_TEST_PYTHON || (fs.existsSync(venvPython) ? venvPython : 'python3');
const tmp = fs.mkdtempSync(path.join(process.env.BP_TEST_TMP || os.tmpdir(), 'bp-chooser-'));
const readyFile = path.join(tmp, 'ready.json');
const host = spawn(python, [
  'desktop/host.py', '--data-dir', path.join(tmp, 'user-data'), '--ready-file', readyFile, '--smoke-test',
], {cwd: root, stdio: ['pipe', 'ignore', 'pipe']});
let logs = '', browser;
host.stderr.on('data', data => { logs += data; });
const exited = new Promise(resolve => {
  host.once('close', resolve);
  host.on('error', error => { logs += error.message; });
});
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));

(async () => {
  try {
    for (let i = 0; i < 300 && host.pid && host.exitCode === null && !fs.existsSync(readyFile); i++) await delay(100);
    assert(fs.existsSync(readyFile), 'Desktop controller did not start');
    const info = JSON.parse(fs.readFileSync(readyFile, 'utf8'));
    browser = await chromium.launch();
    const context = await browser.newContext();
    await context.route('https://**/*', route => route.abort());
    const page = await context.newPage();
    // Some private web views can block storage. Startup must not depend on it.
    await page.addInitScript(() => Object.defineProperty(window, 'sessionStorage', {
      get() { throw new DOMException('Storage blocked', 'SecurityError'); },
    }));
    let releaseState, stateRequested, connectRequests = 0;
    const firstState = new Promise(resolve => { stateRequested = resolve; });
    const blockedState = new Promise(resolve => { releaseState = resolve; });
    await page.route(info.url + 'state', async route => {
      stateRequested();
      await blockedState;
      await route.continue();
    });
    page.on('request', req => {
      if (req.url() === info.url + 'connect' && req.method() === 'POST') connectRequests++;
    });
    await page.goto(info.url + '?splash=done');
    await Promise.race([firstState, delay(5000).then(() => { throw new Error('No request for controller state'); })]);
    assert.equal(await page.locator('#splash').count(), 0, 'Native splash must not play twice');
    assert(await page.locator('#connect').isDisabled(), 'Connection enabled before controller state arrived');
    await page.locator('#connection').evaluate(form => form.requestSubmit());
    assert.equal(connectRequests, 0, 'An early submit reached the controller');
    releaseState();
    await page.waitForSelector('#connection[data-ready=true] #connect:enabled');
    await page.locator('#connection').evaluate(form => form.requestSubmit());
    await page.waitForFunction(() => window.L && document.querySelector('#dataset')?.value === 'demo' &&
      document.querySelector('#predict-button')?.disabled === false);
    assert.equal(connectRequests, 1, 'Expected exactly one connection request');
    await page.selectOption('#flight_mode', 'ascent');
    await page.fill('#burst_altitude', '1000');
    await page.fill('#ascent_rate', '5');
    await page.click('#predict-button');
    await page.waitForFunction(() => document.querySelector('#flight-status')?.textContent.startsWith('Flight complete'));
    const exportResponse = await context.request.get(new URL(info.download, page.url()).href);
    assert(exportResponse.ok(), 'Local mode did not serve the model box NetCDF');
    const exportBytes = await exportResponse.body();
    assert.deepEqual(exportBytes.subarray(0, 8), Buffer.from([137, 72, 68, 70, 13, 10, 26, 10]));
    console.log('Desktop startup: deferred connection, local flight and model box NetCDF passed.');
  } finally {
    try {
      if (browser) await browser.close();
    } finally {
      host.stdin.end();
      const timeout = setTimeout(() => host.kill('SIGKILL'), 5000);
      try { await exited; } finally { clearTimeout(timeout); }
      fs.rmSync(tmp, {recursive: true, force: true});
    }
  }
})().catch(error => {
  console.error(logs, error);
  process.exitCode = 1;
});
