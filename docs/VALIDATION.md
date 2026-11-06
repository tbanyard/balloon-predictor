# Validation of BalloonPredictor v1.2

Release checks on Python 3.12, Linux, 5 November 2026.

## Completed

- 64 backend tests: all 43 v1.1 tests plus 21 v1.2 cases, including parameterised
  whole/fractional ascent-rate checks. Existing ERA5/WRF, physics, series, export,
  API, persistence and CLI coverage remains passing.
- A clean installation of the pinned requirements succeeded into an isolated
  directory. A dependency/version audit of its 44 distributions passed.
- Default CLI port is 8001. JavaScript, CSS and shell syntax checks passed.
- The actual frontend/Leaflet runs against the real local API in JSDOM. Checks
  cover numeric constraints, single/series flights, launch tabs, coordinated
  arrivals and selected NetCDF links, online GFS controls, window movement,
  locking/resizing/minimising/restoring, sidebar controls, served favicon,
  soundings, setup controls and existing exports.
- Coordinated plans reach the common AMSL altitude and UTC time, including
  refinement with atmospheric vertical wind, distinct launch altitudes, saved
  rates, work limits and per-balloon failures. Plans persist after API restart.
- Sonde export was compared with the actual attached reference: same variable
  order/names, dimension names, datatypes, variable attribute names, units,
  fill values, calendar and compression/chunking. Original global-attribute
  names/type kinds are retained; values and provenance describe a simulation.
  Sample counts vary with flight duration. No observation values or original
  contact/launch identity are bundled. Dry, humid, full-flight and dateline
  export cases passed; temporary HTTP exports are removed after transfer.
- Live ECMWF IFS 2026-11-05 00 UTC: downloaded pressure/surface fields at +12/+15 h
  and static terrain, then decoded a regional 14-level dataset with u/v, T,
  humidity, vertical motion and terrain. A separate live 500 hPa sample confirmed
  the returned field identities and physical units.
- Live GFS 2026-11-05 06 UTC: downloaded +9/+10/+11 h regional subsets, decoded
  41 pressure levels, sampled at 20 km, predicted a real-model ascent and exported
  its sonde NetCDF. Live checks exposed and corrected invalid ERA5-style pressure
  levels in the initial GFS request. The current request matches NOAA's inventory.
- Mocked provider tests exercise caching, actual GRIB decoding, cycle validation,
  GFS auto-cycle fallback, step boundaries, humidity retention and download limits.

## Limits

Chromium was downloaded but terminated with SIGTRAP before opening a page in
this environment. Full visual browser rendering, pointer gestures, responsive
layout and live map tiles therefore remain unverified. The DOM tests exercise
keyboard movement/resize and locks; a real-browser regression is included in
`scripts/browser-check.cjs` for an environment where Chromium can run.

No authenticated CDS request, real SkySightWRF-file benchmark, comparison against
observed flight tracks, Docker execution or PowerShell execution was performed.
WRF compatibility is covered with standard synthetic WRF-ARW files, including
native column reads, wind rotation and humidity. Decoded-column cache limits do
not include all horizontal geometry, job results or temporary export arrays.

Live retrieval proves the checked runs worked at build time, not that a provider
will always be available. Forecast accuracy and calibrated uncertainty were not
validated. See SCIENCE.md and docs/V1.2.md for model and export assumptions.
