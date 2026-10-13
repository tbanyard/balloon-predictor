"""Read regular pressure-level GRIB/NetCDF, retaining real valid times."""
import json
from pathlib import Path

import numpy as np
import xarray as xr

from ..errors import PredictorError
from .schema import G, R_D, dataset_from_arrays, geometric_height

ALIASES = {"u": "u", "u_component_of_wind": "u", "v": "v", "v_component_of_wind": "v",
           "t": "temperature", "temperature": "temperature", "z": "geopotential", "geopotential": "geopotential",
           "gh": "geopotential_height", "geopotential_height": "geopotential_height",
           "w": "omega", "vertical_velocity": "omega",
           "10u": "surface_u", "u10": "surface_u", "10m_u_component_of_wind": "surface_u",
           "10v": "surface_v", "v10": "surface_v", "10m_v_component_of_wind": "surface_v",
           "2t": "surface_temperature", "t2m": "surface_temperature", "2m_temperature": "surface_temperature",
           "sp": "surface_pressure", "surface_pressure": "surface_pressure"}


def _indices(lat, lon, area):
    lon = (np.asarray(lon) + 180) % 360 - 180
    lat = np.asarray(lat)
    yi, xi = np.argsort(lat), np.argsort(lon)
    # A duplicate 360/0 longitude is not another grid column.
    xi = xi[np.r_[True, np.diff(lon[xi]) > 1e-7]]
    if area:
        n, w, s, e = area
        yi = yi[(lat[yi] >= s - 1e-6) & (lat[yi] <= n + 1e-6)]
        xi = xi[(lon[xi] >= w - 1e-6) & (lon[xi] <= e + 1e-6)]
    if len(yi) < 2 or len(xi) < 2:
        raise PredictorError("Selected area contains fewer than 2×2 weather grid points.")
    return yi, xi, lat[yi], lon[xi]


def _assemble(records, lat, lon, versions=None, attrs=None):
    times = sorted({key[0] for key in records if key[2] == "u"})
    levels = sorted({key[1] for key in records if key[2] == "u"}, reverse=True)
    if not times or len(levels) < 2:
        raise PredictorError("No usable pressure-level winds were found.")
    shape = (len(times), len(levels), len(lat), len(lon))
    fields = {}
    for name in ("u", "v", "temperature", "geopotential", "geopotential_height", "omega"):
        if not any(key[2] == name for key in records):
            continue
        values = np.full(shape, np.nan, dtype="float32")
        for ti, time in enumerate(times):
            for li, level in enumerate(levels):
                if (time, level, name) in records:
                    values[ti, li] = records[(time, level, name)][1]
        fields[name] = values
    for name in ("u", "v", "temperature"):
        if name not in fields:
            raise PredictorError(f"Missing pressure-level {name}; import U, V, temperature and geopotential/height.")
        if not np.isfinite(fields[name]).any(axis=(2, 3)).all():
            raise PredictorError(f"Missing {name} at one or more valid times/pressure levels.")
    if "geopotential" in fields:
        height = fields.pop("geopotential") / G
        if "geopotential_height" in fields:
            height = np.where(np.isfinite(height), height, fields.pop("geopotential_height"))
    elif "geopotential_height" in fields:
        height = fields.pop("geopotential_height")
    else:
        raise PredictorError("Geopotential (z) or geopotential height (gh) is required; no standard-atmosphere height guessing.")
    fields["height"] = geometric_height(height)
    if not np.isfinite(fields["height"]).any(axis=(2, 3)).all():
        raise PredictorError("Missing geopotential/height at one or more valid times/pressure levels.")
    fields["pressure"] = np.broadcast_to(np.asarray(levels, dtype="float32")[None, :, None, None] * 100, shape).copy()
    if "omega" in fields:
        fields["w"] = -fields.pop("omega") * R_D * fields["temperature"] / (fields["pressure"] * G)
    surface = {}
    surface_shape = (len(times), len(lat), len(lon))
    for name in ("surface_u", "surface_v", "surface_temperature", "surface_pressure"):
        if all((time, 0, name) in records for time in times):
            surface[name] = np.stack([records[(time, 0, name)][1] for time in times])
    terrain_keys = sorted(key for key in records if key[1] == 0 and key[2] in ("geopotential", "geopotential_height"))
    if terrain_keys:
        key = terrain_keys[0]
        terrain = records[key][1] / G if key[2] == "geopotential" else records[key][1]
        surface["terrain"] = geometric_height(terrain)
    warnings = ["Pressure-level winds are interpolated in geometric altitude; sub-ground pressure levels are discarded."]
    if not surface:
        warnings.append("No surface data: below the lowest valid pressure level, its wind is held constant. Use fixed landing altitude.")
    elif not all(key in surface for key in ("surface_u", "surface_v", "surface_temperature", "surface_pressure")):
        warnings.append("Incomplete surface fields: below the lowest valid pressure level, its wind is held constant.")
    provenance = {"pressure_levels_hpa": levels, "expver": sorted(versions or []),
                  "height_conversion": "z/g -> geopotential height -> spherical geometric altitude",
                  "surface_fields": sorted(surface)}
    return dataset_from_arrays(times, lat, lon, fields, surface,
                               {"warnings_json": json.dumps(warnings), "provenance_json": json.dumps(provenance), **(attrs or {})})


