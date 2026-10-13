/* Optional developer check: npm install && npm run test:frontend.
   Exercises actual frontend code against the real Python API in a virtual DOM.
   Does not replace visual browser testing. */
const fs = require("fs");
const path = require("path");
const os = require("os");
const { spawn } = require("child_process");
const { JSDOM, VirtualConsole } = require("jsdom");

const root = path.resolve(__dirname, "..");
process.chdir(root);
const envPython = process.env.BP_TEST_PYTHON;
const localPython = process.platform === "win32" ? path.join(root, ".venv", "Scripts", "python.exe") : path.join(root, ".venv", "bin", "python");
const python = envPython || (fs.existsSync(localPython) ? localPython : process.platform === "win32" ? "python" : "python3");
const port = Number(process.env.BP_TEST_PORT || 18002);
const base = "http://127.0.0.1:" + port + "/";
const data = fs.mkdtempSync(path.join(process.env.BP_TEST_TMP || os.tmpdir(), "balloon-ui-test-"));
const server = spawn(python, ["-m", "balloon_predictor", "serve", "--port", String(port)], {
  cwd: root, env: { ...process.env, BP_DATA_DIR: data }, stdio: ["ignore", "ignore", "pipe"]
});
let logs = "", dom;
server.stderr.on("data", bytes => { logs += bytes.toString(); });
server.on("error", error => { logs += error.message; });
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));

