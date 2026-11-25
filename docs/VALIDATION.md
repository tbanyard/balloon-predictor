# Validation for v1.6

Local Python 3.12 checks pass **137 tests** with two Unix-socket tests skipped
because this workspace prohibits AF_UNIX binding. Both remain mandatory in
GitHub's Linux checks. The prediction engine and authentication behavior are
covered by the retained suite; no weather-provider account was used here.

New Node regressions exercise the actual SVG renderer's 45-degree transform,
logarithmic pressure, matching pressure/height axes, bracketing-only model-height
interpolation, initial trace visibility, barb units/direction, independent and
linked zoom, dragging, pinch/reset and listener cleanup. The moist guide remains
checked against MetPy's documented numerical example.

The window suite now covers 19 scenarios, including three-panel coordinated
tiling, both shared separators, locking, one remaining panel and persisted
layouts. Chromium checks include the real map confirmation flows, model/run
selection and ordering, profile controls, pixel alignment and connection cards
at desktop and phone widths, along with the earlier data/export/login checks.

The kit's VALIDATION.json records APK compilation, release-script simulations
and any remaining native checks. Windows/macOS/Debian installation, Android
emulator execution and full Unix-socket broker tests must pass in GitHub Actions
before publication. No physical-device test is claimed from this workspace.

Earlier records follow as historical information.

---

# Validation for v1.5.3

Local Python 3.12 validation passes **131 tests**, with **two Unix-socket tests
skipped** because this execution workspace prohibits Unix-socket binding. Both
tests are mandatory in Linux CI (`BP_REQUIRE_UNIX_BROKER_TEST=1`); they exercise
the kernel peer-credential broker and the full browser/SSH/broker handoff.

The local sign-in regressions use real loopback SSH password authentication and
interactive verification codes. They check failure/retry, host-key rejection,
Origin checks, cookie attributes, cancellation, logout, account selection and
native-app reconnects. Only the final Unix-socket boundary is stubbed in the
tests that must run inside this restricted workspace.

The UI integration suites and real Chromium browser regressions pass, including
the login page at desktop/mobile widths, private workspaces, rescan, exports,
desktop local startup and the existing weather/panel workflows. User-facing
server defaults are 8005. Syntax and compilation checks also pass.

The kit's VALIDATION.json records APK build checks and prepare/release-script
simulation results. Windows/macOS/Debian installation and Android emulator
testing remain required in GitHub Actions before publication. This local record
does not claim those native workflows have already run for v1.5.3.

Previous validation records are retained below as historical information.

---

# Validation for v1.4

The v1.4 release passes **89 backend tests**. Its existing frontend DOM/API
workflow also passes. Additional v1.4 checks cover 13 window-interaction
scenarios and real NetCDF export/loading workflows against synthetic weather.
These use the actual shipped JS/API, not a duplicated demonstration UI.

See [V1.4.md](V1.4.md#verification-and-limits) for coverage, commands and limits.
Local Chromium/Firefox could not start in this runtime, and the remote browser
could not reach the local test server. Rendered desktop/Android/touch validation
is therefore not claimed. The Playwright script is updated and included for
local execution. PowerShell/Docker startup was not executed here.

The previous validation record is retained below as historical information.

---

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
