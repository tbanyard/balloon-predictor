"""A small, documented NetCDF schema shared by every weather adapter."""
import json
from datetime import datetime, timezone

import numpy as np
import xarray as xr

from ..errors import PredictorError

G = 9.80665
R_D = 287.05
EARTH_RADIUS = 6371008.8


def geometric_height(geopotential_height):
    h = np.asarray(geopotential_height)
    return EARTH_RADIUS * h / (EARTH_RADIUS - h)


def dataset_from_arrays(times, latitude, longitude, fields, surface=None, attrs=None):
    latitude, longitude = np.asarray(latitude), np.asarray(longitude)
    if latitude.ndim == longitude.ndim == 1:
        longitude, latitude = np.meshgrid(longitude, latitude)
    shape = np.asarray(fields["u"]).shape
    if len(shape) != 4 or shape[0] != len(times) or shape[2:] != latitude.shape:
        raise PredictorError("Expected fields on (time, level, y, x) and matching latitude/longitude.")
    ds = xr.Dataset(coords={"time": np.asarray(times, dtype="datetime64[ns]"),
                            "level": np.arange(shape[1]), "y": np.arange(shape[2]), "x": np.arange(shape[3])})
    ds["latitude"] = (("y", "x"), latitude.astype("float64"))
    ds["longitude"] = (("y", "x"), longitude.astype("float64"))
    for key, value in fields.items():
        value = np.asarray(value, dtype="float32")
        if value.shape != shape:
            raise PredictorError(f"Field {key} does not match the wind grid.")
        ds[key] = (("time", "level", "y", "x"), value)
    for key, value in (surface or {}).items():
        value = np.asarray(value, dtype="float32")
        dims = ("y", "x") if value.ndim == 2 else ("time", "y", "x")
        ds[key] = (dims, value)
    units = {"u": "m s-1", "v": "m s-1", "w": "m s-1", "height": "m",
             "temperature": "K", "pressure": "Pa", "terrain": "m", "specific_humidity": "kg kg-1",
             "surface_u": "m s-1", "surface_v": "m s-1", "surface_temperature": "K", "surface_pressure": "Pa"}
    for name, unit in units.items():
        if name in ds:
            ds[name].attrs["units"] = unit
    ds.attrs.update({"schema_version": 1, "height_reference": "AMSL", "wind_reference": "earth",
                     "created_utc": datetime.now(timezone.utc).isoformat(), **(attrs or {})})
    return ds


def validate_dataset(ds):
    for name in ("u", "v", "height", "temperature", "pressure", "latitude", "longitude"):
        if name not in ds:
            raise PredictorError(f"Weather dataset is missing {name}.")
    if ds.sizes.get("time", 0) < 1:
        raise PredictorError("Weather has no valid timestamps.")
    if ds.sizes.get("level", 0) < 2 or ds.sizes.get("y", 0) < 2 or ds.sizes.get("x", 0) < 2:
        raise PredictorError("Weather needs at least two vertical levels and a 2×2 horizontal grid.")
    if np.any(np.diff(ds.time.values.astype("datetime64[ns]").astype("int64")) <= 0):
        raise PredictorError("Weather times must be unique and strictly increasing.")
    if not np.isfinite(ds.latitude).all() or not np.isfinite(ds.longitude).all():
        raise PredictorError("Invalid weather grid coordinates.")
    for name in ("u", "v", "height", "temperature", "pressure"):
        if tuple(ds[name].dims) != ("time", "level", "y", "x"):
            raise PredictorError(f"{name} must use (time, level, y, x).")
    return ds


def describe_dataset(ds, name):
    heights = np.asarray(ds.height.values)
    finite = np.isfinite(heights)
    for field in ("u", "v", "temperature", "pressure"):
        finite &= np.isfinite(ds[field].values)
    finite &= (ds.temperature.values > 0) & (ds.pressure.values > 0)
    if "terrain" in ds:
        finite &= heights > ds.terrain.values[None, None] + 0.1
    tops = np.max(np.where(finite, heights, -np.inf), axis=1)
    valid_tops = tops[np.isfinite(tops)]
    if not valid_tops.size:
        raise PredictorError("No valid atmospheric columns were found above terrain.")
    def stamp(t):
        return np.datetime_as_string(t.astype("datetime64[s]"), unit="s") + "Z"
    return {"id": name, "label": ds.attrs.get("label", name), "source": ds.attrs.get("source", "custom"),
            "start": stamp(ds.time.values[0]), "end": stamp(ds.time.values[-1]),
            "area": [float(ds.latitude.max()), float(ds.longitude.min()),
                     float(ds.latitude.min()), float(ds.longitude.max())],
            "minimum_top_altitude": float(np.min(valid_tops)), "maximum_altitude": float(np.max(valid_tops)),
            "missing_columns": int(np.sum(~np.isfinite(tops))),
            "shape": dict(ds.sizes), "terrain_available": "terrain" in ds,
            "vertical_wind_available": "w" in ds, "humidity_available": "specific_humidity" in ds,
            "warnings": json.loads(ds.attrs.get("warnings_json", "[]")),
            "provenance": json.loads(ds.attrs.get("provenance_json", "{}"))}
