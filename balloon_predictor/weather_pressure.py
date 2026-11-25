"""Isobaric weather from actual model columns, without ISA height conversion."""
import math
import numpy as np
from scipy.optimize import brentq

from .errors import CoverageError
from .weather.interpolate import DemoWeather, Sample


def raw_column(model, ti, y, x):
    if hasattr(model, "raw_column"):
        return model.raw_column(ti, y, x)
    raw = {name: values[ti, :, y, x] for name, values in model.fields.items()}
    raw["terrain"] = float(model.ground[y,x]) if model.ground is not None else -math.inf
    surface = {name.removeprefix("surface_"): float(values[ti,y,x]) for name,values in model.surface.items()}
    if surface:
        raw["surface"] = surface
    return raw


def pressure_column(column, pressure):
    """Interpolate adjacent native levels in log pressure, retaining holes.

    A surface observation can anchor the bottom at 10 m AGL. Values below the
    physical model surface or outside represented pressures are masked.
    """
    fields = {key: np.asarray(column[key],dtype=float) for key in ("u","v","temperature","height","pressure")}
    h = fields["height"]
    finite = h[np.isfinite(h)]
    if len(finite) < 2:
        raise CoverageError("Fewer than two represented heights at this pressure column.")
    # Preserve original adjacency, including rows with missing coordinates.
    if finite[-1] < finite[0]:
        fields = {name: values[::-1] for name,values in fields.items()}
    terrain = column.get("terrain", -math.inf)
    surface = column.get("surface", {})
    sp = surface.get("pressure", math.nan)
    if math.isfinite(sp) and sp > 0 and pressure > sp + 1e-4:
        raise CoverageError("This pressure surface is below model ground.")
    good = np.ones(len(h),dtype=bool)
    for values in fields.values():
        good &= np.isfinite(values)
    good &= (fields["height"] > terrain+.1) & (fields["temperature"] > 0) & (fields["pressure"] > 0)
    if math.isfinite(sp) and sp > 0:
        good &= fields["pressure"] <= sp + 1e-4
    if not good.any():
        raise CoverageError("No valid pressure levels above terrain.")
    first,last = np.flatnonzero(good)[[0,-1]]
    fields = {key: values[first:last+1] for key,values in fields.items()}
    good = good[first:last+1]
    keys = ("u","v","temperature","pressure")
    if math.isfinite(terrain) and all(k in surface and math.isfinite(surface[k]) for k in keys) and sp > 0 and surface["temperature"] > 0 and fields["height"][0] > terrain+10.1:
        fields = {key: np.r_[terrain+10 if key=="height" else surface[key],values] for key,values in fields.items()}
        good = np.r_[True,good]
    p = fields["pressure"]
    exact = np.flatnonzero(good & np.isclose(p,pressure,rtol=1e-9,atol=1e-4))
    if len(exact):
        return {key:float(values[exact[0]]) for key,values in fields.items()}
    for i in range(len(p)-1):
        if not (good[i] and good[i+1] and p[i] > pressure > p[i+1] and fields["height"][i+1] > fields["height"][i]):
            continue
        fraction = (math.log(pressure)-math.log(p[i]))/(math.log(p[i+1])-math.log(p[i]))
        result = {key:float(values[i]*(1-fraction)+values[i+1]*fraction) for key,values in fields.items()}
        result["pressure"] = float(pressure)
        return result
    raise CoverageError("Pressure is outside represented levels or crosses a missing atmospheric layer.")


def demo_pressure_height(model, pressure, when):
    try:
        return brentq(lambda height:model.sample(0,0,height,when).pressure-pressure,-500,55000)
    except ValueError as exc:
        raise CoverageError("Pressure level is outside the demo atmosphere.") from exc


def sample_pressure(model, lat, lon, pressure, when, demo_height=None):
    if isinstance(model,DemoWeather):
        height = demo_height if demo_height is not None else demo_pressure_height(model,pressure,when)
        return model.sample(lat,lon,height,when),height
    i,j,fraction = model.time_bracket(when) if hasattr(model,"time_bracket") else model._bracket(model.times,when.timestamp(),"Time")
    times = [(i,1.)] if i==j or fraction<=1e-12 else [(j,1.)] if fraction>=1-1e-12 else [(i,1-fraction),(j,fraction)]
    values = {key:0. for key in ("u","v","temperature","height")}
    weights = model.spatial_weights(lat,lon)
    for ti,tw in times:
        for y,x,sw in weights:
            column = pressure_column(raw_column(model,ti,y,x),pressure)
            for key in values:
                values[key] += column[key]*tw*sw
    return Sample(values["u"],values["v"],values["temperature"],pressure),values["height"]
