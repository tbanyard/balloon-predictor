"""A fixed-height, fixed-time map sampled from a saved flight's weather.

Native archives are read in small blocks, then use exactly the trajectory
sampler (including its terrain and missing-layer rules). No model download or
choice of a different forecast is performed here.
"""
import copy
from datetime import datetime
import math
from pathlib import Path

import numpy as np

from .context import flight_weather
from .errors import CoverageError, PredictorError
from .modelbox import bounds, era_block, wrf_block
from .models import PredictionRequest
from .provenance import record_file, validate_manifest, receipt
from .weather.io import NETCDF_LOCK
from .weather.native import ERA5Weather, WRFWeather
from .weather.interpolate import DemoWeather
from .weather_pressure import sample_pressure, demo_pressure_height


def _runs(indices, limit):
    """Contiguous chunks also keep WRF's staggered-grid reads aligned."""
    run = []
    for index in sorted(indices):
        if run and (index != run[-1] + 1 or len(run) >= limit):
            yield np.array(run)
            run = []
        run.append(index)
    if run:
        yield np.array(run)


def prepare_native(base, when, latitudes, longitudes, progress):
    if not isinstance(base, (ERA5Weather, WRFWeather)):
        return base
    with NETCDF_LOCK:
        # A private records list prevents other archive users changing time
        # indices while these columns are being assembled.
        model = copy.copy(base)
        model.info = copy.deepcopy(base.info)
        model.source_receipts = dict(getattr(base, "source_receipts", {}))
        model.records = list(base.records)
        if hasattr(base, "loaded_days"):
            model.loaded_days = set(base.loaded_days)
        model.ensure_time(when)
        first, last, fraction = model.time_bracket(when)
        times = {first} if first == last or fraction <= 1e-12 else {last} if fraction >= 1-1e-12 else {first, last}
        corners = set()
        for lat in latitudes:
            for lon in longitudes:
                try:
                    corners.update((y, x) for y, x, weight in model.spatial_weights(lat, lon) if weight > 1e-12)
                except CoverageError:
                    pass
        if not corners:
            raise CoverageError("This map area is outside the selected flight's weather grid. Move the map inside the model coverage.")
        ys, xs = {p[0] for p in corners}, {p[1] for p in corners}
        columns = {}
        if isinstance(model, ERA5Weather):
            # netCDF4 orthogonal indexing can batch disjoint ERA5 nodes; WRF
            # needs contiguous groups because its velocities are staggered.
            ylist, xlist = np.array(sorted(ys)), np.array(sorted(xs))
            blocks = [(ylist[y:y+8], xlist[x:x+32]) for y in range(0,len(ylist),8) for x in range(0,len(xlist),32)]
        else:
            blocks = [(y, x) for y in _runs(ys, 8) for x in _runs(xs, 32)]
        used_bytes = 0
        for ti in sorted(times):
            path = model.archive.meta(model.records[ti][1])["path"] if isinstance(model, ERA5Weather) else Path(model.records[ti][1])
            record_file(model, path)
            for n, (yb, xb) in enumerate(blocks):
                if not any((int(y), int(x)) in corners for y in yb for x in xb):
                    continue
                progress(f"Reading weather columns · time {sorted(times).index(ti)+1}/{len(times)} · block {n+1}/{len(blocks)}")
                fields, surface = (era_block if isinstance(model, ERA5Weather) else wrf_block)(model, ti, yb, xb)
                for iy, y in enumerate(yb):
                    for ix, x in enumerate(xb):
                        if (int(y), int(x)) not in corners:
                            continue
                        raw = {name: values[:, iy, ix].copy() for name, values in fields.items()}
                        raw["terrain"] = float(surface["terrain"][iy, ix])
                        keys = {"u": "surface_u", "v": "surface_v", "temperature": "surface_temperature", "pressure": "surface_pressure"}
                        if all(key in surface and math.isfinite(float(surface[key][iy, ix])) for key in keys.values()) and surface["surface_temperature"][iy, ix] > 0 and surface["surface_pressure"][iy, ix] > 0:
                            raw["surface"] = {name: float(surface[key][iy, ix]) for name, key in keys.items()}
                            if "surface_specific_humidity" in surface:
                                raw["surface"]["specific_humidity"] = float(surface["surface_specific_humidity"][iy, ix])
                        used_bytes += sum(v.nbytes for v in raw.values() if isinstance(v, np.ndarray))
                        if used_bytes > 96 * 1024**2:
                            raise PredictorError("This weather overlay needs too many native columns. Use a smaller map area or a lower grid size.")
                        columns[ti, int(y), int(x)] = raw
            record_file(model, path)
        # Sampling is now independent of the archive and its shared LRU cache.
        model.raw_column = lambda ti, y, x: columns[ti, int(y), int(x)]
        model.ensure_time = lambda when: None
        return model


