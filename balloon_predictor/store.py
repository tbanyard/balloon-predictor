from collections import OrderedDict
import json
import os
from pathlib import Path
import re
import threading
import uuid

from filelock import FileLock
import xarray as xr

from .errors import PredictorError
from .weather import DatasetWeather, DemoWeather
from .weather.schema import describe_dataset, validate_dataset
from .weather.io import NETCDF_LOCK
from .weather.native import NativeCatalog


def checked_name(name):
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", name) or name in ("demo", "era5", "wrf"):
        raise PredictorError("Dataset name must be 1–64 lowercase letters, numbers, '-' or '_'; demo, era5 and wrf are reserved.")
    return name


def atomic_json(path, value):
    path = Path(path)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        temporary.write_text(json.dumps(value, indent=2, allow_nan=False), encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class WeatherStore:
    def __init__(self, settings):
        self.settings = settings
        settings.prepare()
        self.cache = OrderedDict()
        self.lock = threading.RLock()
        self.native = NativeCatalog(settings)
        from .terrain import TerrainCatalog
        self.terrain = TerrainCatalog(settings.terrain_dir or settings.data_dir / "terrain")

    def list(self):
        result = [DemoWeather.info, *self.native.list()]
        for path in sorted((self.settings.data_dir / "weather").glob("*.json")):
            try:
                metadata = json.loads(path.read_text(encoding="utf-8"))
                checked_name(metadata["id"])
                if (path.parent / (metadata["id"] + ".nc")).exists():
                    result.append(metadata)
            except (ValueError, KeyError, PredictorError):
                continue
        return result

    def exists(self, name):
        checked_name(name)
        return (self.settings.data_dir / "weather" / (name + ".nc")).exists()

    def save(self, name, ds, overwrite=False):
        checked_name(name)
        directory = self.settings.data_dir / "weather"
        with FileLock(str(directory / (name + ".lock")), timeout=60), self.lock, NETCDF_LOCK:
            if self.exists(name) and not overwrite:
                raise PredictorError(f"Dataset '{name}' already exists. Use another name or CLI --overwrite.")
            validate_dataset(ds)
            size_mb = ds.nbytes / 1024**2
            if size_mb > self.settings.max_weather_mb:
                raise PredictorError(f"Dataset is {size_mb:.0f} MB, above the configured {self.settings.max_weather_mb} MB limit. Crop the WRF domain/time window or raise max_weather_mb.")
            metadata = describe_dataset(ds, name)
            path = directory / (name + ".nc")
            temporary = directory / (name + "." + uuid.uuid4().hex + ".tmp.nc")
            try:
                encoding = {key: {"zlib": True, "complevel": 2} for key in ds.data_vars if key not in ("latitude", "longitude")}
                ds.to_netcdf(temporary, engine="netcdf4", encoding=encoding)
                os.replace(temporary, path)
                atomic_json(directory / (name + ".json"), metadata)
            finally:
                temporary.unlink(missing_ok=True)
            self.cache.clear()
            return metadata

    def get(self, name, when=None):
        if name == "demo":
            weather = DemoWeather()
            weather.terrain_catalog = self.terrain
            return weather
        if name in ("era5", "wrf") or any(info["id"] == name for info in self.native.wrf):
            weather = self.native.get(name, when)
            weather.terrain_catalog = self.terrain
            return weather
        checked_name(name)
        path = self.settings.data_dir / "weather" / (name + ".nc")
        if not path.exists():
            raise PredictorError(f"Weather dataset '{name}' is not available. Download or import it first.")
        with self.lock, NETCDF_LOCK:
            stamp = path.stat().st_mtime_ns
            key = (name, stamp)
            if key in self.cache:
                return self.cache[key]
            with xr.open_dataset(path, engine="netcdf4") as ds:
                if ds.nbytes / 1024**2 > self.settings.max_weather_mb:
                    raise PredictorError("Weather dataset exceeds max_weather_mb. Crop it or increase the setting.")
                from .provenance import record_file
                from .errors import CoverageError
                before = path.stat()
                ds.load()
                after = path.stat()
                if (before.st_mtime_ns,before.st_size)!=(after.st_mtime_ns,after.st_size):
                    raise CoverageError("Weather file changed during loading. Retry after the writer finishes.")
                weather = DatasetWeather(ds, describe_dataset(ds, name))
                weather.source_path = path
                record_file(weather, path)
                weather.terrain_catalog = self.terrain
            # Keep one materialized weather dataset; in-flight references remain valid.
            self.cache.clear()
            self.cache[key] = weather
            return weather