def read_grib(paths, area=None, start=None, end=None, attrs=None):
    """Stream GRIB messages and crop each decoded field before retaining it."""
    try:
        import eccodes as ec
    except ImportError as exc:
        raise PredictorError("Install the weather extra: pip install -e '.[weather]'.") from exc
    records, versions, grid = {}, set(), None
    lat, lon = None, None
    for path in paths:
        with Path(path).open("rb") as f:
            while (gid := ec.codes_grib_new_from_file(f)) is not None:
                try:
                    short = ec.codes_get(gid, "shortName")
                    name = ALIASES.get(short)
                    if name is None:
                        continue
                    if ec.codes_get(gid, "gridType") != "regular_ll":
                        raise PredictorError("Only regular latitude/longitude GRIB is supported. Regrid reduced Gaussian/model-level data first.")
                    typ = ec.codes_get(gid, "typeOfLevel")
                    if typ == "isobaricInhPa":
                        level = float(ec.codes_get(gid, "level"))
                    elif typ == "isobaricInPa":
                        level = float(ec.codes_get(gid, "level")) / 100
                    elif typ in ("surface", "heightAboveGround"):
                        level = 0
                        if name in ("u", "v", "temperature", "omega"):
                            continue
                    else:
                        continue
                    date, clock = int(ec.codes_get(gid, "validityDate")), int(ec.codes_get(gid, "validityTime"))
                    time = np.datetime64(f"{date // 10000:04d}-{date // 100 % 100:02d}-{date % 100:02d}T{clock // 100:02d}:{clock % 100:02d}:00", "ns")
                    static_terrain = level == 0 and name in ("geopotential", "geopotential_height")
                    if not static_terrain and (start is not None and time < start or end is not None and time > end):
                        continue
                    ni, nj = int(ec.codes_get(gid, "Ni")), int(ec.codes_get(gid, "Nj"))
                    consecutive = int(ec.codes_get(gid, "jPointsAreConsecutive"))
                    def grid_array(key):
                        a = ec.codes_get_array(gid, key)
                        return a.reshape(ni, nj).T if consecutive else a.reshape(nj, ni)
                    lats, lons = grid_array("latitudes"), grid_array("longitudes")
                    if not np.allclose(lats, lats[:, :1]) or not np.allclose(lons, lons[:1, :]):
                        raise PredictorError("GRIB grid coordinates are not separable regular axes.")
                    yi, xi, newlat, newlon = _indices(lats[:, 0], lons[0], area)
                    signature = (tuple(newlat), tuple(newlon))
                    if grid is not None and signature != grid:
                        raise PredictorError("GRIB files have different horizontal grids.")
                    grid, lat, lon = signature, newlat, newlon
                    value = grid_array("values")
                    if ec.codes_get(gid, "bitmapPresent"):
                        value = np.where(value == ec.codes_get(gid, "missingValue"), np.nan, value)
                    value = value[np.ix_(yi, xi)].astype("float32")
                    try:
                        version = int(ec.codes_get(gid, "experimentVersionNumber"))
                        versions.add(version)
                    except (ec.CodesInternalError, ValueError):
                        version = 1
                    key = (time, level, name)
                    # Prefer final ERA5 over ERA5T for duplicate observations.
                    priority = 0 if version == 1 else 1
                    if key in records and priority == records[key][0]:
                        old = records[key][1]
                        overlap = np.isfinite(old) & np.isfinite(value)
                        if not np.allclose(old[overlap], value[overlap], rtol=1e-5, atol=1e-5):
                            raise PredictorError("Conflicting duplicate GRIB fields/timestamps. Do not mix forecast cycles.")
                        value = np.where(np.isfinite(old), old, value)
                    if key not in records or priority < records[key][0]:
                        records[key] = (priority, value)
                finally:
                    ec.codes_release(gid)
    return _assemble(records, lat, lon, versions, attrs)


