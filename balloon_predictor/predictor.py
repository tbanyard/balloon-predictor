"""Midpoint integration with explicitly resolved burst and ground events."""
from datetime import timedelta
import math

import numpy as np

from .errors import CoverageError, PredictorError
from .weather.schema import EARTH_RADIUS

RHO_REF = 101325 / (287.05 * 288.15)


def distance_km(a, b):
    lat1, lat2 = map(math.radians, (a["latitude"], b["latitude"]))
    dlat = lat2 - lat1
    dlon = math.radians(b["longitude"] - a["longitude"])
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * EARTH_RADIUS * math.asin(min(1.0, math.sqrt(h))) / 1000


def run_flight(request, weather, wind_bias=(0, 0)):
    if request.use_vertical_wind and not weather.info.get("vertical_wind_available"):
        raise PredictorError("Vertical wind was requested, but this dataset does not provide it.")
    state = np.array([request.launch_latitude, request.launch_longitude, request.launch_altitude], dtype=float)
    elapsed = 0.0
    phase = "ascent"
    start = request.launch_datetime
    limit = request.max_duration_hours * 3600
    paths = {"ascent": [], "descent": []}

    def ground(s):
        return weather.terrain(s[0], s[1]) if request.landing_mode == "terrain" else request.landing_altitude

    launch_ground = weather.terrain(state[0], state[1]) if weather.info.get("terrain_available") else ground(state)
    if state[2] < launch_ground - 1:
        raise PredictorError(f"Launch altitude is below ground ({launch_ground:.0f} m AMSL). Increase launch altitude.")

    def velocity(s, seconds):
        if abs(s[0]) > 85:
            raise CoverageError("This integrator supports latitudes between 85°S and 85°N.")
        sample = weather.sample(s[0], s[1], s[2], start + timedelta(seconds=seconds))
        if phase == "ascent":
            vertical = request.ascent_rate
        else:
            vertical = -request.descent_rate
            if request.descent_model == "density":
                vertical *= math.sqrt(RHO_REF / sample.density)
        if request.use_vertical_wind:
            vertical += sample.w
        radius = EARTH_RADIUS + s[2]
        return np.array([(sample.v + wind_bias[1]) / radius * 180 / math.pi,
                         (sample.u + wind_bias[0]) / (radius * math.cos(math.radians(s[0]))) * 180 / math.pi,
                         vertical])

    def advance(s, seconds, dt):
        initial = velocity(s, seconds)
        middle = s + initial * dt / 2
        return s + velocity(middle, seconds + dt / 2) * dt

    def point(s, seconds):
        return {"datetime": (start + timedelta(seconds=seconds)).isoformat().replace("+00:00", "Z"),
                "latitude": float(s[0]), "longitude": float((s[1] + 180) % 360 - 180),
                "altitude": float(s[2]), "elapsed_seconds": round(float(seconds), 4)}

    paths["ascent"].append(point(state, 0))
    burst = None

    def finish(landing=None):
        launch = paths["ascent"][0]
        endpoint = landing or burst
        all_points = paths["ascent"] + paths["descent"][1:]
        return {"prediction": [{"stage": key, "trajectory": value} for key,value in paths.items() if value],
                "summary": {"launch": launch, "burst": burst, "landing": landing,
                            "endpoint": endpoint, "endpoint_kind": "landing" if landing else "target",
                            "duration_seconds": round(float(elapsed),2),
                            "ascent_seconds": burst["elapsed_seconds"],
                            "descent_seconds": round(float(elapsed-burst["elapsed_seconds"]),2),
                            "drift_km": round(distance_km(launch,endpoint),3),
                            "burst_drift_km": round(distance_km(launch,burst),3),
                            "path_length_km": round(sum(distance_km(a,b) for a,b in zip(all_points,all_points[1:])),3)}}
    while elapsed < limit - 1e-6:
        dt = min(request.time_step, limit - elapsed)
        current_v = velocity(state, elapsed)
        if phase == "ascent":
            if current_v[2] <= 0:
                raise PredictorError("Vertical wind prevents ascent. Disable vertical wind or revise ascent rate.")
            dt = min(dt, max(1e-4, (request.burst_altitude - state[2]) / current_v[2]))
        else:
            if current_v[2] >= 0:
                raise PredictorError("Vertical wind prevents descent. Disable vertical wind or revise descent rate.")
            # Prevent the midpoint from sampling far below the surface.
            remaining = state[2] - ground(state)
            if remaining > 0:
                dt = min(dt, max(1e-4, 1.5 * remaining / -current_v[2]))
        proposed = advance(state, elapsed, dt)
        event = (proposed[2] >= request.burst_altitude - 1e-5 if phase == "ascent"
                 else proposed[2] <= ground(proposed))
        if event:
            lo, hi = 0.0, dt
            for _ in range(30):
                mid = (lo + hi) / 2
                trial = advance(state, elapsed, mid)
                crossed = trial[2] >= request.burst_altitude if phase == "ascent" else trial[2] <= ground(trial)
                if crossed:
                    hi = mid
                else:
                    lo = mid
            dt = hi
            proposed = advance(state, elapsed, dt)
            proposed[2] = request.burst_altitude if phase == "ascent" else ground(proposed)
        # The endpoint must also be covered, even when the last midpoint is covered.
        weather.sample(proposed[0], proposed[1], proposed[2], start + timedelta(seconds=float(elapsed + dt)))
        elapsed += dt
        state = proposed
        paths[phase].append(point(state, elapsed))
        if event:
            if phase == "ascent":
                burst = paths["ascent"][-1]
                if request.flight_mode == "ascent":
                    return finish()
                phase = "descent"
                paths["descent"].append(dict(burst))
            else:
                return finish(paths["descent"][-1])
    raise PredictorError("Flight did not reach its endpoint within the maximum duration. Increase it or revise flight rates.")


