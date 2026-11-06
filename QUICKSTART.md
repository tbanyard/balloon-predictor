# BalloonPredictor v1.3

Extract the ZIP into `/data1/tim`. Its `balloon-predictor-v1.3` folder can sit
beside the existing v1.2 installation. Python 3.12+ is required.

```bash
cd /data1/tim/balloon-predictor-v1.3
bash start.sh
```

Open **http://127.0.0.1:8001**. Without an active environment, first-time startup
installs the pinned dependencies in a per-user environment. For an existing
Conda environment, install this release's dependencies first:

```bash
conda activate balloon-predictor
python -m pip install -r requirements.lock.txt
bash start.sh
```

Or create a new environment, from the extracted project directory:

```bash
conda create -n balloon-predictor-v13 -c conda-forge python=3.12 pip gh git
conda activate balloon-predictor-v13
python -m pip install -r requirements.lock.txt
bash start.sh
```

For a remote machine, forward the port from your own computer:

```bash
ssh -L 8001:127.0.0.1:8001 tim@YOUR_SERVER
```

Then open the same local URL. Override the port with `bash start.sh --port 8002`.

## Download an operational forecast

1. Under **Weather dataset**, select **Online · ECMWF operational IFS…** or
   **Online · NOAA GFS…**. This opens the retained weather manager.
2. Check the UTC window, area and dataset name. Enlarge the suggested area for
   long flights or multiple sites. Include the whole flight through landing,
   or the earliest coordinated launch through arrival. One download spans at
   most 72 hours.
3. Leave the model cycle blank for the latest available run, or specify a recent
   cycle. Click **Download & add dataset** and wait for completion.
4. The new dataset is selected automatically. Calculate flights within its
   displayed coverage; the model is sampled along each track in space and time.

ECMWF/GFS need no API key. Downloads are cached and successful datasets persist.
These are recent operational runs, not historical archives. CDS credentials
remain necessary for ERA5/ERA5T downloads.

## Coordinate balloons at one altitude and time

Open **Launch schedule & coordination → Reach altitude together**. Enter the
arrival UTC time, an AMSL target such as 20000 m, and rates such as `5, 4, 3, 2`.
Choose the current launch point or all saved points. Alternatively choose each
saved point's individual ascent rate. Click **Plan coordinated arrivals**.

The table lists launch times in order and predicted positions at arrival. Click
a balloon to inspect its ascent or download its sonde. The optional atmospheric
vertical wind is included when solving launch times. Flights stop at the common
altitude, within 0.25 s of the requested time. Failed coverage is listed per
balloon. Up to 80 balloons are allowed. The dotted endpoint connector is a
formation guide; equal time/altitude does not guarantee a straight spatial line.

## Download a simulated sonde

Select a completed flight and, for a series/plan, the individual balloon.
Click **Download sonde NetCDF** in the flight profile. Choose ascent only or
full flight with the adjacent selector. The export follows the supplied
22-variable NETCDF4 schema, with 1 Hz model samples and updated simulation
metadata. Missing humidity and unresolved observational perturbations use NaN.
See [docs/V1.2.md](docs/V1.2.md) for exact export semantics.

## Arrange the workspace

Drag an unlocked panel by its header; resize from its lower-right corner.
**Lock/Unlock** fixes/releases its geometry. Focus the header/resize handle and
use arrow keys (Shift for larger changes) for keyboard control. **Dock** restores
the default position. **−** minimises; reopen with **Map**, **Profile** or
**Sounding** in the toolbar. **Reset layout** restores all defaults.

The orange arrow hides/restores the sidebar. Docked map/profile panels adapt to
the freed space. Layout and locks are remembered. On narrow screens, panels
stack vertically and movement/resizing is disabled.

## Existing weather and flight features

ERA5 remains the initial dataset, reading `/data3/ERA5` and `era5_topo.nc` as
in ERA5Viewer. Native WRF files are indexed from `/data1/tim/SkySightWRF` without
loading entire volumes. Use **Manage weather → Rescan local data**. Change paths
in `config.yml`, or pass `bash start.sh --config /path/to/config.yml`.

Launch tabs, repeated schedules, sensitivity members, landing/burst comparisons,
Skew-T/u-v plots, setup import/export and JSON/CSV/GeoJSON/KML remain available.
Real WRF support still depends on the variables in [docs/WRF.md](docs/WRF.md).
See [docs/GIT.md](docs/GIT.md) for GitHub updates and the `v1.3.0` release tag.

## New v1.3 controls

Use **Ground & model mismatch** to enter the physical site elevation or select
high-resolution terrain. **Check ground** compares it with model orography
without changing the entered launch altitude. For realistic coastal landings,
select **Physical high-resolution DEM** as the landing surface and add tiles
covering the route. The default bridge approximation is labelled in results;
select Strict when you require fully resolved model air.

**Manage weather** shows the actual save directories and offers separate cleanup
buttons for raw cache, named datasets, and prediction/export files. It can also
download one public GLO-30 launch tile or use your own regional GeoTIFFs.

Beside the sonde export, **Download model box NetCDF** exports all available
levels and bracketing model times, with a ≥0.5° / 50 km margin. It has independent
full-flight/ascent and selected-flight/whole-plan choices. Writing runs as a job;
use the resulting save link to download it again if needed.

The header Controls button opens a left drawer on phones. Desktop panels have
maximise/restore, edge/corner resize and a docked Map/Profile split divider.

Details and limitations: [docs/V1.3.md](docs/V1.3.md).
