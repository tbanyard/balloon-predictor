# Changelog

## 1.5.0

- Local, web and SSH desktop connection modes, animated startup, and Debian package.
- Android Server settings keep the live flight and tunnel when returning.
- Desktop data folder control, per-user defaults, and discoverable processing logs.
- Archive dates follow launches; floating Profile and inactive descent controls corrected.
- Rounded sensitivity regions for multiple endpoint samples.

## v1.4.0 — 2026-11-06

- Remove section numbers; rename the metrics/download panel Flight Details and
  the Skew-T/wind panel Flight Profile. Use Data origins in the interface.
- Put the sidebar hide button inside Flight Setup and the sticky reopen tab
  beside Trajectory. Retain the responsive mobile drawer and status visibility.
- Explain near-surface estimation plainly, use +/− disclosures, and disable
  landing settings for ascent-only/coordinated flights. Ignore irrelevant fixed
  landing/target altitude ordering in ascent-only request validation.
- Animate a small theme-orange vector balloon during calculations, with reduced
  motion support and cleanup on success or failure.
- Separate model-box and sonde export cards, options, progress and persistent
  save links. Both auto-download when ready; async state stays with its original
  flight/scope. Add background sonde exports while keeping the old direct URL.
- Replace Dock with explicit Arrange placements; add drag-to-edge halves,
  quarters and maximise previews, gentle move/resize alignment with release, and
  Alt for free movement. Use a two-box Unmaximise symbol and correct tooltip.
- Preserve v1.3 layouts/setups, physics and data workflows. Dependencies unchanged.
- Validation: 89 backend tests, full frontend DOM/API workflow, 13 window
  interaction scenarios and dedicated export/loading workflow checks pass.
  Actual rendered desktop/Android checks remain unverified in this runtime.


## v1.3.0 — 2026-11-05

- Separate physical launch ground, DEM landing and model orography; preserve
  actual launch altitude. Add labelled hydrostatic/lapse near-surface bridge
  and strict mode, with approximation metadata in predictions and sonde files.
- Read regional GeoTIFF DEM/DTM tiles; download public GLO-30 launch tiles.
- Export native-grid model boxes for one flight or complete plans, including
  sensitivity tracks, native time brackets, all available levels, source receipts,
  latitude-aware margins, explicit clipping, background progress and mixed-source ZIPs.
- Fix operational forecasts inheriting the ERA5-only grid choice and invalid
  second-bearing suggested download times. Display real server-side save paths.
- Add separate dataset/cache/result cleanup with dependency counts and shared
  leases protecting active jobs/streams across v1.3 processes.
- Add panel maximise/restore, eight resize handles, docked split divider and
  toolbar minimise/restore toggles. Put the sidebar toggle in the header; use
  a real mobile drawer and one main panel at a time.
- Keep launch setups and reset old window geometry; retain port 8001.
- Verification: 82 backend tests and expanded frontend DOM workflow checks pass;
  real GLO-30 download and real cached IFS/GFS sonde/model-box workflows pass.
  Rendered desktop/Android appearance remains unverified in this build runtime.


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
