# Flight sampling and synthetic soundings

## Weather and height

ERA5 viewer-compatible input uses actual L137 model level numbers, T/U/V,
lnsp (or sp), and optional q. The half-level coefficients are bundled in
`balloon_predictor/weather/era5_native/l137.csv`, shared with the attached
ERA5 viewer v1.2 package. Surface pressure is `exp(lnsp)`; half pressure is
`a + b * sp`; full pressure is the mean of adjacent half levels. Hydrostatic
integration starts at model level 137 and uses contiguous levels upward,
with `T_virtual = T * (1 + 0.609133*q)`. Missing lower layers prevent height
reconstruction above them. When q is absent, the dry-height approximation is
labelled; this does not mean humidity was measured as zero.

Embedded surface geopotential, or a matching archive-root `era5_topo.nc`,
sets the sea-level reference. Topography coordinates must match the weather
grid after ordering and longitude normalization. There is no guessed terrain.
Geopotential height H is converted to geometric altitude `R*H/(R-H)` for the
predictor's altitude and geographical integration. ERA5 viewer itself displays
geopotential height, so its numeric height labels can differ slightly aloft.

Native WRF sampling follows the variables and equations in docs/WRF.md.
Mass-grid winds are destaggered and rotated; WRF mass-level heights use the
adjacent staggered geopotential values divided by 9.81, following the previous
predictor's native WRF convention. WRF and ERA5 heights therefore retain their
native derivation rather than being assumed identical. WRF QVAPOR is mixing
ratio r; the sounding uses specific humidity `q = r/(1+r)`.

## Interpolation and trajectory

At each contributing horizontal node, winds and temperature are interpolated
linearly in altitude; pressure is interpolated in log(p). The node samples are
then combined horizontally and linearly in UTC time. Regular grids use
bilinear spatial weights, including the global longitude seam. Fixed WRF
curvilinear grids use barycentric triangles within adjacent native grid cells.
The native readers reject missing layers inside a required vertical bracket.
Below the lowest represented wind, the nearest wind is held constant; complete
WRF surface fields provide a 10 m anchor. There is no extrapolation above the
model top or outside spatial/time coverage.

Time interpolation follows adjacent daily ERA5 files. A timestamp can be
sampled exactly, including the last record, but an endpoint after the final
record is invalid. Native time gaps larger than `max_time_gap_hours` (6 h by
default) are not bridged. Repeated launches check the entire trajectory; a
covered launch time alone does not guarantee a covered flight.

Midpoint integration advances latitude, longitude and geometric altitude.
Ascent speed is fixed unless atmospheric vertical wind is explicitly enabled.
The density parachute model uses dry density `p/(R_d*T)` and an ISA sea-level
reference for the entered descent speed. This remains a prescribed balloon
profile; no gas expansion, heating, drag or burst mechanics are simulated.
Ground/burst/target crossings are resolved within the time step. Ascent-only
mode stops at exactly the requested altitude and has no landing result. Full
mode includes descent and exposes both burst and landing endpoint locations.

Sensitivity members perturb flight parameters and a constant u/v wind bias.
They are not a meteorological forecast ensemble or calibrated probability.
Altitude endpoints use each member's perturbed burst/target altitude, so a
nonzero burst sigma means the endpoints are at different altitudes. Set burst
sigma to zero when comparing spread at one exact common target height.

## Soundings and plots

The default synthetic sounding samples the predicted ascent at up to 180
actual positions/times including launch and burst/target. The alternate launch
column samples those altitudes at a fixed launch position and UTC time. Both
stop at the height reached by the flight. The data is model-derived and omits
instrument lag, balloon wake, radiation and wetting effects.

For specific humidity q and pressure p, vapour pressure is
`e = p*q/(0.622 + 0.378*q)`. Dew point (°C) uses the liquid-water Magnus inverse:
`L = log(e/611.2)`, `Td = 243.5*L/(17.67-L)`. Humidity is available only when all
contributing samples provide valid humidity. Missing/zero q yields no finite
dew point, not an invented moisture observation. Supersaturated model data is
not silently clipped to temperature. The approximation is not an ice frost
point conversion and is less accurate at extreme stratospheric temperatures.

