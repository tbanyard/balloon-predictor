"""WRF grid-to-earth rotation, including Lambert output without ALPHA fields.

The projection fallback follows NCAR wrf-python's uvmet cone/longitude equations:
https://wrf-python.readthedocs.io/en/develop/user_api/generated/wrf.uvmet.html
The returned sine uses the WRF COSALPHA/SINALPHA convention.
"""
import numpy as np
from ..errors import PredictorError


def rotation(attrs, latitude, longitude, cosine=None, sine=None, frame=None):
    frame = frame or attrs.get("balloon_wind_reference", "grid")
    if frame not in ("grid", "earth"):
        raise PredictorError("WRF wind reference must be grid or earth.")
    if frame == "earth":
        return np.ones_like(longitude, dtype=float), np.zeros_like(longitude, dtype=float)
    if cosine is not None and sine is not None:
        ca, sa = np.asarray(cosine), np.asarray(sine)
        if not np.all(np.isfinite(ca + sa)) or not np.allclose(ca*ca + sa*sa, 1, atol=.01):
            raise PredictorError("WRF rotation coefficients are invalid.")
        return ca, sa
    try:
        if int(attrs["MAP_PROJ"]) != 1:
            raise ValueError()
        a, b, standard = (float(attrs[k]) for k in ("TRUELAT1", "TRUELAT2", "STAND_LON"))
        if not np.isfinite(a+b+standard) or not (0 < abs(a) < 90 and 0 < abs(b) < 90) or a*b <= 0:
            raise ValueError()
        a, b = np.radians(abs(a)), np.radians(abs(b))
        cone = (np.log(np.cos(a)/np.cos(b)) / np.log(np.tan(np.pi/4-a/2)/np.tan(np.pi/4-b/2))
                if abs(a-b) > np.radians(.1) else np.sin(a))
        angle = np.radians((np.asarray(longitude)-standard+180) % 360-180)*cone
        angle = np.where(np.asarray(latitude) < 0, -angle, angle)
        return np.cos(angle), -np.sin(angle)
    except (KeyError, ValueError, TypeError, ZeroDivisionError):
        raise PredictorError("WRF needs COSALPHA/SINALPHA or valid Lambert MAP_PROJ=1, TRUELAT1, TRUELAT2 and STAND_LON. Set wrf_wind_reference: earth only for already rotated winds.") from None
