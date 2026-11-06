"""Per-flight lower-atmosphere policy without mutating shared model data."""
import copy
import math
import numpy as np

from .errors import CoverageError, PredictorError
from .weather.interpolate import DatasetWeather


class FlightWeather:
    def __init__(self, base, request):
        self.base = base
        self.request = request
        self.info = copy.deepcopy(base.info)
        self.bridge = {"policy": request.below_model_policy, "method": "Lowest valid wind and humidity held; temperature uses -6.5 K/km; pressure integrated hydrostatically with that lapse rate. Model heights are unchanged.",
                       "maximum_depth_m": 0.0, "sample_calls": 0, "first_datetime": None, "last_datetime": None}
        self.model = copy.copy(base)
        self.model.info = self.info
        self.model.source_receipts = dict(getattr(base, "source_receipts", {}))
        if isinstance(base, DatasetWeather):
            original = type(base)._column.__get__(self.model)
            def column(ti, y, x, altitude):
                if hasattr(self.model, "raw_column"):
                    raw = self.model.raw_column(ti, y, x)
                    h = np.asarray(raw["height"], float)
                    valid = np.isfinite(h) & (h > raw.get("terrain", -np.inf) + .1)
                    for key in ("u", "v", "temperature", "pressure"):
                        valid &= np.isfinite(raw[key])
                    valid &= (np.asarray(raw["temperature"]) > 0) & (np.asarray(raw["pressure"]) > 0)
                    surface = raw.get("surface")
                    anchor = float(np.min(h[valid])) if valid.any() else math.inf
                    if surface and anchor > raw["terrain"] + 10:
                        anchor = float(raw["terrain"]) + 10
                else:
                    h = self.model.fields["height"][ti, :, y, x]
                    terrain = self.model.ground[y, x] if self.model.ground is not None else -np.inf
                    valid = np.isfinite(h) & (h > terrain + .1)
                    for key in ("u", "v", "temperature", "pressure"):
                        valid &= np.isfinite(self.model.fields[key][ti, :, y, x])
                    valid &= (self.model.fields["temperature"][ti, :, y, x] > 0) & (self.model.fields["pressure"][ti, :, y, x] > 0)
                    surface = self.model.surface
                    if "surface_pressure" in surface:
                        valid &= self.model.fields["pressure"][ti, :, y, x] <= surface["surface_pressure"][ti, y, x]
                    anchor = float(np.min(h[valid])) if valid.any() else math.inf
                    keys = ("surface_u", "surface_v", "surface_temperature", "surface_pressure")
                    if math.isfinite(terrain) and all(k in surface and math.isfinite(float(surface[k][ti,y,x])) for k in keys) and surface["surface_temperature"][ti,y,x] > 0 and surface["surface_pressure"][ti,y,x] > 0 and np.any(h[valid] > terrain + 10.1):
                        anchor = float(terrain) + 10
                if not math.isfinite(anchor):
                    return original(ti, y, x, altitude)  # Existing detailed coverage error.
                if altitude >= anchor - 1e-6:
                    return original(ti, y, x, altitude)
                depth = float(anchor - altitude)
                if request.below_model_policy == "strict":
                    raise CoverageError(f"Strict ground policy: {altitude:.1f} m is below the lowest valid model air at {anchor:.1f} m AMSL. Choose “Estimate the missing near-surface air” or use a model with better vertical/terrain resolution.")
                if depth > 3000:
                    raise CoverageError("Estimating the missing near-surface air would extend more than 3 km below the model’s lowest valid air. Use a model that resolves this launch site.")
                values = original(ti, y, x, anchor)
                temperature = values["temperature"] + .0065 * depth
                values["pressure"] *= (temperature / values["temperature"]) ** (9.80665 / (287.05 * .0065))
                values["temperature"] = temperature
                self.bridge["maximum_depth_m"] = max(self.bridge["maximum_depth_m"], float(depth))
                self.bridge["sample_calls"] += 1
                return values
            self.model._column = column
        self.terrain_catalog = getattr(base, "terrain_catalog", None)
        self.info["physical_ground"] = {"source": request.ground_source, "known_elevation_m": request.ground_altitude,
                                       "dem": request.dem_id, "landing_mode": request.landing_mode,
                                       "vertical_reference": "AMSL / orthometric metres; no ellipsoid conversion"}
        self.info["below_model"] = self.bridge

    def sample(self, lat, lon, altitude, when):
        previous = self.bridge["sample_calls"]
        result = self.model.sample(lat, lon, altitude, when)
        if self.bridge["sample_calls"] > previous:
            stamp = when.isoformat()
            first, last = self.bridge["first_datetime"], self.bridge["last_datetime"]
            self.bridge["first_datetime"] = min(first, stamp) if first else stamp
            self.bridge["last_datetime"] = max(last, stamp) if last else stamp
        return result

    def terrain(self, lat, lon):
        return self.base.terrain(lat, lon)

    def dem(self, lat, lon):
        if not self.terrain_catalog:
            raise PredictorError("Physical DEM catalog is not configured.")
        value, tile = self.terrain_catalog.sample(lat, lon, self.request.dem_id)
        from .provenance import record_file
        record_file(self.model, self.terrain_catalog.root / tile)
        used = self.info["physical_ground"].setdefault("tiles", [])
        if tile not in used:
            used.append(tile)
        return value

    def launch_ground(self):
        req = self.request
        lat, lon = req.launch_latitude, req.launch_longitude
        if req.ground_source == "dem":
            return self.dem(lat, lon)
        if req.ground_source == "model":
            return self.terrain(lat, lon)
        return req.ground_altitude


def flight_weather(base, request):
    return base if isinstance(base, FlightWeather) else FlightWeather(base, request)
