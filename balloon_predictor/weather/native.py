"""Direct local ERA5 L137 and native WRF readers.

Only the horizontal coordinates and the needed vertical columns are loaded.
Weather volumes stay on disk; an LRU byte budget bounds decoded column data.
"""
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
import hashlib
from pathlib import Path
import re

import numpy as np
from netCDF4 import Dataset, num2date

from ..errors import CoverageError, PredictorError
from .era5_native.data import Archive, DataError, decode, open_nc, iso
from .era5_native.physics import full_pressure, hydrostatic_height
from .interpolate import DatasetWeather
from .io import NETCDF_LOCK
from .schema import G, geometric_height


class ColumnCache:
    def __init__(self, mb):
        self.limit = int(mb * 1024**2)
        self.bytes = 0
        self.items = OrderedDict()

    def get(self, key):
        if key in self.items:
            self.items.move_to_end(key)
            return self.items[key][0]

    def put(self, key, value):
        def byte_size(v):
            if isinstance(v,np.ndarray): return v.nbytes
            if isinstance(v,dict): return sum(byte_size(x) for x in v.values())
            return 8
        size = byte_size(value)
        if key in self.items:
            self.bytes -= self.items.pop(key)[1]
        if size <= self.limit:
            while self.items and self.bytes + size > self.limit:
                self.bytes -= self.items.popitem(last=False)[1][1]
            self.items[key] = (value, size)
            self.bytes += size
        return value


def interpolate_column(column, altitude):
    h = np.asarray(column["height"], dtype=float)
    order = np.argsort(h)
    h = h[order]
    fields = {k: np.asarray(column[k], dtype=float)[order] for k in
              ("u", "v", "temperature", "pressure", "w", "specific_humidity") if k in column}
    good = np.isfinite(h) & (h > column.get("terrain", -np.inf) + .1)
    for key in ("u", "v", "temperature", "pressure"):
        good &= np.isfinite(fields[key])
    good &= (fields["temperature"] > 0) & (fields["pressure"] > 0)
    if np.count_nonzero(good) < 2:
        raise CoverageError("Fewer than two valid levels above terrain at this location.")
    # Keep holes inside a column: a missing layer cannot be interpolated away.
    first, last = np.flatnonzero(good)[[0, -1]]
    h = h[first:last + 1]
    fields = {k: v[first:last + 1] for k, v in fields.items()}
    good = good[first:last + 1]
    if altitude > h[-1] + .5:
        raise CoverageError(f"Altitude {altitude:.0f} m exceeds available model top ({h[-1]:.0f} m here).")
    surface = column.get("surface")
    if surface and h[0] > column["terrain"] + 10:
        h = np.r_[column["terrain"] + 10, h]
        good = np.r_[True, good]
        fields = {k: np.r_[surface.get(k, v[0] if k == "specific_humidity" else 0), v] for k, v in fields.items()}
    if altitude <= h[0]:
        i = j = 0
        fraction = 0
    else:
        j = min(int(np.searchsorted(h, altitude, side="left")), len(h) - 1)
        if abs(h[j] - altitude) <= .5:
            i = j
            fraction = 0
        else:
            i = j - 1
            fraction = (altitude - h[i]) / (h[j] - h[i])
    if not good[i] or not good[j]:
        raise CoverageError("An atmospheric layer is missing at the requested altitude.")
    result = {}
    for key, values in fields.items():
        a, b = values[i], values[j]
        if key == "specific_humidity" and not (np.isfinite(a) and np.isfinite(b) and 0 <= a < 1 and 0 <= b < 1):
            continue
        if not np.isfinite(a) or not np.isfinite(b):
            raise CoverageError(f"Missing {key} in an atmospheric column.")
        result[key] = float(np.exp(np.log(a) * (1-fraction) + np.log(b) * fraction)
                            if key == "pressure" else a * (1-fraction) + b * fraction)
    return result


class NativeWeather(DatasetWeather):
    def _column(self, ti, y, x, altitude):
        return interpolate_column(self.raw_column(ti, y, x), altitude)

    def sample(self, lat, lon, altitude, when):
        with NETCDF_LOCK:
            try:
                self.ensure_time(when)
                self.time_bracket(when)
                return super().sample(lat, lon, altitude, when)
            except (DataError, OSError, RuntimeError) as exc:
                raise PredictorError(f"Cannot sample local weather: {exc}") from exc

    def time_bracket(self, when):
        i, j, fraction = self._bracket(self.times, when.timestamp(), "Time")
        if i != j and 1e-12 < fraction < 1-1e-12 and self.times[j]-self.times[i] > self.max_gap:
            raise CoverageError("Adjacent weather records are more than the configured time gap apart. Choose an available launch window.")
        return i, j, fraction

    def ensure_time(self, when):
        pass