def model_bounds(base, longitude_centre):
    if hasattr(base, "lat"):
        north,south = min(85.,float(np.max(base.lat))),max(-85.,float(np.min(base.lat)))
        if getattr(base,"periodic",False):
            return [north,longitude_centre-180,south,longitude_centre+180],True
        centre = (base.lons[0]+base.lons[-1])/2 if base.regular else base.reference_lon
        lon = centre+(base.lon-centre+180)%360-180
        west,east = float(np.min(lon)),float(np.max(lon))
    else:
        north,west,south,east = base.info["area"]
        if east < west:
            east += 360
        if east-west >= 359:
            return [min(85.,north),longitude_centre-180,max(-85.,south),longitude_centre+180],True
    shift = 360*round((longitude_centre-(west+east)/2)/360)
    return [float(north),west+shift,float(south),east+shift],False


def overlay_area(base, request, result, options):
    centre = (options.area[1]+options.area[3])/2 if options.area else request.launch_longitude
    available,periodic = model_bounds(base,centre)
    n,w,s,e = available
    regional = n-s <= 40 and e-w <= 80 and not periodic
    if options.coverage == "auto":
        if regional:
            return available,available,"model"
        area = list(options.area) if options.area else [min(85.,request.launch_latitude+10),request.launch_longitude-13,max(-85.,request.launch_latitude-10),request.launch_longitude+13]
        area[0],area[2] = min(area[0],n),max(area[2],s)
        if not periodic:
            area[1],area[3] = max(area[1],w),min(area[3],e)
        return area,available,"viewport"
    if options.area is not None:
        return list(options.area),available,"requested"
    # Retain v1.6.1 API calls without a viewport. The UI always requests auto
    # coverage, which fills regional models and buffers large/global map views.
    points = [p for flight in [result,*result.get("ensemble",[])] for stage in flight["prediction"] for p in stage["trajectory"]]
    box = bounds(points)
    area = [min(85.,box["north"]),box["west_unwrapped"],max(-85.,box["south"]),box["east_unwrapped"]]
    area[0],area[2] = min(area[0],n),max(area[2],s)
    if not periodic:
        # Align the model box with the trajectory box's longitude copy.
        offset = 360*round(((area[1]+area[3])/2-(w+e)/2)/360)
        area[1],area[3] = max(area[1],w+offset),min(area[3],e+offset)
    return area,available,"requested"


