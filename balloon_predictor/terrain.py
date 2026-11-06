"""Physical terrain is independent of forecast orography and vertical coordinates."""
import math
from pathlib import Path
import uuid
import threading
from collections import OrderedDict

from .errors import CoverageError, PredictorError


class TerrainCatalog:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._handles = OrderedDict()

    def list(self):
        return [{"id": p.name, "path": str(p), "bytes": p.stat().st_size}
                for p in sorted(self.root.glob("*.tif")) if p.is_file()]

    def sample(self, lat, lon, tile="auto"):
        with self._lock:
            return self._sample(lat, lon, tile)

    def _open(self, path, rasterio):
        stamp = (path.stat().st_mtime_ns, path.stat().st_size)
        cached = self._handles.pop(path, None)
        if cached and cached[0] == stamp:
            self._handles[path] = cached
            return cached[1]
        if cached:
            cached[1].close()
        try:
            ds = rasterio.open(path)
        except rasterio.errors.RasterioError as exc:
            raise PredictorError(f"Cannot read physical terrain tile {path.name}.") from exc
        while len(self._handles) >= 8:
            self._handles.popitem(last=False)[1][1].close()
        self._handles[path] = (stamp, ds)
        return ds

    def _sample(self, lat, lon, tile="auto"):
        try:
            import rasterio
            from rasterio.warp import transform
            from rasterio.windows import Window
        except ImportError as exc:
            raise PredictorError("GeoTIFF terrain needs rasterio. Reinstall the v1.3 requirements.") from exc
        paths = sorted(self.root.glob("*.tif")) if tile == "auto" else [self.root / Path(tile).name]
        if tile != "auto" and Path(tile).name != tile:
            raise PredictorError("Select a terrain tile from the catalog.")
        candidates = []
        for path in paths:
            if not path.is_file():
                continue
            ds = self._open(path, rasterio)
            if not ds.crs:
                raise PredictorError(f"Terrain tile {path.name} has no coordinate reference system.")
            xx, yy = transform("EPSG:4326", ds.crs, [lon], [lat])
            row, col = ds.index(xx[0], yy[0])
            if 0 <= row < ds.height and 0 <= col < ds.width:
                # A single native pixel keeps coastlines and nodata boundaries intact.
                val = ds.read(1, window=Window(col, row, 1, 1), masked=True)[0, 0]
                if not getattr(val, "mask", False) and math.isfinite(float(val)):
                    unit = (ds.units[0] or "m").lower()
                    if unit not in ("m", "metre", "meter", "metres", "meters"):
                        raise PredictorError(f"Terrain tile {path.name} must contain elevations in metres.")
                    px, py = ds.xy([row, row, min(row+1,ds.height-1)], [col, min(col+1,ds.width-1), col])
                    ll, pp = transform(ds.crs, "EPSG:4326", px, py)
                    def metres(i):
                        return math.hypot(((ll[i]-ll[0]+180)%360-180)*math.cos(math.radians(lat)), pp[i]-pp[0])*111195
                    resolution = max(metres(1), metres(2))
                    candidates.append((resolution, float(val), path.name))
        if not candidates:
            raise CoverageError("No valid physical DEM pixel here. Add a covering GeoTIFF tile, or choose a known launch/fixed landing elevation. Missing tiles are never assumed to be ocean.")
        _, value, name = min(candidates)
        return value, name

    def download(self, latitude, longitude, progress):
        """Fetch one 1-degree public GLO-30 tile; no global DEM download."""
        import requests
        y, x = math.floor(latitude), math.floor((longitude + 180) % 360 - 180)
        name = f"Copernicus_DSM_COG_10_{'N' if y >= 0 else 'S'}{abs(y):02d}_00_{'E' if x >= 0 else 'W'}{abs(x):03d}_00_DEM"
        target = self.root / (name + ".tif")
        if target.exists():
            return {"tile": target.name, "path": str(target), "cached": True}
        url = f"https://copernicus-dem-30m.s3.amazonaws.com/{name}/{name}.tif"
        temporary = target.with_name(uuid.uuid4().hex + ".part")
        try:
            with requests.get(url, stream=True, timeout=(20, 120)) as response:
                if response.status_code == 404:
                    raise PredictorError("No public GLO-30 tile at this location. Ocean tiles and some restricted land tiles are absent; use a local DEM or known elevation.")
                response.raise_for_status()
                count = 0
                with temporary.open("wb") as output:
                    for block in response.iter_content(1024 * 1024):
                        count += len(block)
                        if count > 300 * 1024**2:
                            raise PredictorError("Terrain tile exceeded the 300 MB limit.")
                        output.write(block)
                        progress(f"Downloading GLO-30 tile · {count / 1024**2:.1f} MB")
            import rasterio
            with rasterio.open(temporary) as ds:
                if ds.count != 1 or not ds.crs:
                    raise PredictorError("Downloaded terrain tile is not a valid elevation GeoTIFF.")
            temporary.replace(target)
        except requests.RequestException as exc:
            raise PredictorError("Could not download the public GLO-30 tile. Check the network or use a local GeoTIFF.") from exc
        finally:
            temporary.unlink(missing_ok=True)
        return {"tile": target.name, "path": str(target), "cached": False,
                "source": url, "datum": "EGM2008 orthometric metres", "type": "DSM (includes vegetation/buildings)"}
