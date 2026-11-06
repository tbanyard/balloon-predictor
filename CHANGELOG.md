# Changelog

## 1.2.0

- Fixed number-field step bases: whole and fractional ascent/descent rates,
  target/burst altitudes and maximum flight durations validate correctly.
- Default port 8001 in the CLI, launchers, Docker, healthcheck and instructions.
- ECMWF operational and NOAA GFS online choices in Weather dataset, with
  regional/time selection, cached downloads and explicit model-cycle provenance.
- GFS uses its actual 41-level NOMADS inventory; IFS supports current forecast
  horizons and the May 2026 operational-stream change for 06/18 UTC runs.
- Pressure-level readers retain specific humidity, optional relative-humidity
  conversion and atmospheric vertical motion. Surface pressure masks subterranean
  levels. Missing near-surface humidity is not replaced with zero.
- Sonde NetCDF download for the selected flight, including a selected member of
  a launch series or coordinated plan; ascent-only or full-flight scopes.
- Data-free 22-variable schema matches the supplied NETCDF4 sonde structure;
  1 Hz model sampling, updated metadata, and missing observational perturbations.
- Coordinate different ascent rates at a common AMSL altitude and UTC time,
  including vertical-wind timing refinement, launch-order table and map guide.
- Independent draggable/resizable map, profile and sounding windows, lock/dock/
  minimise/reopen controls, remembered layouts, and keyboard movement/resizing.
- Collapsible sidebar with a persistent restore arrow, orange scrollbars and
  an explicit orange-balloon browser tab icon.
- Per-selection memory checks reject oversized downloads before retrieval.


## 1.1.0

- ERA5 is the initial browser dataset. Direct, packed L137 model-level NetCDF
  support matches ERA5 viewer v1.2, with `/data3/ERA5` and `era5_topo.nc` defaults.
- Linear time interpolation follows adjacent records across daily files;
  missing times, long gaps and lost spatial/vertical coverage are reported.
- Native WRF output is indexed from `/data1/tim/SkySightWRF`; choose individual
  runs/domains and read only required vertical columns from large files.
- Up to 20 saved launch points, flight profile tabs, map selection, browser
  persistence and portable JSON setup export/import.
- Repeated launches over an end-exclusive window, fractional-hour intervals,
  current/all launch points, multiple tracks and per-launch coverage failures.
- Ascent-only calculations resolve the exact target altitude. Full flights
  retain landing calculations and also expose burst endpoint comparisons.
- Sensitivity markers follow the selected landing or altitude endpoint.
- Model-derived ascent soundings and fixed launch columns: Skew-T/log-P
  temperature, optional dew point, separate u/v curves, side panel and popout.
- Sounding CSV/SVG/print exports; single-flight and whole-series
  JSON/CSV/GeoJSON/KML exports.
- Version badge next to BalloonPredictor, offline coastlines, per-user startup
  environments, personal configuration overrides, and GitHub Actions CI.
- Retained the original weather download dialog and cropped ERA5/IFS/WRF imports.

## 1.0.0

Initial local ERA5/ERA5T/IFS/WRF trajectory predictor, weather imports/downloads,
parameter sensitivity runs and flight exports.
