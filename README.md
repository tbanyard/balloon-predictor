# BalloonPredictor v1.1

A complete local balloon-prediction application: a Leaflet map, Python API,
weather downloads/imports, a trajectory engine, sensitivity runs, and exports.
Runs on your computer at **http://localhost:8000**.

Start with [QUICKSTART.md](QUICKSTART.md). Version 1.1 adds direct ERA5 viewer
archive support, large-file WRF reading, launch-point tabs, repeated launches,
altitude endpoints and synthetic radiosonde plots. Both apps stay independent.

See [CHANGELOG.md](CHANGELOG.md) and [docs/GIT.md](docs/GIT.md) for the release
changes and a ready-to-follow GitHub upload guide.

| Source | What it is | How this project uses it |
|---|---|---|
| ERA5 | Final historical reanalysis | Reads the ERA5 viewer L137 archive directly by default; also retains CDS downloads and pressure-level imports |
| ERA5T | Preliminary recent reanalysis, usually about five days behind real time | Uses the same CDS datasets; identifies GRIB expver=5 and records the available release |
| ECMWF operational / IFS | Actual weather forecasts | Downloads ECMWF open data, with a selected cycle or the latest available 00/12 UTC cycle |
| WRF | Your own model output | Reads large native WRF-ARW files directly from /data1/tim/SkySightWRF, with separate run/domain choices; cropped imports remain available |
| Demo | Synthetic analytical atmosphere | Lets you run the whole application without credentials or weather downloads |

“ERA5 operational” can mean the regularly updated ERA5/ERA5T reanalysis, or
ECMWF's operational forecast model. Both are included, under distinct names.
The ERA5 MARS stream called “oper” refers to the high-resolution ERA5 stream;
it does not turn ERA5 into a future forecast.

## Start here

Install **Python 3.12 or newer**, extract the ZIP, and open a terminal inside
the extracted balloon-predictor directory. Initial installation needs internet
access. The Linux/macOS launcher uses a per-user environment under
`~/.cache/balloon-predictor/venv`, or reuses an explicitly activated environment.
Generated data is per-user under `~/.local/share/balloon-predictor` unless
`BP_DATA_DIR` is set. Existing project-local `.venv` installations are supported.

**macOS / Linux**

~~~bash
chmod +x start.sh
./start.sh
~~~

**Windows PowerShell**

~~~powershell
.\start.ps1
~~~

If your PowerShell policy blocks the script, use the manual commands below.
The scripts install the tested dependency snapshot on the first run and then
start the server. Open **http://localhost:8000** and click **Calculate flight**
to use the selected ERA5 archive. To try without local data, choose
**Demo · synthetic winds** explicitly. Press Ctrl+C to stop the server.

If installation is interrupted, use the manual install steps to finish it
before running the start script again.

**Manual installation**

~~~bash
python -m venv .venv
# macOS/Linux:
source .venv/bin/activate
# Windows PowerShell instead:
# .\.venv\Scripts\Activate.ps1

python -m pip install -r requirements.lock.txt
python -m pip install --no-deps -e .
balloon-predictor serve
~~~

On Linux, if python3 -m venv is unavailable, install your distribution's
python3-venv package. On Windows, use the Python installation that provides
Python 3.12+; py -3.12 can substitute for python in the first command.

`server.py` and the launchers run directly from the extracted folder. Manual
editable installation adds the `balloon-predictor` CLI; `python -m
balloon_predictor` works without that step. Keep the project folder.

## Direct local ERA5: the ERA5 viewer format

The initial dataset is **ERA5 · local model-level archive**. No CDS credentials
or conversion are needed for existing daily files:

```text
/data3/ERA5/2016/era5_2016d269.nc
/data3/ERA5/2016/era5_2016d270.nc
/data3/ERA5/era5_topo.nc
```

This is the same packed T/U/V/lnsp L137 format used by ERA5 viewer v1.2.
Full-level pressure comes from the bundled L137 coefficients and actual surface
pressure. Hydrostatic heights use T and optional q; absent q is explicitly a
dry-air height approximation. Matching surface geopotential is required for
AMSL trajectories. Geopotential heights are converted to geometric altitude
for the integrator, unlike the viewer's geopotential-height display.