(async () => {
  try {
    let ready = false;
    for (let attempt = 0; attempt < 100; attempt++) {
      try { const response = await fetch(base + "api/health"); if (response.ok) { ready = true; break; } }
      catch {}
      await delay(100);
    }
    if (!ready) throw new Error("Test server did not start: " + logs);
    const errors = [], console = new VirtualConsole();
    console.on("jsdomError", error => errors.push(error.message));
    const html = fs.readFileSync("frontend/index.html", "utf8").replace(/<script[^>]+src=[^>]+><\/script>/g, "");
    dom = new JSDOM(html, { url: base, runScripts: "outside-only", pretendToBeVisual: true, virtualConsole: console });
    const window = dom.window;
    window.fetch = (url, options) => fetch(new URL(url, base), options);
    window.SVGSVGElement.prototype.createSVGRect = () => ({});
    window.HTMLDialogElement.prototype.showModal = function () { this.open = true; };
    window.HTMLDialogElement.prototype.close = function () { this.open = false; };
    Object.defineProperty(window.HTMLElement.prototype, "clientWidth", { get() { return this.id === "map" ? 1000 : 400; } });
    Object.defineProperty(window.HTMLElement.prototype, "clientHeight", { get() { return this.id === "map" ? 600 : 37; } });
    window.eval(fs.readFileSync("frontend/vendor/leaflet/leaflet.js", "utf8"));
    const mapFactory = window.L.map;
    window.L.map = (id, options) => mapFactory(id, { ...options, preferCanvas: false });
    window.eval(fs.readFileSync("frontend/plots.js", "utf8"));
    window.eval(fs.readFileSync("frontend/app.js", "utf8"));
    await delay(200);
    const element = id => window.document.getElementById(id);
    element("dataset").value = "demo";
    element("dataset").onchange();
    element("launch_datetime").value = "2024-01-15T12:00:00";
    element("burst_altitude").value = "1000";
    await element("flight-form").onsubmit({ preventDefault() {} });
    if (element("flight-result").hidden) throw new Error(element("flight-status").textContent);
    if (window.document.querySelectorAll("#altitude-chart polyline").length !== 2) throw new Error("Altitude profile missing.");
    if (!window.document.querySelector('[data-format="csv"]').href.includes("/export/csv")) throw new Error("CSV export missing.");
    await element("weather-open").onclick();
    element("download-source").value = "ifs";
    element("download-source").onchange();
    if (element("cycle-field").hidden) throw new Error("IFS cycle selector missing.");
    element("landing_mode").value = "fixed";
    element("landing_mode").onchange();
    if (element("landing-field").hidden) throw new Error("Fixed landing field missing.");
    element("ascent_rate").dispatchEvent(new window.Event("input", { bubbles: true }));
    if (!element("flight-result").hidden || !element("exports").hidden) throw new Error("Changed inputs left a stale prediction visible.");

    element("weather-close").onclick();
    element("flight_mode").value = "ascent";
    element("flight_mode").onchange();
    if (!element("descent_rate").disabled) throw new Error("Ascent-only control did not disable descent.");
    await element("flight-form").onsubmit({ preventDefault() {} });
    if (element("metric-endpoint-label").textContent !== "TARGET / BURST") throw new Error("Altitude endpoint missing.");
    if (!element("metric-stages").textContent.includes("ascent only")) throw new Error("Ascent endpoint still reports landing.");
    element("sounding-open").onclick();
    await delay(30);
    if (window.document.querySelectorAll("#uv-chart path").length !== 2) throw new Error("Separate u/v lines missing.");
    if (!element("sounding-note").textContent.includes("Humidity is absent")) throw new Error("Missing ERA5/demo moisture was not labelled.");
    if (!element("sounding-popout").href.includes("sounding.html?job=")) throw new Error("Sounding popout missing.");
    element("sounding-mode").value = "launch";
    await element("sounding-mode").onchange();
    if (!element("sounding-note").textContent.startsWith("Fixed column")) throw new Error("Fixed launch sounding failed.");
    element("sounding-close").onclick();

    element("duplicate-flight").onclick();
    if (window.document.querySelectorAll("#flight-tabs button").length !== 2) throw new Error("Multiple launch tabs missing.");
    element("flight_name").value = "Second launch";
    element("flight_name").dispatchEvent(new window.Event("input", { bubbles: true }));
    element("launch_longitude").value = "-3.5";
    element("launch_longitude").dispatchEvent(new window.Event("change", { bubbles: true }));
    await element("flight-form").onsubmit({ preventDefault() {} });
    window.document.querySelectorAll("#flight-tabs button")[0].click();
    if (element("flight-result").hidden) throw new Error("Switching launch points lost a saved result.");
    element("run_mode").value = "series";
    element("series_duration").value = "3";
    element("series_interval").value = "1";
    element("series_sites").value = "all";
    element("run_mode").onchange();
    await element("flight-form").onsubmit({ preventDefault() {} });
    if (!element("series-summary").textContent.startsWith("3 / 3")) throw new Error("Repeated launches failed.");
    if (window.document.querySelectorAll("#series-launches button").length !== 3) throw new Error("Hourly launch selection missing.");
    window.document.querySelectorAll("#series-launches button")[1].click();
    if (!element("metric-landing-time").textContent.includes("13:")) throw new Error("Selecting a scheduled launch did not update its profile.");
    window.document.querySelectorAll("#flight-tabs button")[1].click();
    if (!element("series-summary").textContent.startsWith("3 / 3")) throw new Error("Series did not cover both launch points.");
    element("weather-open").onclick();
    await element("rescan-weather").onclick();
    if (!element("rescan-status").textContent.includes("refreshed")) throw new Error("Local rescan failed.");
    const guides = window.BalloonPlots.moistLapse([925,850,700,500,300,200],278.15).map(t => t - 273.15);
    const reference = [5,.996,-8.89,-28.389,-60.12,-83.343];
    if (guides.some((t, i) => Math.abs(t - reference[i]) > 1)) throw new Error("Saturated guides differ from MetPy's documented example.");
    if (errors.length) throw new Error(errors.join("\n"));
    process.stdout.write("Frontend DOM integration: PASS · flights, exact targets, tabs, multi-point series, sounding modes, u/v, rescan, exports, thermodynamic guides\n");
  } catch (error) {
    process.stderr.write(error.stack + "\n");
    process.exitCode = 1;
  } finally {
    if (dom) dom.window.close();
    server.kill();
    // Generated test data only; never the user's configured data directory.
    server.once("exit", () => fs.rmSync(data, { recursive: true, force: true }));
  }
})();