class ERA5Weather(NativeWeather):
    def __init__(self, archive, when, cache, max_gap):
        self.archive, self.cache, self.max_gap = archive, cache, max_gap
        self.loaded_days = set()
        self.records = []
        self.ensure_time(when)
        if not self.records:
            raise CoverageError(f"No ERA5 data near {iso(when)}. Choose a date present in your local archive.")
        nearest = min(self.records, key=lambda e: abs(e[0]-when.timestamp()))
        self.meta = archive.meta(nearest[1])
        if not {"u", "v", "t"}.issubset(self.meta["fields"]) or not self.meta["height_available"]:
            raise PredictorError("ERA5 needs u, v, t, lnsp/sp and contiguous model levels through 137.")
        if self.meta["surface_z"] is None and self.meta["topo_indices"] is None:
            raise PredictorError("ERA5 trajectories need sea-level heights. Add matching era5_topo.nc to the archive root, or surface z to the weather files.")
        lon, lat = np.meshgrid(self.meta["lon"], self.meta["lat"])
        self.setup_grid(lat, lon)
        self.info = era5_info(archive)
        self.info.update(terrain_available=True, humidity_available="q" in self.meta["fields"],
                         area=[float(lat.max()), float(lon.min()), float(lat.min()), float(lon.max())],
                         warnings=list(self.meta["warnings"]),
                         provenance={"root": str(archive.root), "reader": "direct ERA5 L137 columns",
                                     "height": "Geometric AMSL converted from hydrostatic geopotential height",
                                     "humidity": "q if supplied; dry height approximation otherwise",
                                     "surface_z": self.meta["surface_z"] or "era5_topo.nc"})

    def ensure_time(self, when):
        for delta in (-1, 0, 1):
            day = (when + timedelta(days=delta)).date().isoformat()
            if day in self.loaded_days:
                continue
            self.loaded_days.add(day)
            if day in self.archive.dates:
                for entry in self.archive.day(day)["times"]:
                    dt = datetime.fromisoformat(entry["datetime"].replace("Z", "+00:00"))
                    self.records.append((dt.timestamp(), entry["file"], entry["index"]))
        self.records.sort()
        self.times = np.array([r[0] for r in self.records])

    def raw_column(self, ti, y, x):
        _, fid, index = self.records[ti]
        meta = self.archive.meta(fid)
        for warning in meta["warnings"]:
            if warning not in self.info["warnings"]:
                self.info["warnings"].append(warning)
        used=self.info["provenance"].setdefault("files",[])
        relative=str(meta["path"].relative_to(self.archive.root))
        if relative not in used:used.append(relative)
        if (not np.array_equal(meta["levels"], self.meta["levels"]) or
            not np.array_equal(meta["lat"], self.meta["lat"]) or not np.array_equal(meta["lon"], self.meta["lon"])):
            raise CoverageError("ERA5 grids or model levels change across files. Use a consistent archive.")
        key = ("era5", str(meta["path"]), meta["stamp"], index, y, x)
        cached = self.cache.get(key)
        if cached is not None:
            return cached
        timedim, leveldim, latdim, londim = meta["dims"]
        def point(var, level=True):
            selection = []
            for dim, size in zip(var.dimensions, var.shape):
                if dim == timedim:
                    selection.append(index)
                elif dim == latdim:
                    selection.append(int(meta["yi"][y]))
                elif dim == londim:
                    selection.append(int(meta["xi"][x]))
                elif dim == leveldim and level:
                    selection.append(slice(None))
                elif size == 1:
                    selection.append(0)
                else:
                    raise PredictorError(f"Unsupported ERA5 dimension {dim} in {var.name}.")
            arr = decode(var, tuple(selection))
            return arr[meta["li"]] if leveldim in var.dimensions and level else float(arr)
        with open_nc(meta["path"]) as nc:
            fields = {key: point(nc.variables[name]) for key, name in
                      (("u", "u"), ("v", "v"), ("temperature", "t"))}
            sp = np.exp(point(nc.variables["lnsp"], False)) if "lnsp" in nc.variables else point(nc.variables["sp"], False)
            q = point(nc.variables["q"]) if "q" in meta["fields"] else None
            if meta["surface_z"]:
                surface_z = point(nc.variables[meta["surface_z"]], False)
            elif meta["topo_indices"] is not None:
                yi, xi = meta["topo_indices"]
                surface_z = float(self.archive.topography["z"][yi[y], xi[x]])
            else:
                raise PredictorError("Surface geopotential is unavailable in this ERA5 file.")
            fields["pressure"] = full_pressure(sp, meta["levels"])
            fields["height"] = geometric_height(hydrostatic_height(fields["temperature"], sp, meta["levels"], q) + surface_z/G)
            fields["terrain"] = float(geometric_height(surface_z/G))
            if q is not None:
                fields["specific_humidity"] = q
        return self.cache.put(key, fields)

    def terrain(self, lat, lon):
        with NETCDF_LOCK:
            ti = min(range(len(self.records)), key=lambda i: abs(self.records[i][0]-self.times[0]))
            return sum(self.raw_column(ti, y, x)["terrain"]*w for y, x, w in self.spatial_weights(lat, lon))


