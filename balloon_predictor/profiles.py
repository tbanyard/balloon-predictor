"""Synthetic radiosonde observations at each ascent position and UTC time."""
from datetime import datetime
import math

import numpy as np


def dewpoint_c(pressure, specific_humidity):
    """Magnus inversion over liquid water; unavailable humidity stays unavailable."""
    if specific_humidity is None or not math.isfinite(specific_humidity) or not 0 < specific_humidity < 1:
        return None
    e = pressure * specific_humidity / (.622 + .378*specific_humidity)
    logarithm = math.log(e/611.2)
    value = 243.5*logarithm/(17.67-logarithm)
    return value if math.isfinite(value) else None


def observation(weather, point):
    sample = weather.sample(point["latitude"],point["longitude"],point["altitude"],
                            datetime.fromisoformat(point["datetime"].replace("Z","+00:00")))
    return {**point,"pressure_hpa":sample.pressure/100,"temperature_c":sample.temperature-273.15,
            "dewpoint_c":dewpoint_c(sample.pressure,sample.specific_humidity),
            "specific_humidity":sample.specific_humidity,"u":sample.u,"v":sample.v}


def flight_sounding(result, weather):
    points = result["prediction"][0]["trajectory"]
    indices = np.unique(np.linspace(0,len(points)-1,min(180,len(points))).round().astype(int))
    rows = [observation(weather,points[i]) for i in indices]
    return {"mode":"ascent","rows":rows,"humidity_available":any(r["dewpoint_c"] is not None for r in rows),
            "description":"Atmosphere sampled along the predicted ascent at its changing location and time.",
            "notes":["Model-derived synthetic sounding; no sensor lag, radiation correction or balloon wake is simulated.",
                     "Dew point is omitted wherever humidity is absent or zero; no moisture is invented.",
                     "Dew point uses a liquid-water Magnus approximation; it is not frost point."]}
