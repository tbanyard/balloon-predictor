"""Bounded portable forecast packs for Android's offline single-flight calculator."""
from datetime import datetime, timezone
import json
from pathlib import Path
import struct
import uuid

from netCDF4 import Dataset
import numpy as np

from .errors import PredictorError
from .modelbox import model_weather, selection
from .models import ModelBoxRequest, PredictionRequest
from .provenance import validate_manifest
from .weather.io import NETCDF_LOCK

MAGIC = b"BPF1"
MAX_BYTES = 32 * 1024**2
FIELDS = ("height", "u", "v", "temperature", "pressure", "w")
SURFACE = ("surface_u", "surface_v", "surface_temperature", "surface_pressure")


def export_forecast_pack(result, store, progress):
    """Keep native grid nodes, levels and bracketing model records around a flight."""
    req = PredictionRequest.model_validate(result["request"])
    path = store.settings.data_dir / "exports" / (uuid.uuid4().hex + ".bpforecast")
    try:
        with NETCDF_LOCK:
            validate_manifest(result)
            weather = model_weather(store, req.dataset, req.launch_datetime)
            if not getattr(weather, "regular", False) or not hasattr(weather, "source_path"):
                raise PredictorError("Offline packs need a saved regular-grid dataset. Download IFS, GFS, or pressure-level ERA5 in Manage Weather.")
            points = [p for flight in [result, *result.get("ensemble", [])]
                      for stage in flight["prediction"] for p in stage["trajectory"]]
            ys, xs, times, box = selection(weather, points, ModelBoxRequest(margin_degrees=.75, margin_km=75))
            # Include the next and preceding model time when available, to permit
            # a launch-time adjustment on the phone. Never invent time coverage.
            times = list(range(max(0, times[0]-1), min(len(weather.times), times[-1]+2)))
            centre = box["longitude_centre"]
            lons = centre + (weather.lon[0,xs] - centre + 180) % 360 - 180
            order = np.argsort(lons)
            xs, lons = xs[order], lons[order]
            if len(ys) < 2 or len(xs) < 2 or len(times) < 2 or len(weather.levels) < 2:
                raise PredictorError("This offline pack needs at least two grid points in each direction, times and levels. Download a larger window.")
            ny, nx, nl, nt = len(ys), len(xs), len(weather.levels), len(times)
            blocks = []
            with Dataset(weather.source_path) as nc:
                for key in (*FIELDS, *SURFACE, "terrain"):
                    if key not in nc.variables:
                        continue
                    shape = [nt,nl,ny,nx] if key in FIELDS else [nt,ny,nx] if key in SURFACE else [ny,nx]
                    blocks.append({"name":key,"shape":shape})
                if not set(FIELDS[:5]).issubset(item["name"] for item in blocks):
                    raise PredictorError("This dataset is missing winds, height, temperature or pressure.")
                size = sum(int(np.prod(item["shape"]))*4 for item in blocks)
                if size > MAX_BYTES:
                    raise PredictorError("This pack is over 32 MiB. Use a smaller regional forecast or a shorter flight.")
                setup = req.model_dump(mode="json")
                header = {"format":"BalloonPredictor offline forecast 1", "label":weather.info["label"],
                          "source":weather.info["source"], "created":datetime.now(timezone.utc).isoformat(),
                          "lat":weather.lat[ys,0].tolist(), "lon":lons.tolist(), "time":weather.times[times].tolist(),
                          "levels":nl, "fields":blocks, "setup":setup,
                          "gap_seconds":store.settings.max_time_gap_hours*3600}
                metadata = json.dumps(header, ensure_ascii=False, allow_nan=False, separators=(",",":")).encode()
                if len(metadata)>64*1024:
                    raise PredictorError("The grid metadata exceeds the phone pack limit. Crop the forecast.")
                progress("Packing the model grid for offline use")
                with path.open("wb") as file:
                    file.write(MAGIC + struct.pack("<I",len(metadata)) + metadata)
                    for item in blocks:
                        variable = nc[item["name"]]
                        index = (times,slice(None),ys,xs) if variable.ndim==4 else (times,ys,xs) if variable.ndim==3 else (ys,xs)
                        data = np.ma.asarray(variable[index],dtype="<f4").filled(np.nan)
                        if list(data.shape)!=item["shape"]:
                            raise PredictorError("Forecast grid does not match the offline format.")
                        file.write(data.tobytes(order="C"))
            validate_manifest(result)
        return {"path":str(path),"filename":f"forecast-{req.dataset}-{req.launch_datetime:%Y%m%dT%H%MZ}.bpforecast",
                "bytes":path.stat().st_size,"type":"offline"}
    except Exception:
        path.unlink(missing_ok=True)
        raise