The predictor reads only the needed vertical columns, linearly interpolates
between actual neighbouring timestamps, and follows adjacent daily files
across midnight. The launch controls list available dates/times. Missing days,
long gaps, lost domain coverage and flights above the represented model top
produce explanations. No automatic fallback to synthetic weather occurs.

Set `era5_root`, `wrf_root`, `cache_mb` and `max_time_gap_hours` in `config.yml`.
The roots also accept `BP_ERA5_ROOT` / `BP_WRF_ROOT`, and the server accepts
`--era5-root` / `--wrf-root` / `--cache-mb`. A personal config can be passed to
`bash start.sh --config /path/to/my-config.yml`.

## Launch points, schedules and endpoint comparisons

**Add launch point** / **Duplicate** create independent setups (up to 20).
Tabs in **03 / Flight Profile** and the map's launch markers switch between them.
Setups are saved in this browser; **Save launch setups** / **Load setups** let
you move them between browsers. Calculated results stay in the server's results
folder and can be exported; browser reload restores setups rather than tracks.

The **Flight endpoint** selector chooses a full burst-and-land flight or an
ascent-only calculation terminating at the exact target/burst altitude. Full
flights still allow comparison of burst positions. The map controls separately
select visible stages and landing/altitude endpoint markers, including
sensitivity endpoints.

Expand **Repeated launches**, set a duration and interval, and choose the
current point or all saved points. The launch window excludes its end: 24 h at
1 h intervals gives 24 launches; 12 h at 3 h gives four. Each point uses its own
start time and flight settings. Select an individual launch to inspect its
profile. Successful tracks remain visible when other launches lack coverage;
failed launches show their reasons. Export buttons export the whole series.

```bash
python -m balloon_predictor predict-series examples/launch-series.yml \
  --output data/results/my-series
```

A series is limited to 256 launch times across all points and 1024 trajectories
including sensitivity members, to keep processing reviewable and bounded.

## Skew-T and u/v profiles

After a successful prediction, **Open Skew-T & u/v alongside** opens the
atmospheric diagnostics beside the map. The initial sounding samples the model
at the balloon's changing ascent position and timestamp. **Fixed launch
column** holds position/time fixed and samples up to the same target altitude.
The popout window provides SVG, CSV and print/PDF export.

Temperature is plotted against log pressure on a skewed temperature axis,
with dry/saturated/mixing-ratio guides. The wind panel has separate eastward
u and northward v curves versus actual altitude in km AMSL. ERA5 q or WRF
QVAPOR enables dew point. Absent humidity produces temperature and wind curves
only. These are synthetic model soundings, without sensor lag or instrument
corrections. No CAPE/CIN or parcel analysis is implied; the sounding is limited
to the altitude reached by the predicted ascent.

## Add your ERA5 credentials

