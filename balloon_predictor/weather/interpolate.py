"""Interpolate native columns before combining space and time."""
from dataclasses import dataclass
from datetime import datetime, timezone
import math

import numpy as np
from scipy.spatial import Delaunay, QhullError

from ..errors import CoverageError, PredictorError
from .schema import R_D


@dataclass(frozen=True)
class Sample:
    u: float
    v: float
    temperature: float
    pressure: float
    w: float = 0
    specific_humidity: float | None = None

    @property
    def density(self):
        return self.pressure / (R_D * self.temperature)


def standard_atmosphere(altitude):
    """Dry ISA; used only in the explicitly synthetic demo."""
    h = max(-500, altitude)
    bases = [0, 11000, 20000, 32000, 47000, 51000, 71000]
    lapses = [-0.0065, 0, 0.001, 0.0028, 0, -0.0028, -0.002]
    t, p = 288.15, 101325.0
    for k, base in enumerate(bases):
        stop = bases[k + 1] if k + 1 < len(bases) else 86000
        dz = min(h, stop) - base
        lapse = lapses[k]
        next_t = t + lapse * dz
        p *= math.exp(-9.80665 * dz / (R_D * t)) if not lapse else (next_t / t) ** (-9.80665 / (R_D * lapse))
        t = next_t
        if h <= stop:
            return t, p
    return t, p


class DemoWeather:
    info = {"id": "demo", "label": "Demo · synthetic winds", "source": "demo", "start": None, "end": None,
            "area": [85, -180, -85, 180], "minimum_top_altitude": 55000,
            "maximum_altitude": 55000, "terrain_available": True, "vertical_wind_available": True,
            "warnings": ["Synthetic demo weather. This is not a real flight forecast."],
            "provenance": {"description": "Analytical winds and dry ISA; flat ground at 0 m."}}

    def sample(self, lat, lon, altitude, when):
        if abs(lat) > 85 or not (-500 <= altitude <= 55000):
            raise CoverageError("Outside demo coverage.")
        t, p = standard_atmosphere(altitude)
        phase = when.timestamp() / 3600 * math.pi / 12
        u = 6 + 24 * math.exp(-((altitude - 11000) / 5500) ** 2) + 3 * math.sin(phase)
        v = 4 * math.sin(altitude / 7000) + 2 * math.cos(phase)
        return Sample(u, v, t, p, 0.1 * math.sin(altitude / 3000))

    def terrain(self, lat, lon):
        return 0.0


