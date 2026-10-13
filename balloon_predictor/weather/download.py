"""Explicit downloads with reusable, atomic raw-file caches."""
from collections import defaultdict
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path

from filelock import FileLock
import numpy as np

from ..errors import PredictorError
from .readers import read_grib

ERA5_LEVELS = [1, 2, 3, 5, 7, 10, 20, 30, 50, 70, 100, 125, 150, 175, 200, 225,
               250, 300, 350, 400, 450, 500, 550, 600, 650, 700, 750, 775, 800, 825,
               850, 875, 900, 925, 950, 975, 1000]
IFS_LEVELS = [10, 50, 100, 150, 200, 250, 300, 400, 500, 600, 700, 850, 925, 1000]


def as_np_time(value):
    return np.datetime64(value.astimezone(timezone.utc).replace(tzinfo=None), "ns")


def hourly_window(start, end):
    first = start.replace(minute=0, second=0, microsecond=0)
    last = end.replace(minute=0, second=0, microsecond=0)
    if end > last:
        last += timedelta(hours=1)
    return first, last


def cached_retrieve(cache_dir, signature, retrieve, refresh=False):
    digest = hashlib.sha256(json.dumps(signature, sort_keys=True).encode()).hexdigest()
    path = Path(cache_dir) / (digest + ".grib")
    path.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(str(path) + ".lock", timeout=3600):
        if path.exists() and path.stat().st_size > 0 and not refresh:
            return path
        part = path.with_suffix(".part")
        try:
            retrieve(part)
            if not part.exists() or part.stat().st_size == 0:
                raise PredictorError("Weather service returned an empty file.")
            os.replace(part, path)
            path.with_suffix(".request.json").write_text(json.dumps(signature, indent=2), encoding="utf-8")
        finally:
            part.unlink(missing_ok=True)
    return path


def download_era5(request, settings, progress):
    now = datetime.now(timezone.utc)
    first, last = hourly_window(request.start, request.end)
    # Availability can vary by day; the remote service remains authoritative.
    if last.date() > (now - timedelta(days=5)).date():
        raise PredictorError("ERA5/ERA5T is retrospective, usually at least five days behind real time. For future launches select ECMWF operational (IFS).")
    try:
        import cdsapi
    except ImportError as exc:
        raise PredictorError("Install CDS support: pip install -e '.[weather]'.") from exc
    key = os.environ.get("CDSAPI_KEY") or os.environ.get("CDS_API_KEY")
    kwargs = {"quiet": True, "progress": False, "timeout": 60, "retry_max": 3, "sleep_max": 10}
    if key:
        kwargs.update(key=key, url=os.environ.get("CDSAPI_URL", "https://cds.climate.copernicus.eu/api"))
    try:
        client = cdsapi.Client(**kwargs)
    except Exception as exc:
        raise PredictorError("CDS credentials could not be loaded. Create ~/.cdsapirc as described in README.md, or set CDSAPI_URL/CDSAPI_KEY.") from exc
    days = defaultdict(list)
    current = first
    while current <= last:
        days[current.date()].append(current.strftime("%H:%M"))
        current += timedelta(hours=1)
    files = []
    for day, hours in sorted(days.items()):
        common = {"product_type": ["reanalysis"], "year": [str(day.year)], "month": [f"{day.month:02d}"],
                  "day": [f"{day.day:02d}"], "time": hours, "data_format": "grib",
                  "area": list(request.area), "grid": [request.grid, request.grid]}
        jobs = [("reanalysis-era5-pressure-levels",
                 {**common, "pressure_level": [str(p) for p in ERA5_LEVELS],
                  "variable": ["u_component_of_wind", "v_component_of_wind", "temperature", "geopotential", "vertical_velocity"]})]
        if request.surface:
            jobs.append(("reanalysis-era5-single-levels", {**common, "variable": [
                "10m_u_component_of_wind", "10m_v_component_of_wind", "2m_temperature", "surface_pressure", "geopotential"]}))
        for dataset, selection in jobs:
            progress(f"Retrieving {day} · {dataset}. CDS may queue this request; predictions reuse the local cache.")
            def retrieve(target, dataset=dataset, selection=selection):
                try:
                    client.retrieve(dataset, selection, str(target))
                except Exception as exc:
                    raise PredictorError("CDS retrieval failed. Check dataset terms, credentials, dates and CDS service status. See the server log for the exception type.") from exc
            files.append(cached_retrieve(settings.data_dir / "cache", {"dataset": dataset, "request": selection}, retrieve, request.refresh))
    progress("Converting ERA5 GRIB into the local weather dataset")
    ds = read_grib(files, area=request.area, attrs={"source": request.source,
                   "label": f"{request.name} · {'ERA5' if request.source == 'era5' else 'ERA5 / ERA5T'}"})
    provenance = json.loads(ds.attrs["provenance_json"])
    versions = provenance["expver"]
    if request.source == "era5" and 5 in versions:
        raise PredictorError("CDS returned preliminary ERA5T (expver=5). Use the ERA5T source, or request an older finalized period.")
    provenance.update({"provider": "Copernicus Climate Data Store", "downloaded_utc": now.isoformat(),
                       "request_start": request.start.isoformat(), "request_end": request.end.isoformat(),
                       "source_note": "CDS selects final/preliminary fields by date; there is no ERA5T product_type."})
    ds.attrs["provenance_json"] = json.dumps(provenance)
    if request.source == "era5t":
        warnings = json.loads(ds.attrs["warnings_json"])
        warnings.append("ERA5T selection uses the available CDS release by date. Recent expver=5 is preliminary; older periods may already be final expver=1. Refresh explicitly to obtain later revisions.")
        ds.attrs["warnings_json"] = json.dumps(warnings)
    return ds


