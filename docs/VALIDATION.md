# Validation of BalloonPredictor v1.1

Checked with Python 3.12 on Linux during this release build.

## Completed checks

- **43 backend tests passed**, including the original 32 and 11 v1.1 tests.
- A JSDOM integration run loads the actual frontend and Leaflet against the
  real local Python API, with no JavaScript errors. It covers full flights,
  exact altitude endpoints, saved launch-point tabs, hourly series across two
  points, launch selection, ascent/fixed-column sounding modes, separate u/v
  curves, rescan, download controls and export links.
- Approximate saturated guide temperatures match MetPy's published
  moist-lapse example within 0.06 °C for the checked levels.
- Installation from the pinned requirements succeeded in a fresh virtual
  environment after retrying an interrupted wheel download.
- `pip check` reports no broken dependency requirements in that environment.
- Frontend JavaScript and the shell launcher pass syntax checks.
- Direct `server.py --help` exposes personal config, data roots, cache and port.

New backend format/science coverage includes packed/reordered/reversed ERA5
model-level variables, matching packed terrain, geometric height conversion,
interpolation across daily midnight boundaries, optional humidity and missing
terrain rejection. Native WRF checks include scalar column reads, wind
rotation/destaggering, QVAPOR mixing-ratio conversion, time interpolation,
separate run/domain groups, moving-grid rejection and duplicate timestamps.

Trajectory tests cover exact ascent targets, sensitivity endpoints and exports,
end-exclusive schedules, multiple sites, per-launch failures when weather ends
before the flight endpoint, and continued successes elsewhere. API tests cover
asynchronous series, sounding modes/CSV, all series exports and persisted jobs
across server restart. Existing analytical drift, terrain landing, dry-density
descent, global longitude seam, curvilinear grids, GRIB/pressure-level imports,
expver merging, CLI and mocked download checks remain passing.

## Limits

The user supplied the predictor and ERA5 viewer ZIPs. The WRF header/text from
the original brief was not attached. Native WRF compatibility was checked with
standard synthetic WRF-ARW fixtures, not the user's actual SkySightWRF files.
No benchmark on the real approximately 10 GB file was possible. Native reads
retain weather volumes on disk and bound decoded columns; horizontal geometry
and results use additional RAM outside that byte budget.

Chromium execution was unavailable in the build environment, so desktop/mobile
layout, full browser rendering and live background map tiles were not visually
verified. `scripts/browser-check.cjs` is included to run that regression on a
machine with Playwright Chromium installed. The UI was verified through syntax,
asset serving and the expanded DOM/API integration test.

No authenticated CDS request or live operational IFS forecast retrieval was
performed. Download client tests use mocked responses backed by real generated
GRIB fixtures. Docker and PowerShell launchers were not executed on their target
platforms. No validation against observed balloon flights was performed.

These checks establish behavior with controlled inputs, not forecast accuracy
or calibrated uncertainty. See SCIENCE.md for sampling and physical assumptions.
