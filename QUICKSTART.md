# BalloonPredictor v1.5.1

Extract the source ZIP anywhere you can write. Its `balloon-predictor-1.5.1` folder can sit
beside v1.3. Python 3.12 is recommended. To keep concurrently running versions
independent, use a separate Conda environment:

```bash
conda create -n balloon-v14 -c conda-forge python=3.12 pip
conda activate balloon-v14
cd balloon-predictor-1.5.1
python -m pip install -r requirements.lock.txt
python -m pip check
bash start.sh --port 8001
```

Install once in that environment. For subsequent launches:

```bash
conda activate balloon-v14
cd balloon-predictor-1.5.1
bash start.sh --port 8001
```

Open **http://127.0.0.1:8001**. Use another free port if an existing server is
already on 8001, or stop that server first. A server on port 8000 can keep
running in its own environment. Avoid changing packages in an environment used
by another running application.

The server dependency lock is unchanged from v1.4. A compatible existing environment
can be used; matching packages do not need reinstalling. Without an activated
environment, `bash start.sh` manages a per-user environment automatically. That
automatic environment is shared across predictor versions, so use explicitly
separate environments when keeping different versions running independently.

Launch setups, weather storage paths and v1.3 window geometry are retained.
Linux's default launcher data directory remains
`~/.local/share/balloon-predictor` (or `$XDG_DATA_HOME/balloon-predictor`). The
weather manager shows the actual server paths. Different environments do not
isolate this data folder: use `BP_DATA_DIR` for separate data collections if
wanted. Do not clear data while v1.2 or older processes are using it.

For a remote server, forward the port from your computer:

```bash
ssh -L 8001:127.0.0.1:8001 tim@YOUR_SERVER
```

## Weather and terrain

Select an existing ERA5 day, WRF run, saved forecast or Demo. To download an
operational forecast, choose **Online · ECMWF operational IFS…** or
**Online · NOAA GFS…**, or open **Manage weather**. Check the area and UTC window,
covering the entire flight or earliest coordinated launch through arrival.
Choose a recent cycle or leave it blank for the latest available run. IFS/GFS
need no API key; ERA5/ERA5T require CDS credentials.

For coarse model ground near a coast or valley, open **Ground & model mismatch**.
Enter a known site elevation or select a physical DEM. **Check ground** compares
the real site with model orography without moving the launch altitude. The
near-surface option lets you either estimate missing air below the lowest usable
model level or stop there. The approximation is explained and recorded.

Manage weather → **Physical high-resolution terrain** can download a regional
GLO-30 tile at your launch point or use local GeoTIFF DEM/DTM files. Add adjacent
tiles when needed for DEM landings. A known launch elevation and the selected
landing surface are independent. Landing/descent settings are disabled for
ascent-only flights and coordinated arrivals.

## Panels, progress and downloads

The unnumbered panels are **Flight Map**, **Flight Details** (metrics, altitude
history and downloads), and **Flight Profile** (Skew-T and wind profiles).
**‹ Controls** is inside Flight Setup; hiding it exposes **› Controls** beside
Trajectory. On phones this opens a left drawer. The orange balloon beside job
status animates while calculating, including when controls are hidden.

After a prediction, Flight Details contains two clearly separated NetCDF cards.
Each has its own options, progress, action and save link. Model boxes may include
one flight plus sensitivity tracks or a whole plan; sondes always use only the
selected flight. Both begin downloading when ready and leave a reusable Save
link. Generated exports remain until you clear saved predictions/exports.

Use **Arrange ▾** for halves/quarters or drag an unlocked window to a workspace
edge and release over its orange preview. Drag away to float again. Nearby
edges gently align; pull farther to release, or hold Alt for free movement.
**+** maximises, the two-box icon **Unmaximise** restores, and **−** minimises.
Toolbar **Map / Details / Profile** buttons toggle panel visibility. Reset layout
restores the default Map/Details stack and its adjustable split.

## Coordinate balloons

Open **Launch schedule & coordination**, choose **Reach altitude together**,
then set a common target altitude/time and ascent rates such as `5, 4, 3, 2`.
Calculate to get launch times (slower balloons first), arrival positions and a
CSV timetable. This aligns altitude/time; it does not guarantee a straight
geometric line or eliminate forecast uncertainty.

## More details

- [docs/V1.4.md](docs/V1.4.md): changes and verification limits.
- [docs/V1.3.md](docs/V1.3.md): model-box contents, terrain physics and cleanup.
- [docs/API-AND-ORIGINS.md](docs/API-AND-ORIGINS.md): API/OAS/GET/POST and Data origins.
- [docs/V1.5.md](docs/V1.5.md): supported installers and release notes.
