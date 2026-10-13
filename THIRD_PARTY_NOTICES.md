# Third-party notices

This is an independent implementation inspired by the public SondeHub/CUSF
balloon-prediction workflow. No SondeHub frontend or Tawhiri source is included.

Leaflet 1.9.4 is redistributed under its BSD 2-Clause license. The full license
is in frontend/vendor/leaflet/LICENSE. Map tiles, when online, come from
OpenStreetMap and display their required attribution. Review the
[OpenStreetMap tile usage policy](https://operations.osmfoundation.org/policies/tiles/)
before hosting a public or heavily used instance.

ERA5 and ERA5T data come from the Copernicus Climate Change Service / ECMWF.
Accept the relevant CDS dataset terms, retain metadata and cite the dataset
when publishing results. ECMWF open data requires its applicable attribution.
Weather data is downloaded by the user and is not bundled in this distribution.

Python packages retain their own licenses. See the respective distributions
for license texts; they are installed separately.

## ERA5 viewer shared reader and coefficients

The internal `balloon_predictor/weather/era5_native` metadata reader, L137
physics and coefficients are adapted from the attached ERA5 viewer v1.2 project
under its MIT licence. The predictor adds scalar packed-value handling and
column-based trajectory sampling. The standalone fixture generator is likewise
adapted from the viewer's synthetic test generator. ECMWF is the source of the
L137 half-level coefficient table; the reference is documented in SCIENCE.md.

## Natural Earth coastline

`frontend/coastlines.js` is the bundled Natural Earth 1:50m coastline from the
attached ERA5 viewer. Natural Earth data is public domain:
https://www.naturalearthdata.com/about/terms-of-use/

Thermodynamic guide equations are described in SCIENCE.md. The SVG plotting
implementation is local JavaScript; it does not bundle MetPy code or assets.