Follow the [official CDS API setup](https://cds.climate.copernicus.eu/how-to-api).
Create a file named **.cdsapirc in your home directory**:

~~~yaml
url: https://cds.climate.copernicus.eu/api
key: YOUR_PERSONAL_ACCESS_TOKEN
~~~

On Linux/macOS this is ~/.cdsapirc. On Windows it is typically
C:\Users\YOUR_USERNAME\.cdsapirc. Avoid saving it as .cdsapirc.txt.

Sign in to CDS and manually accept the terms for both:

- [ERA5 hourly pressure levels](https://cds.climate.copernicus.eu/datasets/reanalysis-era5-pressure-levels?tab=download)
- [ERA5 hourly single levels](https://cds.climate.copernicus.eu/datasets/reanalysis-era5-single-levels?tab=download)

The included .cdsapirc.example is a template, not an active credential file.
If you want your credential file beside the project instead, copy it to
.cdsapirc and set CDSAPI_RC to its absolute path. Alternatively, copy
.env.example to .env and set CDSAPI_URL/CDSAPI_KEY there.

Credentials are used by the Python process only. The frontend never receives
them. The example files have placeholders; no actual credentials are included.

## Download and select weather

In the browser, open **Manage weather**, select a source and provide:

1. A unique dataset name.
2. A UTC window covering the entire flight, with time to spare after landing.
3. An area covering the entire expected drift, not just the launch point.
4. Surface data enabled if you want model terrain and near-ground winds.

Click **Download & add dataset**. Once it finishes, the dataset appears in the
flight dropdown. Downloading is a separate background job; predictions reuse
the local weather. CDS can queue requests for a substantial time.

ERA5 downloads use hourly times, with the requested window rounded outwards.
All 37 pressure levels (1000–1 hPa) are requested, along with U, V, temperature,
geopotential and vertical pressure velocity. The optional surface request adds
10 m winds, 2 m temperature, surface pressure and terrain geopotential.

ERA5T is **not a separate CDS product_type**. CDS returns the available release
for the dates requested. The app records GRIB expver=1/5. Selecting “ERA5 final”
rejects preliminary fields; selecting ERA5T permits the available release.
Previously downloaded preliminary fields stay in the cache until you explicitly
refresh them. Older ERA5T dates may already have been replaced by final ERA5.

IFS uses open data without CDS credentials. It downloads selected global GRIB
fields and crops them locally, so downloads can be larger than an ERA5 regional
request. The included IFS levels are 1000, 925, 850, 700, 600, 500, 400, 300, 250,
200, 150, 100, 50 and 10 hPa. The 50–10 hPa gap matters for stratospheric flights.
The importer requires real height fields and does not fabricate winds above the
model top. A 25 km burst can be covered by 10 hPa, but vertical detail is limited.

Automatic IFS selection uses 00/12 UTC cycles for a longer forecast window.
Explicit 06/18 UTC cycles are also supported. This implementation conservatively
allows 00/12 forecasts to 240 h and 06/18 forecasts to 90 h, with available
3 h/6 h steps. ECMWF's open-data portal retains recent cycles for a short period;
for older cycles, import your archived files instead.

Each download/import is limited to 72 hours for remote requests. The configured
materialized weather limit is 2048 MB. This applies to downloaded/cropped
imports. Direct ERA5/WRF readers do not materialize weather volumes; use them
for large source files. See docs/WRF.md for cache and geometry memory details.

**Command-line downloads**

~~~bash
balloon-predictor download examples/download-era5.yml
balloon-predictor download examples/download-era5t.yml
balloon-predictor download examples/download-ifs.yml
balloon-predictor list
balloon-predictor inspect era5-uk-20240115
~~~

Edit the example dates first where appropriate. The dated ERA5 example is
historical; the ERA5T and forecast example windows eventually become stale.
Use another name to keep multiple datasets. To replace an existing one,
use --overwrite. Raw download cache files are refreshed only when refresh: true
is also set.

## Use defined WRF output

For direct use, keep `wrfout*` files under `/data1/tim/SkySightWRF` (one folder
per run), click **Manage weather → Rescan local data**, and select the run/domain.
This is the recommended path for large files. The original cropped importer
below remains available.

Put your wrfout files in data/raw/ or use absolute paths to their existing
location. Then:

~~~bash
balloon-predictor import-wrf --name wrf-d02 \
  --label "My WRF run · domain 02" \
  --files "data/raw/wrfout_d02_*"
~~~

On PowerShell put the command on one line; the backslash continuation is a
macOS/Linux shell convention. Quote the wildcard so Python expands it.

Or edit the WRF entry in **config.yml**, set enabled: true, define your paths
and optional crop/time interval, and run:

~~~bash
balloon-predictor import-config
~~~

Each entry is one fixed WRF horizontal domain. Define a different dataset name
for d01, d02 or another run. Restart is unnecessary: reopen Manage weather to
refresh the dropdown after a CLI import.

The adapter destaggers U/V/W and geopotential, rotates winds into east/north
coordinates, derives temperature from potential temperature, uses P+PB for
pressure and HGT for terrain, and reads every selected time. It rejects moving
nests, mixed grids, conflicting duplicate timestamps and missing rotation
coefficients. It does not silently switch to an outer domain.

See **[docs/WRF.md](docs/WRF.md)** for required fields and large-output guidance.
Here “WRF input” means WRF output used as input to this balloon predictor.
This project does **not** create wrfinput, wrfbdy or WPS forcing files.

**Already downloaded ERA5 or IFS files**

~~~bash
balloon-predictor import-era5 --name my-era5 \
  --files "data/raw/era5-pressure.nc" "data/raw/era5-surface.nc"
balloon-predictor import-ifs --name my-ifs \
  --files "data/raw/ifs*.grib2" --area 60 -12 48 5
~~~

Use only NetCDF files or only GRIB files in a pressure-level import. Files may
be split by variable/time if their grids match. Supported GRIB is regular
latitude/longitude pressure-level/surface data. Reduced Gaussian grids,
spectral coefficients and arbitrary ECMWF model levels need regridding or
conversion for this pressure-level importer. Your ERA5 viewer L137 files use
the separate direct archive reader described above. NetCDF requires U/V/T plus z or gh and one-dimensional lat/lon and
pressure coordinates; valid_time, old time, forecast time+step and mixed
expver dimensions are supported.

## Flight settings and scientific assumptions

- All timestamps are UTC; inputs must contain a timezone in the API or CLI.
- All launch/burst/landing altitudes are **metres above mean sea level**, not
  height above the launch site.
- Longitudes in either -180…180 or 0…360 are accepted.
- Horizontal drift follows earth-relative U/V on a sphere, using midpoint
  integration. There is no balloon inertia or horizontal drag lag.
- Ascent rate is prescribed, not calculated from gas mass, balloon expansion
  or drag. Burst is a prescribed altitude.
- Constant descent uses the specified rate at every altitude. Density descent
  uses rate × sqrt(ISA sea-level density / local dry density), appropriate as
  a simple terminal-speed approximation. It is not a parachute deployment model.
- WRF vertical velocity and ERA5 pressure velocity converted to m/s are optional.
  They can alter total vertical speed. If they prevent ascent/descent, the flight
  stops with an explanatory error. The default leaves this disabled.
- Atmospheric columns are interpolated in actual geometric altitude; pressure
  is interpolated logarithmically within each column. Space and time are then
  blended linearly. WRF horizontal interpolation uses native-grid triangulation.
- Pressure-level heights come from z/g or gh and a spherical geopotential-to-
  geometric conversion. WRF uses the usual (PH+PHB)/9.81 mass-level height.
- Sub-ground pressure levels are discarded. Surface winds are blended with the
  lowest valid levels where available; below the lowest represented wind the
  nearest wind is held constant.
- Terrain landing uses the imported model terrain, which may differ from actual
  local ground. Fixed landing uses your specified AMSL altitude.
- The integrator resolves burst and ground crossings inside the last time step.
  It refuses to continue outside the weather time window, horizontal domain or
  vertical top.
- Sensitivity runs perturb ascent/descent/burst parameters and use a constant
  per-flight U/V bias. They are reproducible by seed, but **not** an ECMWF ensemble
  or calibrated landing probability.

Treat this as an experimental trajectory model. Compare results with known
flights before making operational decisions. Small-scale winds, terrain
resolution, balloon/parachute behavior and initial conditions can materially
change a landing location.

## Exports, scripts and API

The browser exports CSV, GeoJSON, KML and complete JSON. CSV includes nominal
and successful sensitivity trajectories; GeoJSON also includes sensitivity
landing points. KML exports the nominal trajectory and event markers.

~~~bash
balloon-predictor predict examples/flight-demo.yml --output data/results/demo-flight
~~~

The output prefix produces .json, .csv, .geojson and .kml files. API predictions
are also saved in data/results/ and remain exportable after a server restart.

API reference: **http://localhost:8000/docs**

| Endpoint | Purpose |
|---|---|
| GET /api/weather | Available datasets with coverage/provenance |
| POST /api/weather/download | Start a download job |
| POST /api/predictions | Start an asynchronous prediction job |
| GET /api/jobs/{id} | Poll queued/running/completed/failed state |
| POST /predict | Synchronous prediction for scripts |
| GET /api/v1/ | Tawhiri-style basic standard_profile query |
| GET /api/predictions/{id}/export/{format} | csv, geojson, kml or json |

The compatibility endpoint returns the familiar prediction/stage/trajectory
shape and accepts the basic launch/rate fields; this is not a full replacement
for every Tawhiri endpoint, profile or error schema.

Example:

~~~bash
curl -X POST http://localhost:8000/predict \
  -H "Content-Type: application/json" \
  -d '{"dataset":"demo","launch_datetime":"2024-01-15T12:00:00Z","launch_latitude":52.817208,"launch_longitude":355.875,"launch_altitude":100,"ascent_rate":2,"burst_altitude":25000,"descent_rate":5}'
~~~

## Docker

Docker Desktop or Docker Engine with Compose is optional:

~~~bash
docker compose up --build
~~~

The default Compose setup runs the demo/IFS and persists data in a named volume.
For ERA5, copy .cdsapirc.example to .cdsapirc, add your token, and uncomment the
credential mount in compose.yml. For WRF, enable the raw-files and config mounts
described there, then:

~~~bash
docker compose exec predictor balloon-predictor import-config
~~~

The container's raw directory is read-only; normalized weather is written to the
named data volume. To run CLI examples, use docker compose exec predictor before
the corresponding balloon-predictor command.

The server is a local single-user application with no built-in authentication.
Compose binds it to localhost. Use a protected reverse proxy if you later host
it elsewhere, and keep the default single API process so jobs/caches agree.

## Folder guide

| Path | Purpose |
| --- | --- |
| `server.py`, `start.sh`, `start.ps1` | Direct launch and environment setup |
| `balloon_predictor/` | API, integrator, weather readers and exporters |
| `frontend/` | Local map, launch controls and SVG sounding plots |
| `config.yml`, `.env.example` | Shared defaults and optional overrides |
| `examples/` | Single flights, series and weather download requests |
| `docs/` | Science, architecture, validation, WRF and Git guides |
| `tests/`, `scripts/` | Backend checks, fixture generator and UI checks |

## Development and verification

~~~bash
python -m pip install -e ".[weather,dev]"
python -m pytest
~~~

Tests generate native GRIB, pressure-level NetCDF and WRF fixtures, check wind
rotation/height/time conventions, compare drift with an analytical solution,
exercise coverage errors and event landing, and check API/CLI/export behavior.
Remote client tests use mocked downloads. Real CDS requests require your token,
and no authenticated CDS download was performed while creating this bundle.

See [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) for common issues and
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for extension points.
The completed checks and their limits are recorded in
[docs/VALIDATION.md](docs/VALIDATION.md).

Optional frontend DOM check (requires Node 20+, only for development):

~~~bash
npm install
npm run test:frontend
~~~

This uses JSDOM and the real local API; it does not render a visual browser.
Node/npm are not needed for normal application use.

A real-browser regression is included for running on a machine with Chromium:

```bash
npx playwright install chromium
npm run test:browser
```

Set `BP_TEST_PYTHON` to your installed Python environment if it is outside the
project `.venv`. The script exercises native sources, responsive layouts,
launch tabs, series, soundings and exports; this check could not run in the
release execution environment. See docs/VALIDATION.md.

The original v1.0 reference list (retained). Additional v1.1 equation and format
references, checked for this release, are in [SCIENCE.md](SCIENCE.md):

- [CDS authentication](https://cds.climate.copernicus.eu/how-to-api)
- [ERA5 data documentation and ERA5T releases](https://confluence.ecmwf.int/spaces/CKB/pages/76414402/ERA5+data+documentation)
- [ERA5 retrieval](https://confluence.ecmwf.int/spaces/CKB/pages/129135000/How+to+download+ERA5+from+the+Climate+Data+Store+CDS)
- [ECMWF open data](https://www.ecmwf.int/en/forecasts/datasets/open-data)
- [ECMWF Python client](https://github.com/ecmwf/ecmwf-opendata)
- [WRF output variables](https://www2.mmm.ucar.edu/wrf/site/users_guide/output_variables.html)
- [WRF wind rotation](https://forum.mmm.ucar.edu/threads/converting-model-grid-relative-wind-to-earth-relative-wind.179/)

This is an original implementation, inspired by the SondeHub/CUSF workflow.
It is not an identical SondeHub clone and does not use its backend. See LICENSE
and THIRD_PARTY_NOTICES.md.