def predict(request, weather, progress=None):
    result = run_flight(request, weather)
    result["request"] = request.model_dump(mode="json")
    result["weather"] = weather.info
    result["warnings"] = list(weather.info.get("warnings", []))
    result["warnings"].append("Below the lowest represented wind level, the nearest wind is held constant.")
    if request.flight_mode == "full" and request.descent_model == "density":
        result["warnings"].append("Descent uses a dry-air density-scaled terminal speed; descent rate is referenced to ISA sea level.")
    if weather.info["source"] == "ifs":
        result["warnings"].append("IFS open-data pressure levels are sparse in the stratosphere; check vertical resolution for high flights.")
    result["ensemble"] = []
    if request.flight_mode == "ascent":
        result["warnings"].append("Ascent-only flight: the endpoint is the requested target altitude. No landing is calculated.")
    if request.ensemble_members:
        rng = np.random.default_rng(request.seed)
        failed = []
        for i in range(request.ensemble_members):
            if progress:
                progress(f"Running sensitivity member {i + 1}/{request.ensemble_members}")
            updates = {"ascent_rate": max(0.05, rng.normal(request.ascent_rate, request.ascent_sigma)),
                       "descent_rate": max(0.05, rng.normal(request.descent_rate, request.descent_sigma)),
                       "burst_altitude": float(np.clip(rng.normal(request.burst_altitude, request.burst_sigma),
                                                        request.launch_altitude + 1, 55000)),
                       "ensemble_members": 0}
            member = request.model_copy(update=updates)
            try:
                flight = run_flight(member, weather, rng.normal(0, request.wind_sigma, size=2))
                result["ensemble"].append({"member": i + 1, **flight})
            except PredictorError as exc:
                failed.append({"member": i + 1, "reason": str(exc)})
        result["ensemble_failed"] = failed
        result["warnings"].append("Sensitivity members perturb balloon parameters and apply a constant wind bias; they are not a meteorological ensemble or calibrated probability.")
    from .profiles import flight_sounding
    try:
        result["sounding"] = flight_sounding(result, weather)
    except PredictorError as exc:
        result["sounding"] = None
        result["warnings"].append("Sounding unavailable: " + str(exc))
    return result


def predict_batch(request, store, progress=None):
    """An end-exclusive launch window; preserve successes and report each failure."""
    import math
    count = math.ceil(request.duration_hours/request.interval_hours - 1e-10)
    entries = []
    total = count * len(request.flights)
    for step in range(count):
        for site, template in enumerate(request.flights):
            launch = template.launch_datetime + timedelta(hours=step*request.interval_hours)
            body = template.model_copy(update={"launch_datetime": launch})
            if progress:
                progress(f"Launch {len(entries)+1}/{total} · {template.flight_name} · {launch:%Y-%m-%d %H:%M} UTC")
            entry = {"index":len(entries),"site_index":site,"flight_name":template.flight_name,
                     "launch_datetime":launch.isoformat().replace("+00:00","Z")}
            try:
                entry.update(status="completed", result=predict(body,store.get(body.dataset,launch)))
            except PredictorError as exc:
                entry.update(status="failed",error=str(exc))
            entries.append(entry)
    return {"type":"series","request":request.model_dump(mode="json"),"flights":entries,
            "completed":sum(e["status"]=="completed" for e in entries),
            "failed":sum(e["status"]=="failed" for e in entries),"total":total,
            "window":"Launches from start inclusive to start + duration exclusive; coverage is checked for every full flight."}
