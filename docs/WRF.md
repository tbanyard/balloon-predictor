# WRF in BalloonPredictor v1.1

## Use large files directly

The default is `/data1/tim/SkySightWRF`. WRF-ARW NetCDF output does not need a
conversion or browser upload:

```text
/data1/tim/SkySightWRF/run-2024-01-15/wrfout_d01_2024-01-15_00:00:00
/data1/tim/SkySightWRF/run-2024-01-15/wrfout_d02_2024-01-15_00:00:00
/data1/tim/SkySightWRF/run-2024-01-16/wrfout_d02_2024-01-16_00:00:00
```

Open **Manage weather → Rescan local data** and select the desired domain/run.
The index reads timestamps and headers, then the selected reader loads horizontal
coordinates and only the vertical columns needed by a trajectory or sounding.
A large file, including a file around 10 GB, stays on disk. Compressed/chunked
NetCDF layout and local disk speed affect performance. Decoded columns have a
bounded LRU cache (`cache_mb`, default 256 MB); horizontal coordinates and
triangulation use additional RAM. This is not a claim of a 256 MB total process
limit or a measured benchmark on the user's specific 10 GB file.

The file scanner recursively finds `wrfout*` files, including extensionless
standard WRF names. It groups by parent folder, filename domain (or `GRID_ID`)
and `SIMULATION_START_DATE` (falling back to `START_DATE`). Keep each forecast
run in a separate folder, especially when those run attributes are absent.
Overlapping duplicate timestamps within one group are rejected rather than
mixing forecasts. Different nests remain distinct choices.

Only dates/times actually present are available. Launch times may lie between
adjacent timestamps, but every point through the selected flight endpoint must
be covered. No historical forecast is fabricated. A configurable maximum gap
(default 6 h) stops interpolation through missing days. The last record can be
sampled exactly; a flight continuing beyond it fails coverage. Repeated-launch
series retain successful launches and list each unavailable launch.

## Required native variables

| Variable | Use |
| --- | --- |
| `Times`, or `XTIME` with CF `… since …` units | Actual timestamps |
| `XLAT`, `XLONG`, `HGT` | Mass-grid geography and terrain |
| `U`, `V` | Staggered grid-relative winds |
| `COSALPHA`, `SINALPHA` | Rotation to eastward/northward winds |
| `P`, `PB` | Total pressure = P + PB |
| `T` | Perturbation potential temperature; theta = T + 300 K |
| `PH`, `PHB` | Staggered geopotential; destagger (PH + PHB) / 9.81 |
| `W` (optional) | Atmospheric vertical wind; required only when requested |
| `U10`, `V10`, `T2`, `PSFC` (optional group) | Surface winds and thermodynamics |
| `QVAPOR` (optional) | Water-vapour mixing ratio; enables dew point |
| `Q2` (optional) | Surface water-vapour mixing ratio |

Winds are destaggered on mass levels and rotated with the native coefficients.
Temperature is `(T + 300) * ((P + PB) / 100000) ** (287 / 1004)`.
QVAPOR is mixing ratio `r`, so specific humidity is `q = r / (1 + r)`.
Dew point follows the sampled model humidity. Missing humidity produces no
moisture curve; it is never treated as a measured dry atmosphere.

The reader checks grids and terrain at accessed timestamps. **Moving nests,
mixed grids, non-ARW layouts and omitted rotation coefficients are unsupported.**
No WRF initial/boundary conditions are generated. No winds above the represented
model top are invented.

The WRF header/text mentioned in the previous brief was absent from the two
attachments. This release was validated with standard synthetic WRF-ARW output,
not the user's actual SkySightWRF file. To check that file on your machine:

```bash
ncdump -h '/data1/tim/SkySightWRF/run-folder/wrfout_d02_DATE_TIME'
python -m balloon_predictor list
python -m balloon_predictor inspect wrf-ID_FROM_LIST
```

## Keep the existing cropped importer

The original **Use your WRF output or existing weather files** workflow remains:

```bash
python -m balloon_predictor import-wrf --name my-wrf \
  --files '/data1/tim/SkySightWRF/run-folder/wrfout_d02_*' \
  --area 60 -12 48 5 \
  --start 2024-01-15T00:00:00Z --end 2024-01-16T00:00:00Z
```

This converter materializes the selected domain/time window and writes a
normalized local dataset. `max_weather_mb` applies to this path. For very large
WRF output, use the new direct run selector instead. Time selections are
inclusive; retain records bracketing the whole flight. Configured imports in
`config.yml` and `import-config` still work.

References:

- [NCAR WRF output variables](https://www2.mmm.ucar.edu/wrf/site/users_guide/output_variables.html)
- [NCAR WRF output documentation](https://www2.mmm.ucar.edu/wrf/site/documentation/users_guide/output.html)
