"""Small synthetic regional forecast for the installed Android test."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import numpy as np
from balloon_predictor.offline import export_forecast_pack
from balloon_predictor.models import PredictionRequest
from balloon_predictor.predictor import predict
from balloon_predictor.settings import Settings
from balloon_predictor.store import WeatherStore
from balloon_predictor.weather.schema import dataset_from_arrays

output = Path(sys.argv[1]).resolve()
store = WeatherStore(Settings(output.parent / "offline-weather"))
shape = (2, 3, 3, 3)
height = np.broadcast_to(np.array([100, 10000, 35000])[None, :, None, None], shape)
pressure = np.broadcast_to(np.array([100000, 25000, 500])[None, :, None, None], shape)
data = dataset_from_arrays(["2024-01-15T12:00", "2024-01-15T18:00"], [51,53,55], [-6,-4,-2],
                           {"height":height,"pressure":pressure,"u":2+height/1000,
                            "v":np.full(shape,3),"temperature":np.full(shape,270)},
                           {"terrain":np.zeros((3,3))}, {"source":"gfs","label":"Synthetic GFS test forecast"})
store.save("offline-fixture", data)
request = PredictionRequest(dataset="offline-fixture", launch_datetime="2024-01-15T13:00:00Z",
                            burst_altitude=1500, time_step=20)
result = predict(request, store.get(request.dataset))
Path(export_forecast_pack(result, store, print)["path"]).replace(output)
