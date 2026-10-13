# Architecture

| Component | Responsibility |
| --- | --- |
| `server.py`, `cli.py` | Direct entry point, CLI imports, flights and series |
| `settings.py` | YAML/env/CLI data roots and resource settings |
| `api.py` | Local FastAPI server, background jobs, result persistence |
| `store.py` | Normalized weather datasets and native archive routing |
| `weather/native.py` | Direct ERA5/WRF indexing, column reads and bounded cache |
| `weather/era5_native/` | ERA5 viewer metadata handling and L137 equations |
| `weather/interpolate.py` | Grid geometry and space/height/time sampling |
| `predictor.py` | Midpoint flight integration, endpoint events and launch series |
| `profiles.py` | Ascent observations and humidity-derived dew point |
| `exports.py` | Single-flight / whole-series CSV, GeoJSON, KML and JSON |
| `frontend/app.js` | Independent launch setups, tabs, schedules, map and jobs |
| `frontend/plots.js` | Skew-T and separate u/v SVG charts |
| `frontend/sounding.*` | Independent sounding window and plot downloads |

ERA5 viewer and BalloonPredictor are standalone sibling applications. The
predictor bundles its own copy of the shared MIT ERA5 metadata/equation code;
it does not require a running viewer or import from a neighbouring installation.
Neither application modifies the shared input weather files.

Native archive scans load timestamps/headers and the optional ERA5 2D terrain
field. Trajectory reads load only native columns. `cache_mb` bounds decoded
column values, with additional memory for coordinates, WRF triangulation and
results. All NetCDF/HDF5 access uses one process-wide reentrant lock. The local
server uses one process; jobs run in a small thread pool.

The original download/import path uses normalized NetCDF:

| Field | Dimensions | Units |
| --- | --- | --- |
| `u`, `v`, optional `w` | time, level, y, x | m/s |
| `height` | time, level, y, x | m AMSL |
| `temperature` | time, level, y, x | K |
| `pressure` | time, level, y, x | Pa |
| optional `specific_humidity` | time, level, y, x | kg/kg |
| `latitude`, `longitude` | y, x | degrees |
| optional `terrain` | y, x | m AMSL |
| surface wind/thermodynamic fields | time, y, x | SI |

Normalized imports retain their `max_weather_mb` materialization guard. Native
reads do not use that whole-volume limit. Both implement `sample(lat, lon,
altitude, datetime)` returning u, v, T, p, w and optional specific humidity,
and `terrain(lat, lon)` returning AMSL terrain.

`POST /api/predictions` runs a single `PredictionRequest`;
`POST /api/predictions/series` runs a bounded `BatchPredictionRequest`. Job IDs
are persisted in the server data directory and can be retrieved after restart.
Series results include an explicit success/failure for every launch. Browser
launch setups are independent of results and stored in localStorage, with JSON
export/import for portability. Changes invalidate only the current point's
calculated results; job completion checks revisions to reject stale display.

Soundings along the ascent are persisted with each prediction, so those and
flight exports survive server restart. Recomputing the alternate fixed launch
column requires the original weather to remain available. The local UI uses
bundled Leaflet and Natural Earth coastlines; optional OSM tiles provide street
map detail when internet access is available. There is no frontend build step.