def era5_info(archive):
    dates = sorted(archive.dates)
    return {"id": "era5", "label": "ERA5 · local model-level archive", "source": "era5",
            "available": bool(dates), "native": True, "root": str(archive.root), "dates": dates,
            "start": dates[0]+"T00:00:00Z" if dates else None,
            "end": dates[-1]+"T23:59:59Z" if dates else None,
            "time_bounds_exact": False, "area": None, "minimum_top_altitude": None,
            "maximum_altitude": None, "terrain_available": bool(archive.topography) or None,
            "vertical_wind_available": False, "warnings": list(archive.messages),
            "provenance": {"root": str(archive.root), "format": "ERA5 viewer L137 daily NetCDF"}}


def wrf_datetimes(nc):
    if "Times" in nc.variables:
        raw = np.asarray(nc.variables["Times"][:])
        strings = []
        for row in raw:
            if np.ndim(row) == 0:
                value = row.item()
                s = value.decode() if isinstance(value, bytes) else str(value)
            else:
                s = row.tobytes().decode() if row.dtype.kind == "S" else "".join(map(str, row))
            strings.append(datetime.fromisoformat(s.strip("\x00 ").replace("_", "T")).replace(tzinfo=timezone.utc))
        return strings
    if "XTIME" in nc.variables:
        var = nc.variables["XTIME"]
        dates = num2date(var[:], str(var.units).replace("_", "T"), only_use_cftime_datetimes=False)
        return [datetime(d.year,d.month,d.day,d.hour,d.minute,d.second,tzinfo=timezone.utc) for d in dates]
    raise PredictorError("WRF requires Times or XTIME with CF time units.")


WRF_REQUIRED = {"U", "V", "T", "P", "PB", "PH", "PHB", "XLAT", "XLONG", "HGT", "COSALPHA", "SINALPHA"}


