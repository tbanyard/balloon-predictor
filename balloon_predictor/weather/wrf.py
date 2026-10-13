"""Native WRF-ARW wrfout conversion; no wrf-python/Fortran dependency."""
import json
from pathlib import Path

import numpy as np
import xarray as xr

from ..errors import PredictorError
from .schema import dataset_from_arrays


def destagger(values, axis):
    left, right = [slice(None)] * values.ndim, [slice(None)] * values.ndim
    left[axis], right[axis] = slice(None, -1), slice(1, None)
    return (values[tuple(left)] + values[tuple(right)]) / 2


def wrf_times(ds):
    if "Times" in ds:
        raw = np.asarray(ds.Times.values)
        strings = []
        for row in raw:
            if np.ndim(row) == 0:
                value = row.item()
                string = value.decode("ascii") if isinstance(value, bytes) else str(value)
            elif row.dtype.kind == "S":
                string = row.tobytes().decode("ascii")
            else:
                string = "".join(str(v) for v in row)
            strings.append(string.strip("\x00 ").replace("_", "T"))
        return np.array(strings, dtype="datetime64[ns]")
    if "XTIME" in ds:
        units = ds.XTIME.attrs.get("units", "")
        if "since" in units:
            base = np.datetime64(units.split("since", 1)[1].strip().replace("_", "T"), "ns")
            return base + (ds.XTIME.values * 60 * 1e9).astype("timedelta64[ns]")
    raise PredictorError("WRF output requires Times, or XTIME with 'minutes since ...' units.")


def read_wrf(paths, area=None, start=None, end=None, attrs=None):
    snapshots = []
    latitude = longitude = terrain = None
    warnings = ["WRF winds are destaggered and rotated with COSALPHA/SINALPHA. Heights use (PH+PHB)/9.81 on mass levels.",
                "WRF terrain is the model terrain; dry density uses pressure and temperature without moisture correction."]
    for path in paths:
        with xr.open_dataset(path, engine="netcdf4", decode_times=False) as ds:
            required = ("U", "V", "PH", "PHB", "P", "PB", "T", "XLAT", "XLONG", "HGT")
            missing = [name for name in required if name not in ds]
            if missing:
                raise PredictorError("WRF file is missing " + ", ".join(missing) + ". Supply native WRF-ARW wrfout files.")
            if "COSALPHA" not in ds or "SINALPHA" not in ds:
                raise PredictorError("WRF COSALPHA and SINALPHA are required to rotate winds; add them to your output.")
            times = wrf_times(ds)
            for ti, time in enumerate(times):
                if start is not None and time < start or end is not None and time > end:
                    continue
                def array(name):
                    da = ds[name]
                    if "Time" in da.dims:
                        da = da.isel(Time=ti)
                    return np.asarray(da.values, dtype="float32")
                lat, lon, hgt = array("XLAT"), array("XLONG"), array("HGT")
                lon = (lon + 180) % 360 - 180
                ny, nx = lat.shape
                ys, xs = slice(None), slice(None)
                if area:
                    n, w, s, e = area
                    mask = (lat >= s) & (lat <= n) & (lon >= w) & (lon <= e)
                    if not np.any(mask):
                        raise PredictorError("Requested area does not intersect the WRF domain.")
                    indices = np.argwhere(mask)
                    ys = slice(max(0, int(indices[:, 0].min()) - 1), min(ny, int(indices[:, 0].max()) + 2))
                    xs = slice(max(0, int(indices[:, 1].min()) - 1), min(nx, int(indices[:, 1].max()) + 2))
                # Crop staggered variables before loading their large 3D arrays.
                def cropped(name):
                    da = ds[name]
                    if "Time" in da.dims:
                        da = da.isel(Time=ti)
                    indexes = {}
                    for dim, base in (("south_north", ys), ("west_east", xs)):
                        if dim in da.dims:
                            indexes[dim] = base
                        if dim + "_stag" in da.dims and base.stop is not None:
                            indexes[dim + "_stag"] = slice(base.start, base.stop + 1)
                    return np.asarray(da.isel(indexes).values, dtype="float32")
                lat, lon, hgt = lat[ys, xs], lon[ys, xs], hgt[ys, xs]
                if latitude is not None:
                    if latitude.shape != lat.shape or not np.allclose(latitude, lat, atol=1e-5) or not np.allclose(longitude, lon, atol=1e-5):
                        raise PredictorError("WRF grids differ or the nest moves. Import one fixed domain per dataset; moving nests are unsupported.")
                    if not np.allclose(terrain, hgt, atol=0.01):
                        raise PredictorError("WRF terrain changes across files.")
                latitude, longitude, terrain = lat, lon, hgt
                u, v = destagger(cropped("U"), -1), destagger(cropped("V"), -2)
                ca, sa = cropped("COSALPHA"), cropped("SINALPHA")
                if not np.allclose(ca * ca + sa * sa, 1, atol=0.01):
                    raise PredictorError("WRF rotation coefficients are invalid.")
                pressure = cropped("P") + cropped("PB")
                temperature = (cropped("T") + 300) * (pressure / 100000) ** (287.0 / 1004.0)
                fields = {"u": u * ca - v * sa, "v": v * ca + u * sa,
                          "height": destagger((cropped("PH") + cropped("PHB")) / 9.81, 0),
                          "temperature": temperature, "pressure": pressure}
                if "W" in ds:
                    fields["w"] = destagger(cropped("W"), 0)
                if "QVAPOR" in ds:
                    r = cropped("QVAPOR")
                    fields["specific_humidity"] = np.where(r >= 0, r / (1 + r), np.nan)
                surface = {}
                if all(name in ds for name in ("U10", "V10", "T2", "PSFC")):
                    u10, v10 = cropped("U10"), cropped("V10")
                    surface = {"surface_u": u10 * ca - v10 * sa, "surface_v": v10 * ca + u10 * sa,
                               "surface_temperature": cropped("T2"), "surface_pressure": cropped("PSFC")}
                snapshots.append((time, fields, surface))
    if not snapshots:
        raise PredictorError("No WRF timestamps match the requested time interval.")
    snapshots.sort(key=lambda entry: entry[0])
    unique = []
    for snapshot in snapshots:
        if unique and snapshot[0] == unique[-1][0]:
            previous = unique[-1]
            if (snapshot[1].keys() != previous[1].keys() or snapshot[2].keys() != previous[2].keys()
                or any(not np.allclose(value, previous[1][name], equal_nan=True) for name, value in snapshot[1].items())
                or any(not np.allclose(value, previous[2][name], equal_nan=True) for name, value in snapshot[2].items())):
                raise PredictorError("Conflicting duplicate WRF timestamps. Do not mix runs/domains.")
            continue
        unique.append(snapshot)
    field_names = set.intersection(*(set(entry[1]) for entry in unique))
    surface_names = set.intersection(*(set(entry[2]) for entry in unique))
    fields = {name: np.stack([entry[1][name] for entry in unique]) for name in field_names}
    surface = {name: np.stack([entry[2][name] for entry in unique]) for name in surface_names}
    surface["terrain"] = terrain
    if not surface_names:
        warnings.append("WRF surface winds/thermodynamics are incomplete; hold the lowest mass-level wind near ground.")
    return dataset_from_arrays([entry[0] for entry in unique], latitude, longitude, fields, surface,
                               {"source": "wrf", "warnings_json": json.dumps(warnings),
                                "provenance_json": json.dumps({"files": [Path(p).name for p in paths], "domain": "one fixed WRF-ARW grid"}),
                                **(attrs or {})})