def _collapse_extra(da):
    if "expver" in da.dims:
        values = list(da.expver.values)
        order = sorted(range(len(values)), key=lambda i: 0 if str(values[i]).lstrip("0") == "1" else 1)
        merged = da.isel(expver=order[0], drop=True)
        for index in order[1:]:
            merged = merged.fillna(da.isel(expver=index, drop=True))
        da = merged
    for dim in ("number", "ensemble", "member"):
        if dim in da.dims:
            if da.sizes[dim] != 1:
                raise PredictorError("Select one meteorological ensemble member before importing.")
            da = da.isel({dim: 0}, drop=True)
    return da


def read_netcdf(paths, area=None, start=None, end=None, attrs=None):
    records, versions, grid = {}, set(), None
    lat, lon = None, None
    for path in paths:
        with xr.open_dataset(path, engine="netcdf4") as ds:
            latname = next((key for key in ("latitude", "lat") if key in ds), None)
            lonname = next((key for key in ("longitude", "lon") if key in ds), None)
            if not latname or not lonname or ds[latname].ndim != 1 or ds[lonname].ndim != 1:
                raise PredictorError("Pressure-level NetCDF requires one-dimensional latitude/longitude coordinates.")
            yd, xd = ds[latname].dims[0], ds[lonname].dims[0]
            yi, xi, newlat, newlon = _indices(ds[latname].values, ds[lonname].values, area)
            signature = (tuple(newlat), tuple(newlon))
            if grid is not None and signature != grid:
                raise PredictorError("NetCDF files have different horizontal grids.")
            grid, lat, lon = signature, newlat, newlon
            if "expver" in ds:
                versions.update(int(v) for v in np.asarray(ds.expver.values).ravel())
            for variable in ds.data_vars:
                name = ALIASES.get(variable)
                if not name:
                    continue
                da = _collapse_extra(ds[variable]).isel({yd: yi, xd: xi})
                pd = next((key for key in ("pressure_level", "isobaricInhPa", "isobaricInPa", "level", "plev") if key in da.dims), None)
                sample_dims = tuple(dim for dim in da.dims if dim not in (pd, yd, xd))
                if any(dim not in ("time", "valid_time", "step") for dim in sample_dims):
                    raise PredictorError(f"Unsupported sample dimensions in {variable}: {sample_dims}.")
                if "valid_time" in ds:
                    timecoord = ds.valid_time
                elif "time" in ds and "step" in da.dims:
                    timecoord = ds.time + ds.step
                elif "time" in ds:
                    timecoord = ds.time
                else:
                    raise PredictorError("NetCDF has no valid_time or time coordinate.")
                template = da.isel({yd: 0, xd: 0, **({pd: 0} if pd else {})}, drop=True)
                timecoord, _ = xr.broadcast(timecoord, template)
                if sample_dims:
                    times = timecoord.transpose(*sample_dims).values.reshape(-1).astype("datetime64[ns]")
                else:
                    times = np.asarray(timecoord.values).reshape(-1).astype("datetime64[ns]")[:1]
                if np.isnat(times).any():
                    raise PredictorError("NetCDF contains invalid timestamps.")
                if pd:
                    levels = np.asarray(ds[pd].values, dtype=float)
                    unit = str(ds[pd].attrs.get("units", "")).lower()
                    if pd == "isobaricInPa" or unit in ("pa", "pascal", "pascals"):
                        levels /= 100
                    values = da.transpose(*sample_dims, pd, yd, xd).values.reshape(len(times), len(levels), len(lat), len(lon))
                else:
                    levels = np.array([0])
                    values = da.transpose(*sample_dims, yd, xd).values.reshape(len(times), 1, len(lat), len(lon))
                for ti, time in enumerate(times):
                    static_terrain = pd is None and name in ("geopotential", "geopotential_height")
                    if not static_terrain and (start is not None and time < start or end is not None and time > end):
                        continue
                    for li, level in enumerate(levels):
                        key = (time, float(level), name)
                        value = values[ti, li].astype("float32")
                        if key in records:
                            old = records[key][1]
                            overlap = np.isfinite(old) & np.isfinite(value)
                            if not np.allclose(old[overlap], value[overlap], rtol=1e-5, atol=1e-5):
                                raise PredictorError("Conflicting duplicate weather fields/timestamps across NetCDF files.")
                            value = np.where(np.isfinite(old), old, value)
                        records[key] = (0, value)
    return _assemble(records, lat, lon, versions, attrs)


def read_pressure_files(paths, **kwargs):
    suffixes = {Path(path).suffix.lower() for path in paths}
    if suffixes <= {".grib", ".grb", ".grib2", ".grb2"}:
        return read_grib(paths, **kwargs)
    if suffixes <= {".nc", ".nc4", ".netcdf"}:
        return read_netcdf(paths, **kwargs)
    raise PredictorError("Use only NetCDF files or only GRIB files per pressure-level import.")