def sample_overlay(result, options, store, progress=lambda message: None):
    validate_manifest(result)
    request = PredictionRequest.model_validate(result["request"])
    target = result["summary"]["burst"]
    altitude = float(options.altitude_m if options.altitude_m is not None else target["altitude"])
    pressure = options.pressure_hpa
    when = request.launch_datetime if options.time_basis == "launch" else datetime.fromisoformat(target["datetime"].replace("Z", "+00:00"))
    progress("Loading the selected flight's weather")
    base = store.get(request.dataset,when)
    area,available,coverage_mode = overlay_area(base,request,result,options)
    north,west,south,east = area
    if north<=south or east<=west:
        raise CoverageError("The visible map is outside this flight's weather coverage. Move the map back inside its model boundary.")
    latitudes = np.linspace(south,north,options.grid_size)
    longitudes = np.linspace(west,east,options.grid_size)
    base = prepare_native(base,when,latitudes,longitudes,progress)
    weather = flight_weather(base,request)
    demo_height = demo_pressure_height(base,pressure*100,when) if pressure is not None and isinstance(base,DemoWeather) else None
    fields = {name:[] for name in ("temperature_c","u","v","wind_speed","altitude_m")}
    valid = estimated = 0
    label = f"{pressure:g} hPa" if pressure is not None else f"{altitude:,.0f} m AMSL"
    heights = []
    for row,lat in enumerate(latitudes):
        values = {name:[] for name in fields}
        progress(f"Sampling at {label} · row {row+1}/{len(latitudes)}")
        for lon in longitudes:
            previous = weather.bridge["sample_calls"]
            try:
                lon = float((lon+180)%360-180)
                if pressure is not None:
                    sample,height = sample_pressure(base,float(lat),lon,pressure*100,when,demo_height)
                else:
                    sample = weather.sample(float(lat),lon,altitude,when)
                    height = altitude
                numbers = (sample.temperature-273.15,sample.u,sample.v,math.hypot(sample.u,sample.v),height)
                if not all(math.isfinite(value) for value in numbers):
                    raise CoverageError("Missing weather at this map cell.")
                valid += 1
                heights.append(height)
                estimated += weather.bridge["sample_calls"]>previous
            except CoverageError:
                numbers = (None,)*len(fields)
            for name,number in zip(fields,numbers):
                values[name].append(number)
        for name in fields:
            fields[name].append(values[name])
    validate_manifest(result)
    for entry in getattr(weather.model,"source_receipts",{}).values():
        if receipt(entry["path"]) != entry:
            raise PredictorError("Weather changed while preparing this overlay. Re-run the prediction before displaying it.")
    if not valid:
        raise CoverageError("No valid weather at this height/pressure and time in the available area. Check the model's represented levels, terrain and time coverage, or calculate a new flight.")
    total = len(latitudes)*len(longitudes)
    warnings = []
    if valid<total:
        warnings.append("Areas outside model coverage, below model ground or with missing atmospheric layers are transparent.")
    if estimated:
        warnings.append(f"{estimated} grid points use the flight's permitted estimate of missing near-surface air.")
    if pressure is not None and hasattr(base,"fields") and base.ground is None and "surface_pressure" not in base.surface:
        warnings.append("This dataset has no terrain or surface pressure. Below-ground pressure levels cannot be identified beyond the supplied missing-data mask.")
    if request.dataset == "demo":
        warnings.append("Synthetic demo weather; not a real forecast.")
    return {"dataset":request.dataset,"label":result["weather"]["label"],"source":result["weather"].get("source"),
            "flight_name":request.flight_name,"altitude_m":altitude if pressure is None else None,"pressure_hpa":pressure,
            "altitude_range_m":[min(heights),max(heights)],"vertical_coordinate":"pressure" if pressure is not None else "altitude",
            "flight_target_altitude_m":float(target["altitude"]),"datetime":when.isoformat().replace("+00:00","Z"),
            "launch_datetime":request.launch_datetime.isoformat().replace("+00:00","Z"),"target_datetime":target["datetime"],
            "time_basis":options.time_basis,"height_kind":"pressure" if pressure is not None else "custom" if options.altitude_m is not None else "burst" if result["summary"].get("landing") else "target",
            "bounds":area,"model_bounds":available,"coverage_mode":coverage_mode,
            "grid":{"latitude":latitudes.tolist(),"longitude":longitudes.tolist(),**fields},
            "valid_cells":valid,"total_cells":total,"estimated_cells":estimated,"warnings":warnings,
            "description":"Saved flight weather at fixed altitude AMSL, or interpolated on adjacent native levels in log pressure then in horizontal position and time. Winds are earth-relative. Animation illustrates flow at this fixed weather time."}
