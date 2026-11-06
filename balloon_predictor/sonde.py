"""Model-derived trajectories in the structure of the supplied NCAS sonde file.

Only the data-free schema is distributed. No observations or personal metadata
from the reference launch are embedded in generated files.
"""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import numpy as np
from netCDF4 import Dataset
from scipy.signal import savgol_filter

from . import __version__
from .errors import PredictorError
from .profiles import dewpoint_c
from .weather.io import NETCDF_LOCK
from .weather.schema import EARTH_RADIUS, R_D

SCHEMA_PATH = Path(__file__).with_name("sonde_schema.json")


def bearing(lat1, lon1, lat2, lon2):
    a, b = np.radians(lat1), np.radians(lat2)
    delta = np.radians(np.asarray(lon2) - lon1)
    return np.degrees(np.arctan2(np.sin(delta)*np.cos(b),
        np.cos(a)*np.sin(b)-np.sin(a)*np.cos(b)*np.cos(delta))) % 360


def sampled_track(result, scope="ascent"):
    if scope not in ("ascent", "full"):
        raise PredictorError("Sonde scope must be ascent or full.")
    points = []
    for stage in result["prediction"]:
        if scope == "ascent" and stage["stage"] != "ascent":
            continue
        for point in stage["trajectory"]:
            if not points or point["elapsed_seconds"] > points[-1]["elapsed_seconds"]:
                points.append(point)
    if len(points) < 2:
        raise PredictorError("At least two trajectory points are required for sonde export.")
    elapsed = np.array([p["elapsed_seconds"] for p in points], dtype=float)
    # The reference uses int64 seconds and a 1 Hz cadence. Preserve that contract
    # rather than rounding an exact fractional endpoint to an incorrect timestamp.
    seconds = np.arange(int(np.floor(elapsed[-1] + 1e-7)) + 1, dtype="int64")
    if len(seconds) < 2:
        raise PredictorError("The flight must last at least one second for the 1 Hz sonde format.")
    latitude = np.interp(seconds, elapsed, [p["latitude"] for p in points])
    longitude = np.degrees(np.interp(seconds, elapsed, np.unwrap(np.radians([p["longitude"] for p in points]))))
    longitude = (longitude + 180) % 360 - 180
    altitude = np.interp(seconds, elapsed, [p["altitude"] for p in points])
    return seconds, latitude, longitude, altitude