def forecast_steps(cycle, start, end):
    valid = list(range(0, 145, 3)) + list(range(150, 241, 6)) if cycle.hour in (0, 12) else list(range(0, 91, 3))
    lo = (start - cycle).total_seconds() / 3600
    hi = (end - cycle).total_seconds() / 3600
    if lo < 0 or hi > valid[-1]:
        raise PredictorError(f"Requested flight window is outside this IFS cycle. Available steps: 0–{valid[-1]} hours. Select a newer cycle or a shorter window.")
    left = max(step for step in valid if step <= lo)
    right = min(step for step in valid if step >= hi)
    return [step for step in valid if left <= step <= right]


def download_ifs(request, settings, progress):
    try:
        from ecmwf.opendata import Client
    except ImportError as exc:
        raise PredictorError("Install forecast support: pip install -e '.[weather]'.") from exc
    client = Client(source=os.environ.get("IFS_SOURCE", "ecmwf"), model="ifs", resol="0p25",
                    infer_stream_keyword=False, maximum_retries=2, retry_after=10)
    cycle = request.cycle
    if cycle is None:
        progress("Finding the latest complete 00/12 UTC IFS operational cycle")
        candidates = []
        for hour in (0, 12):
            try:
                latest = client.latest(time=hour, stream="oper", type="fc", step=0)
                candidates.append(latest.replace(tzinfo=timezone.utc))
            except Exception:
                pass
        if not candidates:
            raise PredictorError("Could not locate an IFS forecast cycle. Check internet access and ECMWF open-data service status, or specify a cycle.")
        cycle = max(candidates)
    steps = forecast_steps(cycle, request.start, request.end)
    common = {"date": cycle.strftime("%Y%m%d"), "time": cycle.hour, "stream": "oper", "type": "fc"}
    files = []
    for step in steps:
        selections = [{**common, "step": step, "levtype": "pl", "levelist": IFS_LEVELS, "param": ["u", "v", "t", "gh"]}]
        if request.surface:
            selections.append({**common, "step": step, "levtype": "sfc", "param": ["10u", "10v", "2t", "sp"]})
        for selection in selections:
            progress(f"Retrieving IFS {cycle:%Y-%m-%d %H} UTC · step {step}h · {selection['levtype']} (global fields, then cropped)")
            def retrieve(target, selection=selection):
                try:
                    client.retrieve(**selection, target=str(target))
                except Exception as exc:
                    raise PredictorError("IFS download failed. Check cycle availability and ECMWF service status. Historical cycles are retained only briefly; use archived local files for backtesting.") from exc
            files.append(cached_retrieve(settings.data_dir / "cache", {"provider": "ifs", "request": selection}, retrieve, request.refresh))
    if request.surface:
        selection = {**common, "step": 0, "levtype": "sfc", "param": ["z"]}
        def retrieve_terrain(target):
            try:
                client.retrieve(**selection, target=str(target))
            except Exception as exc:
                raise PredictorError("IFS terrain download failed; retry or import without surface data and use fixed landing altitude.") from exc
        files.append(cached_retrieve(settings.data_dir / "cache", {"provider": "ifs", "request": selection}, retrieve_terrain, request.refresh))
    progress("Converting and cropping IFS fields")
    ds = read_grib(files, area=request.area, attrs={"source": "ifs", "label": f"{request.name} · IFS {cycle:%d %b %H}Z"})
    provenance = json.loads(ds.attrs["provenance_json"])
    provenance.update({"provider": "ECMWF IFS open data", "cycle": cycle.isoformat(), "forecast_steps_hours": steps,
                       "horizontal_grid_degrees": 0.25, "attribution": "ECMWF, CC BY 4.0"})
    ds.attrs["provenance_json"] = json.dumps(provenance)
    return ds


def download_weather(request, settings, progress=lambda message: None):
    settings.prepare()
    return download_ifs(request, settings, progress) if request.source == "ifs" else download_era5(request, settings, progress)