def scan_wrf(root):
    root = Path(root)
    groups, messages = {}, []
    if not root.is_dir():
        return [], [f"WRF directory does not exist: {root}"]
    for path in sorted(root.rglob("wrfout*")):
        if not path.is_file() or not path.resolve().is_relative_to(root.resolve()):
            continue
        try:
            with Dataset(path) as nc:
                missing = WRF_REQUIRED - set(nc.variables)
                if missing:
                    raise PredictorError("Missing " + ", ".join(sorted(missing)))
                times = wrf_datetimes(nc)
                if not times:
                    continue
                match = re.search(r"(?:^|_)d(\d+)(?:_|\.)", path.name)
                domain = match.group(1) if match else str(getattr(nc, "GRID_ID", "01"))
                run = str(getattr(nc, "SIMULATION_START_DATE", getattr(nc, "START_DATE", "unspecified")))
                group_key = str(path.parent.relative_to(root)) + "|" + domain + "|" + run
                ident = "wrf-" + hashlib.sha256(group_key.encode()).hexdigest()[:12]
                folder=str(path.parent.relative_to(root))
                info = groups.setdefault(ident, {"id": ident, "label": f"WRF · d{domain} · {run}" + (f" · {folder}" if folder!="." else ""),
                    "source": "wrf", "native": True, "available": True, "root": str(root),
                    "records": [], "domain": domain, "run": run, "area": None,
                    "minimum_top_altitude": None, "maximum_altitude": None,
                    "terrain_available": True, "vertical_wind_available": True,
                    "humidity_available": True, "warnings": [],
                    "provenance": {"root": str(root), "domain": domain, "run": run,
                                   "reader": "direct native WRF columns"}})
                info["vertical_wind_available"] &= "W" in nc.variables
                info["humidity_available"] &= "QVAPOR" in nc.variables
                info["records"].extend((t.timestamp(), str(path), i) for i,t in enumerate(times))
        except (OSError, RuntimeError, ValueError, PredictorError, AttributeError) as exc:
            messages.append(f"{path.name}: {exc}")
    result = []
    for info in groups.values():
        info["records"].sort()
        if len({r[0] for r in info["records"]}) != len(info["records"]):
            info["available"] = False
            info["warnings"].append("Duplicate timestamps: keep one file per time and separate overlapping runs into separate directories.")
        info["start"] = iso(datetime.fromtimestamp(info["records"][0][0], timezone.utc))
        info["end"] = iso(datetime.fromtimestamp(info["records"][-1][0], timezone.utc))
        info["dates"] = sorted({datetime.fromtimestamp(r[0], timezone.utc).date().isoformat() for r in info["records"]})
        info["time_bounds_exact"] = True
        info["file_count"] = len({r[1] for r in info["records"]})
        result.append(info)
    return result, messages


class WRFWeather(NativeWeather):
    def __init__(self, info, cache, max_gap):
        if not info["available"]:
            raise PredictorError(" ".join(info["warnings"]))
        self.info = {k:v for k,v in info.items() if k != "records"}
        self.info["warnings"]=list(info["warnings"])
        self.info["provenance"]={**info["provenance"],"files":sorted({Path(r[1]).name for r in info["records"]})}
        self.records, self.cache, self.max_gap = info["records"], cache, max_gap
        self.times = np.array([r[0] for r in self.records])
        self.checked_grids = set()
        _, path, ti = self.records[0]
        with Dataset(path) as nc:
            def grid(name):
                var = nc.variables[name]
                return np.asarray(var[ti] if "Time" in var.dimensions else var[:], dtype=float)
            lat, lon, self.ground = grid("XLAT"), (grid("XLONG")+180)%360-180, grid("HGT")
        if lat.ndim != 2 or min(lat.shape) < 2 or not np.all(np.isfinite(lat)) or not np.all(np.isfinite(lon)):
            raise PredictorError("WRF needs finite latitude/longitude on a fixed 2D grid.")
        self.setup_grid(lat, lon)
        self.info["area"] = [float(lat.max()), float(lon.min()), float(lat.min()), float(lon.max())]
        self.info["warnings"] += ["WRF winds are destaggered and rotated to earth coordinates. Terrain and mass-level heights are native WRF metres.",
                                  "Parachute descent uses dry density; humidity is used for the sounding."]

    def raw_column(self, ti, y, x):
        _, path, index = self.records[ti]
        stat = Path(path).stat()
        key = ("wrf", path, stat.st_mtime_ns, stat.st_size, index, y, x)
        cached = self.cache.get(key)
        if cached is not None:
            return cached
        with Dataset(path) as nc:
            check = (path, stat.st_mtime_ns, index)
            if check not in self.checked_grids:
                # Small 2-D geometry checks reject moving nests before using cached weights.
                def grid(name):
                    var = nc.variables[name]
                    return np.asarray(var[index] if "Time" in var.dimensions else var[:], dtype=float)
                lat, lon = grid("XLAT"), (grid("XLONG")+180)%360-180
                if (lat.shape != self.lat.shape or not np.allclose(lat,self.lat,atol=1e-5,rtol=0)
                    or not np.allclose(lon,self.lon,atol=1e-5,rtol=0) or not np.allclose(grid("HGT"),self.ground,atol=.01,rtol=0)):
                    raise PredictorError("WRF grid or terrain changes. Moving nests and mixed grids are unsupported; select one fixed domain/run.")
                self.checked_grids.add(check)
            def column(name, dx=False, dy=False):
                var = nc.variables[name]
                sel = []
                for dim,size in zip(var.dimensions,var.shape):
                    if dim == "Time": sel.append(index)
                    elif dim == "south_north": sel.append(y)
                    elif dim == "west_east": sel.append(x)
                    elif dim == "west_east_stag": sel.append(slice(x,x+2))
                    elif dim == "south_north_stag": sel.append(slice(y,y+2))
                    elif dim in ("bottom_top","bottom_top_stag"): sel.append(slice(None))
                    elif size == 1: sel.append(0)
                    else: raise PredictorError(f"Unsupported WRF dimension {dim}.")
                arr = np.ma.filled(var[tuple(sel)], np.nan).astype(float)
                return np.mean(arr,axis=-1) if dx or dy else arr
            u,v = column("U",dx=True),column("V",dy=True)
            ca,sa = float(column("COSALPHA")),float(column("SINALPHA"))
            if not np.isfinite(ca+sa) or abs(ca*ca+sa*sa-1) > .01:
                raise PredictorError("WRF rotation coefficients are invalid.")
            pressure = column("P")+column("PB")
            heights = (column("PH")+column("PHB"))/9.81
            fields = {"u":u*ca-v*sa,"v":v*ca+u*sa,
                      "temperature":(column("T")+300)*(pressure/100000)**(287/1004),
                      "pressure":pressure,"height":(heights[:-1]+heights[1:])/2,
                      "terrain":float(self.ground[y,x])}
            if "W" in nc.variables:
                w = column("W"); fields["w"]=(w[:-1]+w[1:])/2
            if "QVAPOR" in nc.variables:
                r=column("QVAPOR"); fields["specific_humidity"]=np.where(r>=0,r/(1+r),np.nan)
            if {"U10","V10","T2","PSFC"}.issubset(nc.variables):
                a,b=float(column("U10")),float(column("V10"))
                surface={"u":a*ca-b*sa,"v":b*ca+a*sa,"temperature":float(column("T2")),"pressure":float(column("PSFC")),"w":0}
                if "Q2" in nc.variables:
                    r=float(column("Q2")); surface["specific_humidity"]=r/(1+r) if r>=0 else np.nan
                if all(np.isfinite(v) for v in surface.values()) and surface["temperature"]>0 and surface["pressure"]>0:
                    fields["surface"]=surface
        return self.cache.put(key,fields)