class DatasetWeather:
    def __init__(self, ds, info):
        self.info = info
        self.times = ds.time.values.astype("datetime64[ns]").astype("int64") / 1e9
        self.fields = {name: np.asarray(ds[name].values) for name in
                       ("u", "v", "height", "temperature", "pressure", "w", "specific_humidity") if name in ds}
        self.surface = {name: np.asarray(ds[name].values) for name in
                        ("surface_u", "surface_v", "surface_temperature", "surface_pressure") if name in ds}
        self.ground = np.asarray(ds.terrain.values) if "terrain" in ds else None
        self.setup_grid(ds.latitude.values, ds.longitude.values)

    def setup_grid(self, latitude, longitude):
        """Shared geometry for in-memory and file-backed column readers."""
        self.lat = np.asarray(latitude)
        self.lon = np.asarray(longitude)
        self.ny, self.nx = self.lat.shape
        self.regular = bool(np.allclose(self.lat, self.lat[:, :1]) and np.allclose(self.lon, self.lon[:1, :]))
        if self.regular:
            self.lats = self.lat[:, 0]
            self.lons = np.degrees(np.unwrap(np.radians(self.lon[0])))
            if np.any(np.diff(self.lats) <= 0) or np.any(np.diff(self.lons) <= 0):
                raise PredictorError("Regular weather axes must be sorted ascending during import.")
            self.periodic = self.lons[-1] - self.lons[0] >= 360 - 1.5 * np.median(np.diff(self.lons))
        else:
            self.reference_lon = float(np.degrees(np.angle(np.mean(np.exp(1j * np.radians(self.lon))))))
            local_lon = self.reference_lon + (self.lon - self.reference_lon + 180) % 360 - 180
            self.scale = math.cos(math.radians(float(np.mean(self.lat))))
            points = np.column_stack([local_lon.ravel() * self.scale, self.lat.ravel()])
            try:
                self.tri = Delaunay(points)
            except QhullError as e:
                raise PredictorError("Cannot triangulate the WRF horizontal grid.") from e
            ys, xs = np.divmod(self.tri.simplices, self.nx)
            # Delaunay can fill the convex hull outside a curved native boundary.
            self.valid_triangles = (np.ptp(ys, axis=1) <= 1) & (np.ptp(xs, axis=1) <= 1)

    @staticmethod
    def _bracket(axis, value, what):
        if value < axis[0] - 1e-7 or value > axis[-1] + 1e-7:
            raise CoverageError(f"{what} is outside weather coverage. Import a larger domain or time window.")
        value = float(np.clip(value, axis[0], axis[-1]))
        if len(axis) == 1:
            return 0, 0, 0.0
        right = min(max(int(np.searchsorted(axis, value, side="right")), 1), len(axis) - 1)
        left = right - 1
        return left, right, (value - axis[left]) / (axis[right] - axis[left])

    def spatial_weights(self, lat, lon):
        if self.regular:
            y0, y1, fy = self._bracket(self.lats, lat, "Latitude")
            if self.periodic:
                lon = self.lons[0] + (lon - self.lons[0]) % 360
                if lon > self.lons[-1]:
                    x0, x1 = self.nx - 1, 0
                    fx = (lon - self.lons[-1]) / (self.lons[0] + 360 - self.lons[-1])
                else:
                    x0, x1, fx = self._bracket(self.lons, lon, "Longitude")
            else:
                centre = (self.lons[0] + self.lons[-1]) / 2
                lon = centre + (lon - centre + 180) % 360 - 180
                x0, x1, fx = self._bracket(self.lons, lon, "Longitude")
            result = [(y0, x0, (1 - fy) * (1 - fx)), (y0, x1, (1 - fy) * fx),
                      (y1, x0, fy * (1 - fx)), (y1, x1, fy * fx)]
            return [(y, x, float(w)) for y, x, w in result if w > 1e-12]
        lon = self.reference_lon + (lon - self.reference_lon + 180) % 360 - 180
        point = np.array([lon * self.scale, lat])
        simplex = int(self.tri.find_simplex(point))
        if simplex < 0 or not self.valid_triangles[simplex]:
            raise CoverageError("Flight has left the native WRF domain. Import a larger nest/domain.")
        transform = self.tri.transform[simplex]
        bary = transform[:2].dot(point - transform[2])
        bary = np.append(bary, 1 - bary.sum())
        return [(int(i // self.nx), int(i % self.nx), float(w))
                for i, w in zip(self.tri.simplices[simplex], bary) if w > 1e-12]

    def terrain(self, lat, lon):
        if self.ground is None:
            raise PredictorError("This dataset has no terrain. Select fixed landing altitude or re-import with surface data.")
        ground = sum(float(self.ground[y, x]) * w for y, x, w in self.spatial_weights(lat, lon))
        if not math.isfinite(ground):
            raise CoverageError("Terrain is missing at this location.")
        return ground

    def _column(self, ti, y, x, altitude):
        h = self.fields["height"][ti, :, y, x].astype("float64")
        values = {name: arr[ti, :, y, x].astype("float64") for name, arr in self.fields.items() if name != "height"}
        terrain = float(self.ground[y, x]) if self.ground is not None else -np.inf
        good = np.isfinite(h) & (h > terrain + 0.1)
        for name in ("u", "v", "temperature", "pressure"):
            good &= np.isfinite(values[name])
        good &= (values["temperature"] > 0) & (values["pressure"] > 0)
        if "w" in values:
            good &= np.isfinite(values["w"])
        h = h[good]
        values = {name: value[good] for name, value in values.items()}
        if len(h) < 2:
            raise CoverageError("Fewer than two valid atmospheric levels above terrain.")
        order = np.argsort(h)
        h = h[order]
        values = {name: value[order] for name, value in values.items()}
        unique = np.r_[True, np.diff(h) > 1e-4]
        h = h[unique]
        values = {name: value[unique] for name, value in values.items()}
        if altitude > h[-1] + 0.5:
            raise CoverageError(f"Altitude {altitude:.0f} m exceeds available model top ({h[-1]:.0f} m here).")
        surface_keys = {"u": "surface_u", "v": "surface_v", "temperature": "surface_temperature", "pressure": "surface_pressure"}
        if math.isfinite(terrain) and all(key in self.surface for key in surface_keys.values()):
            surface_values = {key: float(self.surface[field][ti, y, x]) for key, field in surface_keys.items()}
            if all(math.isfinite(v) for v in surface_values.values()) and surface_values["pressure"] > 0 and surface_values["temperature"] > 0:
                surface_height = terrain + 10
                above = h > surface_height + 0.1
                if np.any(above):
                    h = np.r_[surface_height, h[above]]
                    values = {key: np.r_[surface_values.get(key, 0), val[above]] for key, val in values.items()}
        # Below the lowest represented wind, hold that wind (usually the 10 m field).
        result = {key: float(np.exp(np.interp(altitude, h, np.log(val)))) if key == "pressure"
                  else float(np.interp(altitude, h, val)) for key, val in values.items()}
        return result

    def sample(self, lat, lon, altitude, when):
        t = when.timestamp()
        t0, t1, fraction = self._bracket(self.times, t, "Time")
        weights = self.spatial_weights(lat, lon)
        blended = {}
        humidity_weight = 0.0
        time_weights = [(t0, 1.0)] if t0 == t1 or fraction < 1e-12 else [(t1, 1.0)] if fraction > 1 - 1e-12 else [(t0, 1 - fraction), (t1, fraction)]
        for ti, tw in time_weights:
            for y, x, sw in weights:
                for key, value in self._column(ti, y, x, altitude).items():
                    if key == "specific_humidity":
                        if not math.isfinite(value) or not 0 <= value < 1:
                            continue
                        humidity_weight += tw * sw
                    blended[key] = blended.get(key, 0.0) + value * tw * sw
        return Sample(blended["u"], blended["v"], blended["temperature"], blended["pressure"],
                      blended.get("w", 0.0), blended.get("specific_humidity") if humidity_weight > 1-1e-9 else None)
