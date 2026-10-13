# Changelog

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
