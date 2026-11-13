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
    const downloads=[];
    window.HTMLAnchorElement.prototype.click=function(){downloads.push({url:this.href,filename:this.download});};
    window.SVGSVGElement.prototype.createSVGRect = () => ({});
    window.HTMLDialogElement.prototype.showModal = function () { this.open = true; };
    window.HTMLDialogElement.prototype.close = function () { this.open = false; };
    Object.defineProperty(window.HTMLElement.prototype, "clientWidth", { get() { return ["map","window-area"].includes(this.id) ? 1000 : 400; } });
    Object.defineProperty(window.HTMLElement.prototype, "clientHeight", { get() { return this.id === "map" ? 600 : this.id === "window-area" ? 700 : 37; } });
    window.eval(fs.readFileSync("frontend/vendor/leaflet/leaflet.js", "utf8"));
    const mapFactory = window.L.map;
    window.L.map = (id, options) => mapFactory(id, { ...options, preferCanvas: false });
    window.eval(fs.readFileSync("frontend/plots.js", "utf8"));
    window.matchMedia=()=>({matches:false});
    window.eval(fs.readFileSync("frontend/ui.js", "utf8"));
    window.eval(fs.readFileSync("frontend/windows.js", "utf8"));
    window.eval(fs.readFileSync("frontend/sensitivity.js", "utf8"));
    window.eval(fs.readFileSync("frontend/trajectory-tools.js", "utf8"));
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
    if(!element("landing_mode").disabled||!element("landing_altitude").disabled)throw new Error("Ascent-only flights did not disable landing settings.");
    if (!element("descent_rate").disabled) throw new Error("Ascent-only control did not disable descent.");
    await element("flight-form").onsubmit({ preventDefault() {} });
    if (element("metric-endpoint-label").textContent !== "TARGET") throw new Error("Altitude endpoint missing.");
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
    element("weather-close").onclick();
    for (const value of ["2","3","4","5","5.01","5.05"]) {
      element("ascent_rate").value=value;
      if(!element("ascent_rate").checkValidity())throw new Error("Ascent input rejected "+value);
      element("descent_rate").value=value;
      if(!element("descent_rate").checkValidity())throw new Error("Descent input rejected "+value);
    }
    element("burst_altitude").value="25000";
    if(!element("burst_altitude").checkValidity()||!element("max_duration_hours").checkValidity())throw new Error("Default altitude/duration rejected by browser constraints.");
    element("ascent_rate").value="5";element("descent_rate").value="5";
    element("dataset").value="online-gfs";element("dataset").onchange();
    if(!element("weather-dialog").open||element("download-source").value!=="gfs"||element("cycle-field").hidden)throw new Error("Online GFS workflow missing.");
    if(!element("predict-button").disabled)throw new Error("Online source submitted before download.");
    if(!element("era5-grid-field").hidden)throw new Error("ERA5-only grid is visible for GFS.");
    if(!element("download-start").checkValidity()||!element("download-end").checkValidity())throw new Error("Suggested forecast minute times are invalid.");
    // A previously selected ERA5 grid must never leak into a forecast request.
    element("download-grid").value="1";
    const realFetch=window.fetch;let sent;
    window.fetch=async(url,options)=>{
      if(String(url)==="/api/weather/download"){sent=JSON.parse(options.body);return {ok:false,json:async()=>({detail:"Test intercepted request"})};}
      return realFetch(url,options);
    };
    await element("weather-form").onsubmit({preventDefault(){}});window.fetch=realFetch;
    if(sent.grid!==.25)throw new Error("Operational forecast inherited ERA5 grid.");
    element("weather-close").onclick();element("dataset").value="demo";element("dataset").onchange();
    element("run_mode").value="arrival";element("arrival_datetime").value="2024-01-15T18:00:00";
    element("arrival_altitude").value="1000";element("arrival_rates").value="5,4,3,2";element("arrival_sites").value="all";
    element("run_mode").onchange();
    await element("flight-form").onsubmit({preventDefault(){}});
    if(element("arrival-results").hidden||window.document.querySelectorAll("#arrival-results tbody tr").length!==8)throw new Error("Coordinated arrival table missing: "+element("flight-status").textContent);
    window.document.querySelectorAll("#arrival-results tbody button")[0].click();
    if(element("netcdf-exports").hidden)throw new Error("NetCDF export cards missing.");
    await element("sonde-netcdf").onclick();
    const sondeLink=element("sonde-status").querySelector("a.download-link");
    if(!sondeLink||!sondeLink.download.endsWith(".nc"))throw new Error("Sonde save link missing: "+element("sonde-status").textContent);
    const sonde=await fetch(sondeLink.href);
    if(!sonde.ok||(new Uint8Array(await sonde.arrayBuffer()))[0]!==137)throw new Error("Sonde link did not return NetCDF4.");
    if(!downloads.some(d=>d.url===sondeLink.href))throw new Error("Prepared sonde did not start its download.");
    element("sidebar-toggle").click();
    if(element("sidebar-toggle").getAttribute("aria-expanded")!=="false"||element("sidebar-reopen").hidden)throw new Error("Sidebar collapse failed.");
    element("sidebar-reopen").click();
    window.BalloonWindows.show("sounding-panel");
    const panel=element("sounding-panel"),heading=panel.querySelector("[data-window-handle]"),lock=panel.querySelector(".window-button");
    const before=parseFloat(panel.style.left);
    heading.dispatchEvent(new window.KeyboardEvent("keydown",{key:"ArrowLeft",bubbles:true}));
    if(parseFloat(panel.style.left)>=before)throw new Error("Unlocked panel did not move.");
    lock.click();const locked=panel.style.left;
    heading.dispatchEvent(new window.KeyboardEvent("keydown",{key:"ArrowRight",bubbles:true}));
    if(panel.style.left!==locked)throw new Error("Locked panel moved.");
    lock.click();const width=parseFloat(panel.style.width);
    panel.querySelector(".window-resize").dispatchEvent(new window.KeyboardEvent("keydown",{key:"ArrowRight",bubbles:true}));
    if(parseFloat(panel.style.width)<=width)throw new Error("Panel resize failed.");
    window.BalloonWindows.minimise("sounding-panel");
    if(!panel.hidden)throw new Error("Panel minimise failed.");
    element("restore-sounding").click();if(panel.hidden)throw new Error("Panel restore failed.");
    // Exercise window state transitions, edge gestures and the mobile drawer.
    window.BalloonWindows.reset();
    const mapPanel=element("map-panel"),profile=element("profile-panel"),profileButton=window.document.querySelector('[data-window-open="profile-panel"]');
    const previousHeight=profile.style.height,previousMap=mapPanel.hidden;
    profile.querySelector('[aria-label="Maximise Flight Details panel"]').click();
    if(!mapPanel.hidden||parseFloat(profile.style.height)<680)throw new Error("Maximise did not fill the workspace.");
    profile.querySelector('[aria-label="Unmaximise Flight Details panel"]').click();
    if(mapPanel.hidden!==previousMap||profile.style.height!==previousHeight)throw new Error("Maximise restore lost split/visibility.");
    profileButton.click();if(!profile.hidden)throw new Error("Profile toolbar did not minimise visible panel.");
    profileButton.click();if(profile.hidden)throw new Error("Profile toolbar did not restore panel.");
    const divider=window.document.querySelector('.dock-divider'),splitHeight=mapPanel.style.height;
    divider.dispatchEvent(new window.KeyboardEvent("keydown",{key:"ArrowUp",bubbles:true}));
    if(parseFloat(mapPanel.style.height)>=parseFloat(splitHeight))throw new Error("Dock divider did not change split ratio.");
    // Dragging any edge detaches the window without jumping to stale geometry.
    const west=profile.querySelector('.resize-w'),oldWidth=parseFloat(profile.style.width),oldX=parseFloat(profile.style.left);
    // First move away from the workspace boundary, then expand the left edge.
    const profileHead=profile.querySelector('[data-window-handle]');
    profileHead.dispatchEvent(new window.KeyboardEvent("keydown",{key:"ArrowRight",bubbles:true}));
    const floatingX=parseFloat(profile.style.left);
    west.dispatchEvent(new window.MouseEvent("pointerdown",{button:0,clientX:100,clientY:100,bubbles:true}));
    west.dispatchEvent(new window.MouseEvent("pointermove",{clientX:95,clientY:100,bubbles:true}));
    west.dispatchEvent(new window.MouseEvent("pointerup",{clientX:95,clientY:100,bubbles:true}));
    if(parseFloat(profile.style.left)>=floatingX)throw new Error("Left edge did not resize outwards.");
    window.matchMedia=()=>({matches:true});window.dispatchEvent(new window.Event("resize"));
    if(element("sidebar-toggle").getAttribute("aria-expanded")!=="false"||!element("sidebar-backdrop").hidden)throw new Error("Mobile drawer initial state wrong.");
    element("sidebar-reopen").click();
    if(element("sidebar-backdrop").hidden||element("flight-sidebar").inert)throw new Error("Mobile drawer did not open.");
    element("sidebar-backdrop").click();
    if(!element("sidebar-backdrop").hidden||element("sidebar-toggle").getAttribute("aria-expanded")!=="false")throw new Error("Mobile backdrop did not close drawer.");
    profileButton.click();if(profile.hidden||!mapPanel.hidden)throw new Error("Mobile panels do not switch exclusively.");
    window.matchMedia=()=>({matches:false});window.dispatchEvent(new window.Event("resize"));
    if(element("sidebar-toggle").closest("#flight-sidebar")==null||element("sidebar-reopen").closest(".map-toolbar")==null)throw new Error("Controls placement regressed.");
    if(window.document.querySelector('[data-window-open="profile-panel"]').textContent!=="Details"||element("restore-sounding").textContent!=="Profile")throw new Error("Panel names are inconsistent.");
    if(!element("warnings").querySelector("summary").textContent.includes("data origins"))throw new Error("Data origins label missing.");
    const disclosure=element("ground-settings");disclosure.open=true;disclosure.dispatchEvent(new window.Event("toggle"));
    if(disclosure.querySelector(".disclosure-indicator").textContent!=="−")throw new Error("Expanded disclosure does not show minus.");
    disclosure.open=false;disclosure.dispatchEvent(new window.Event("toggle"));
    if(disclosure.querySelector(".disclosure-indicator").textContent!=="+")throw new Error("Collapsed disclosure does not show plus.");
    const icon=window.document.querySelector('link[rel="icon"]');
    if(!(await fetch(new URL(icon.href,base))).ok)throw new Error("Browser tab icon is missing.");
    if (errors.length) throw new Error(errors.join("\n"));
    process.stdout.write("Frontend DOM integration: PASS · numeric validation, flights, targets, tabs, series, coordinated arrivals, NetCDF, online GFS controls, window edges/maximise/split/toggle, mobile drawer, grid/time validation, storage paths, favicon, soundings and exports\n");
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