class NativeCatalog:
    def __init__(self, settings):
        self.settings = settings
        self.cache = ColumnCache(settings.cache_mb)
        self.scan()

    def scan(self):
        with NETCDF_LOCK:
            self.archive = Archive(self.settings.era5_root, self.settings.cache_mb)
            self.wrf, self.messages = scan_wrf(self.settings.wrf_root)
            self.cache = ColumnCache(self.settings.cache_mb)
            self.weather = {}

    def list(self):
        result = [era5_info(self.archive)]
        result.extend({k:v for k,v in i.items() if k != "records"} for i in self.wrf)
        if not self.wrf:
            result.append({"id":"wrf","label":"WRF · local output (no runs indexed)","source":"wrf",
                           "native":True,"available":False,"root":str(self.settings.wrf_root),
                           "start":None,"end":None,"area":None,"terrain_available":True,
                           "vertical_wind_available":False,"warnings":self.messages,
                           "provenance":{"root":str(self.settings.wrf_root)}})
        return result

    def get(self, name, when=None):
        with NETCDF_LOCK:
            try:
                if name == "era5":
                    if not self.archive.dates:
                        raise PredictorError(f"No local ERA5 files found in {self.settings.era5_root}. Set era5_root in config.yml, rescan, or use Manage weather to download a dataset.")
                    if when is None:
                        first=self.archive.day(sorted(self.archive.dates)[0])["times"][0]["datetime"]
                        when=datetime.fromisoformat(first.replace("Z","+00:00"))
                    weather = ERA5Weather(self.archive, when, self.cache, self.settings.max_time_gap_hours*3600)
                    weather.time_bracket(when)
                    return weather
                info = next((i for i in self.wrf if i["id"] == name), None)
                if info is None:
                    raise PredictorError(f"No WRF run selected. Put native wrfout files in {self.settings.wrf_root}, rescan, and choose a run/domain.")
                if name not in self.weather:
                    self.weather[name] = WRFWeather(info, self.cache, self.settings.max_time_gap_hours*3600)
                weather = self.weather[name]
                if when:
                    weather.time_bracket(when)
                return weather
            except (DataError, OSError, RuntimeError) as exc:
                raise PredictorError(f"Cannot read local weather: {exc}") from exc
