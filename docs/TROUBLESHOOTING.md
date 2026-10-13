# Troubleshooting

**The browser shows an API connection error**

Run the server from the extracted directory, then open http://localhost:8000.
Opening frontend/index.html directly does not start the Python backend.
If port 8000 is busy: balloon-predictor serve --port 8001, then visit port 8001.

**Missing Python modules after an interrupted installation**

Activate the environment you use for the predictor (the default Unix launcher
uses `~/.cache/balloon-predictor/venv`, or your chosen activated environment)
and rerun:

~~~bash
python -m pip install -r requirements.lock.txt
python -m pip install --no-deps -e .
~~~

**CDS credentials cannot be loaded / a download fails**

Check ~/.cdsapirc has the correct current API URL and your personal token.
Accept terms for both ERA5 hourly datasets on the CDS website. Check that the
requested dates are available and retrospective. CDS may queue downloads.
The API deliberately does not display raw remote error bodies containing
potentially sensitive information; the server logs the exception type.

**ERA5 final rejects preliminary ERA5T**

Select ERA5T for the recent period, or choose an older finalized period.
The ERA5T option uses the same CDS hourly datasets and permits the available
release. The JSON provenance contains the observed GRIB expver values.
Select Refresh cached remote files when you need an updated preliminary/final
release, and use another dataset name or CLI --overwrite.

**IFS download cannot locate a forecast**

Use a current launch window and an available recent cycle. Historical cycles
are not a permanent archive. Check ECMWF service status. You can try another
mirror by setting IFS_SOURCE in .env. If requested fields are unavailable for a
cycle, select another cycle or import already downloaded GRIB/NetCDF.

**The map background is blank**

OSM tiles need internet. The bundled Natural Earth coastlines, local map
controls, flight lines, altitude/sounding charts and exports still work. Do not bulk-download tiles from the public tile server.

**Launch altitude is below ground**

Altitudes are AMSL. Click Use ground to set model ground + 1 m, or enter your
known site altitude. Model terrain may differ from surveyed ground.

**Flight leaves the weather domain/time window/model top**

Import a larger domain, a longer time window or a model with a higher top.
The application intentionally stops instead of inventing missing winds.
Slow ascents (for example 2 m/s to 25 km) need several hours of weather.
Sensitivity members may require extra spatial/time/height margins.

**No terrain in the dataset**

Select fixed landing altitude, or include ERA5/IFS surface fields on download.
WRF requires HGT. Existing pressure-only files need a separate matching surface
file if you want terrain-aware landing.

**WRF import says COSALPHA/SINALPHA are missing**

Include these standard rotation fields in your WRF output. Applying
unrotated grid winds as east/north winds can produce substantially wrong drift.

**WRF domain changes or duplicate times conflict**

Keep one fixed domain and one consistent forecast run per dataset. Use separate
names for each nest/run. Moving nests are not implemented.

**Only one weather timestamp is available**

Supply multiple output times bracketing launch through landing. A singleton
time slice is not treated as constant across a flight.

**Memory limit exceeded**

For large native WRF output, select the direct run under Weather dataset; it
reads columns rather than a converted volume. `cache_mb` bounds decoded data,
with additional RAM for geometry and results. For converted imports, use CLI
--area/--start/--end or config.yml cropping. Reduce the time span or
increase max_weather_mb if RAM allows. Remote downloads are limited to 72 h
per dataset to keep local datasets manageable.

**Docker can run the demo but cannot see CDS credentials or WRF files**

Add the mounts documented in compose.yml. The container has its own home
directory; it cannot automatically read your host ~/.cdsapirc.
Use docker compose exec predictor before CLI import commands.

**Need to remove downloaded weather/results**

With the server stopped, remove the matching name.nc and name.json in
data/weather. Raw requests are in data/cache; results are in data/results.
The application does not automatically delete your source files.

**Default ERA5 is selected but no files are available**

Set `era5_root` to the same directory as ERA5 viewer (default `/data3/ERA5`).
Use Manage weather → Rescan local data after adding files. The predictor never
silently substitutes demo weather. Choose Demo explicitly for a trial without
real weather, or use the retained download window.

**ERA5 asks for sea-level heights / era5_topo.nc**

The L137 height calculation needs a matching surface geopotential field. Use
the same archive-root `era5_topo.nc` as the viewer, or embedded surface z.
A fixed landing altitude cannot replace the missing atmospheric height reference.

**WRF runs show the wrong dates, duplicate timestamps or no files**

Check `wrf_root` (default `/data1/tim/SkySightWRF`), readable permissions and
standard `wrfout*` names. Keep each run/domain in a separate appropriate folder.
Rescan after new files are added. The date list comes from actual file times.

**A series has unavailable launches**

The launch list gives the reason for each failure. Weather must cover the
entire flight, including its endpoint and adjacent records for interpolation.
Try an available day, a shorter launch window, ascent-only mode where relevant,
or additional source files. Successful launches remain available for inspection.

**No dew-point curve is shown**

Your existing ERA5 files lack q. Temperature and u/v still plot. WRF QVAPOR or
ERA5 q enables moisture; missing humidity is not a zero-humidity measurement.

**Plots/data disappear after editing or browser reload**

Editing a point invalidates its computed tracks to avoid stale results.
Browser reload retains the saved setups, while result JSON persists on the
server and can be exported before reload. Save launch setups to move them
between browsers. Along-ascent soundings persist with the result; fixed-column
recalculation still requires the original weather files.