The Skew-T uses log pressure and a skewed Celsius temperature axis. Background
dry adiabats use Poisson's equation. Liquid-saturated pseudo-adiabats integrate
`dT/dln(p) = (R_d*T + L_v*r_s)/(C_pd + L_v²*r_s*epsilon/(R_d*T²))` with RK4,
constant latent heat and Magnus saturation; these are approximate visual guides,
not parcel/CAPE diagnostics. Pale green guides show fixed saturation mixing
ratios. u and v use separate lines versus sampled altitude in km AMSL, with
positive values eastward and northward respectively. Exported sounding CSV
keeps the numeric data independent of the SVG's plotting range.

## Coordinated arrivals and sonde files (v1.2)

Operational IFS/GFS pressure fields use the same column-wise spatial/altitude/
UTC sampling as other pressure imports. Missing optional vertical motion is
allowed when it is disabled; requesting it where it is absent causes a coverage
error. Humidity is preserved when available, with no synthetic surface zero.

Coordinated plans first estimate launch UTC from altitude gain and ascent rate,
then simulate each ascent. Optional atmospheric vertical wind triggers iterative
timing correction. The accepted arrival-time residual is at most 0.25 s; failed
coverage/nonconvergence is reported per balloon. Shared altitude/time does not
imply a straight line of positions. These are deterministic scenario calculations.

The sonde export samples the atmosphere along the interpolated predicted track
at 1 Hz. It preserves the supplied 22-variable file structure, while updating
metadata to identify a model simulation. Observational vertical perturbation/
noise fields are missing. Fractional terminal seconds are retained as exact-event
metadata rather than rounded into the integer-second time coordinate. See
[docs/V1.2.md](docs/V1.2.md) for the format and physical limitations.

## References

- [ECMWF model-level pressure, geopotential and height](https://confluence.ecmwf.int/spaces/CKB/pages/158636068/ERA5+compute+pressure+and+geopotential+on+model+levels+geopotential+height+and+geometric+height)
- [ECMWF L137 coefficients](https://confluence.ecmwf.int/display/UDOC/L137+model+level+definitions)
- [NCAR WRF output variables](https://www2.mmm.ucar.edu/wrf/site/users_guide/output_variables.html)
- [MetPy moist-lapse differential equation](https://unidata.github.io/MetPy/latest/api/generated/metpy.calc.moist_lapse.html)

## Physical ground and below-model air (v1.3)

Physical DEM / known launch ground is independent of forecast terrain. It is
never subtracted from model levels or used to shift the launch. DEM landing
samples orthometric metres in a separate regional raster. Horizontal CRS
conversion does not convert vertical datums or GPS ellipsoid heights.

Below a contributing column's lowest valid air anchor, bridge mode holds wind,
vertical wind and specific humidity at that anchor. With depth `d = z0 - z > 0`,
`T = T0 + L d` and `p = p0 (T / T0) ** (g / (Rd L))`, using `L = 0.0065 K/m`,
`g = 9.80665 m/s²`, and `Rd = 287.05 J/(kg K)`. This is a lower-atmosphere
approximation, capped at 3 km and identified in result/sonde metadata. Each
native column is processed before space/time blending. Strict mode refuses
unresolved air. Hydrostatic pressure and density now vary with the true launch
height instead of being silently clamped to a coarser model ground.

A wind/surface anchor may combine 10 m wind, 2 m temperature/humidity and surface
pressure as in the existing reader; it is not a fully resolved ten-metre
thermodynamic observation. Holding q does not model changing saturation or
condensation. A higher-resolution DEM improves ground identification, not the
underlying forecast wind resolution. A better-resolved weather model is needed
for terrain-channelled coastal/valley winds.

Model-box exports retain model terrain and the available native levels/time
brackets. They export the derived fields used by the trajectory adapters, not
simulated observation fields or vertically shifted air. See docs/V1.3.md for
provenance, clipping and the exact export scope.