def write_sonde(path, result, weather, scope="ascent"):
    from .context import flight_weather
    from .models import PredictionRequest
    weather = flight_weather(weather, PredictionRequest.model_validate(result["request"]))
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    seconds, lat, lon, alt = sampled_track(result, scope)
    start = datetime.fromisoformat(result["summary"]["launch"]["datetime"].replace("Z", "+00:00"))
    count = len(seconds)
    fields = {name: np.full(count, np.nan, dtype="float64") for name in schema["variables"] if name != "time"}
    fields.update(timePeriod=seconds.astype("float64"), lat=lat, lon=lon, alt=alt)
    for i, second in enumerate(seconds):
        sample = weather.sample(float(lat[i]), float(lon[i]), float(alt[i]), start + timedelta(seconds=int(second)))
        fields["pressure"][i] = sample.pressure / 100
        fields["temp"][i] = sample.temperature
        fields["u"][i], fields["v"][i] = sample.u, sample.v
        q = sample.specific_humidity
        if q is not None and np.isfinite(q) and 0 <= q < 1:
            fields["specific_humidity"][i] = q
            dew = dewpoint_c(sample.pressure, q)
            fields["dewp"][i] = dew + 273.15 if dew is not None else np.nan
            vapor = sample.pressure*q/(.622 + .378*q)
            tc = sample.temperature-273.15
            fields["relative_humidity"][i] = 100*vapor/(611.2*np.exp(17.67*tc/(tc+243.5)))
    fields["vel_h"] = np.hypot(fields["u"], fields["v"])
    fields["windDir"] = np.where(fields["vel_h"] > 1e-10, np.degrees(np.arctan2(-fields["u"], -fields["v"])) % 360, np.nan)
    fields["PotTemp"] = fields["temp"] * (1000 / fields["pressure"]) ** (R_D / 1004.)
    fields["vel_z"] = np.gradient(alt, seconds)
    window = min(301, count if count % 2 else count-1)
    fields["vel_z_smoothed"] = savgol_filter(fields["vel_z"], window, 2) if window >= 3 else fields["vel_z"].copy()
    a, b = np.radians(lat[0]), np.radians(lat)
    delta = np.radians(lon-lon[0])
    hav = np.sin((b-a)/2)**2 + np.cos(a)*np.cos(b)*np.sin(delta/2)**2
    fields["CompRng"] = 2*EARTH_RADIUS*np.arcsin(np.sqrt(np.clip(hav, 0, 1)))
    fields["CompAz"] = bearing(lat[0], lon[0], lat, lon)
    fields["CompAz"][0] = np.nan
    fields["heading"][1:] = bearing(lat[:-1], lon[:-1], lat[1:], lon[1:])
    still = np.hypot(np.diff(lat), ((np.diff(lon)+180) % 360-180)) < 1e-12
    fields["heading"][1:][still] = np.nan
    provenance = result["weather"].get("provenance", {})
    created = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    clock = start.strftime("%H:%M:%S")
    date = start.strftime("%Y-%m-%d")
    source = result["weather"].get("label", result["request"]["dataset"])
    launch = result["summary"]["launch"]
    ground = weather.launch_ground()
    ground = float("nan") if ground is None else ground
    values = {key: "" for key in schema["global_attributes"]}
    values.update(Conventions="CF-1.13", title="Model-derived simulated radiosonde trajectory",
        project="BalloonPredictor model simulation", source="SIMULATION: " + source,
        history=f"{created}: BalloonPredictor {__version__}; 1 Hz positions interpolated from integrated trajectory; atmosphere sampled at each position and UTC time.",
        processing_level=0, acknowledgement="Model data attribution: " + str(provenance.get("attribution", source)),
        institution="User-generated model simulation", date_created=created, featureType="trajectory",
        site_name=result["request"].get("flight_name", "Simulated launch"), serial="SIMULATED",
        startdate=date, starttime=clock, originalFile=result["request"]["dataset"],
        balloonreleasetime_nph=clock, balloonreleasedate_nph=date,
        originalFileCreationDate_nph=str(provenance.get("cycle", created)),
        originalFile_nph=result["request"]["dataset"], softwareVersion=__version__,
        heightOfStationGroundAboveMeanSeaLevel=ground, startLatitude=lat[0], startLongitude=lon[0],
        surfaceTemperature_C=fields["temp"][0]-273.15, surfaceWindDir=fields["windDir"][0],
        surfaceHumidity=fields["relative_humidity"][0], surfacePressure=fields["pressure"][0],
        launch_time=start.isoformat().replace("+00:00", "Z"))
    with NETCDF_LOCK, Dataset(path, "w", format=schema["format"]) as ds:
        ds.createDimension("time", count)
        for name, spec in schema["global_attributes"].items():
            value = values[name]
            if spec["dtype"] != "str":
                # Empty numeric metadata is represented as missing, never an old observation.
                value = np.asarray(np.nan if value == "" else value, dtype=spec["dtype"]).item()
                value = np.asarray(value, dtype=spec["dtype"])[()]
            else:
                value = str(value)
            ds.setncattr(name, value)
        ds.setncatts({"simulation": "true", "model_provenance": json.dumps(provenance, sort_keys=True),
            "below_model_approximation_json": json.dumps(result["weather"].get("below_model", {})),
            "physical_ground_json": json.dumps(result["weather"].get("physical_ground", {})),
            "sonde_resampling_below_model_json": json.dumps(weather.bridge),
            "trajectory_scope": scope, "sampling_interval_seconds": 1.,
            "sampling_note": "Regular 1 Hz samples through the last complete second. A fractional terminal second is omitted; the exact integrated endpoint is recorded in exact_endpoint_json.",
            "exact_endpoint_json": json.dumps(result["summary"]["burst"] if scope == "ascent" else result["summary"]["endpoint"]),
            "missing_data_note": "NaN denotes unavailable model humidity or observational turbulence/noise quantities. No original sonde measurements are copied.",
            "time_coverage_start": start.isoformat().replace("+00:00", "Z"),
            "time_coverage_end": (start+timedelta(seconds=int(seconds[-1]))).isoformat().replace("+00:00", "Z")})
        for name, spec in schema["variables"].items():
            kwargs = {"fill_value": np.nan} if spec["nan_fill"] else {"fill_value": False}
            variable = ds.createVariable(name, spec["dtype"], tuple(spec["dimensions"]), **kwargs)
            attrs = dict(spec["attributes"])
            if name == "time":
                attrs["units"] = "seconds since " + start.replace(tzinfo=None).isoformat(sep=" ")
            if name in ("vel_z_prime", "vel_z_prime_smoothed"):
                attrs["description"] = "Unavailable: the trajectory model does not resolve observed sensor noise, turbulence or gravity-wave perturbations. Filled with NaN."
            if name == "vel_z_smoothed":
                attrs["description"] = "Simulated geometric ascent/descent velocity, Savitzky-Golay smoothed (up to 301 one-second samples, polyorder=2). This is not an observed buoyancy estimate."
            variable.setncatts(attrs)
            variable[:] = seconds if name == "time" else fields[name]
    return Path(path)
