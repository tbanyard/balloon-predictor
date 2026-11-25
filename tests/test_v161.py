from datetime import timedelta
import math
import time

import numpy as np
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from balloon_predictor.api import create_app
from balloon_predictor.context import flight_weather
from balloon_predictor.errors import CoverageError, PredictorError
from balloon_predictor.models import PredictionRequest, WeatherOverlayRequest
from balloon_predictor.predictor import predict
from balloon_predictor.settings import Settings
from balloon_predictor.store import WeatherStore
from balloon_predictor.weather.interpolate import DemoWeather
from balloon_predictor.weather.native import ERA5Weather, WRFWeather, ColumnCache, scan_wrf
from balloon_predictor.weather.era5_native.data import Archive
from balloon_predictor.weather_overlay import sample_overlay, prepare_native, _runs
from conftest import make_normalized, make_wrf_file
from scripts.make_test_data import make_file


def request(utc, **updates):
    return PredictionRequest(launch_datetime=utc, launch_latitude=53, launch_longitude=-4,
                             launch_altitude=100, burst_altitude=1500, flight_mode="ascent").model_copy(update=updates)


def wait(client, job):
    deadline = time.monotonic()+10
    while time.monotonic()<deadline:
        state = client.get("/api/jobs/"+job["id"]).json()
        if state["status"] in ("completed", "failed"):
            return state
        time.sleep(.02)
    pytest.fail("Overlay job timed out")


@pytest.fixture
def store(tmp_path):
    return WeatherStore(Settings(tmp_path/"data", era5_root=tmp_path/"era5", wrf_root=tmp_path/"wrf"))


def test_overlay_fixed_height_time_and_saved_dataset(store, utc):
    req = request(utc)
    result = predict(req, store.get("demo"))
    launch = sample_overlay(result, WeatherOverlayRequest(grid_size=8), store)
    arrival = sample_overlay(result, WeatherOverlayRequest(grid_size=8, time_basis="target"), store)
    assert launch["datetime"] == "2024-01-15T12:00:00Z"
    assert arrival["datetime"] == result["summary"]["burst"]["datetime"]
    assert launch["altitude_m"] == arrival["altitude_m"] == 1500
    assert launch["dataset"] == "demo" and launch["warnings"]
    expected = DemoWeather().sample(53, -4, 1500, utc)
    assert np.allclose(launch["grid"]["u"], expected.u)
    assert np.allclose(launch["grid"]["temperature_c"], expected.temperature-273.15)
    assert np.allclose(launch["grid"]["wind_speed"], math.hypot(expected.u, expected.v))
    assert not np.allclose(launch["grid"]["u"], arrival["grid"]["u"])
    assert launch["valid_cells"] == launch["total_cells"] == 64


def test_saved_normalized_field_partial_coverage_and_changed_source(store, utc):
    store.save("test", make_normalized())
    req = request(utc, dataset="test")
    result = predict(req, store.get("test"))
    overlay = sample_overlay(result, WeatherOverlayRequest(grid_size=8, area=(56,-7,50,-1)), store)
    assert 0 < overlay["valid_cells"] < overlay["total_cells"]
    assert overlay["grid"]["u"][0][0] is None
    assert overlay["grid"]["u"][3][3] == pytest.approx(3.5)
    assert overlay["grid"]["temperature_c"][3][3] == pytest.approx(-3.15)
    path = store.settings.data_dir/"weather/test.nc"
    path.touch()
    with pytest.raises(PredictorError, match="changed"):
        sample_overlay(result, WeatherOverlayRequest(grid_size=8), store)


def test_no_extrapolation_or_forecast_fallback(store, utc):
    store.save("test", make_normalized())
    result = predict(request(utc, dataset="test"), store.get("test"))
    with pytest.raises(CoverageError, match="No valid weather"):
        sample_overlay(result, WeatherOverlayRequest(grid_size=8, area=(20,20,10,30)), store)
    result["summary"]["burst"]["altitude"] = 50000
    with pytest.raises(CoverageError, match="No valid weather"):
        sample_overlay(result, WeatherOverlayRequest(grid_size=8), store)
    store.settings.data_dir.joinpath("weather/test.nc").unlink()
    with pytest.raises(PredictorError, match="removed"):
        sample_overlay(result, WeatherOverlayRequest(grid_size=8), store)


def test_overlay_dateline_uses_a_small_continuous_box(store, utc):
    result = predict(request(utc, launch_latitude=0, launch_longitude=179.999), store.get("demo"))
    overlay = sample_overlay(result, WeatherOverlayRequest(grid_size=8), store)
    n,w,s,e = overlay["bounds"]
    assert 0 < e-w < 3 and w < 180 < e and s < 0 < n
    assert overlay["valid_cells"] == 64
    explicit = sample_overlay(result, WeatherOverlayRequest(grid_size=8, area=(1,179,-1,181)), store)
    assert explicit["bounds"] == [1,179,-1,181]


@pytest.mark.parametrize("values", [dict(grid_size=7),dict(grid_size=49),dict(flight_index=-1),dict(time_basis="latest"),
    dict(area=(10,10,20,20)),dict(area=(90,-10,0,10)),dict(area=(10,-200,0,200)),dict(area=(10,0,0,float("nan"))),dict(dataset="another-forecast")])
def test_bounded_overlay_request(values):
    with pytest.raises(ValidationError):
        WeatherOverlayRequest(**values)


@pytest.mark.parametrize("kind", ["era5", "wrf"])
def test_bulk_native_overlay_matches_original_flight_sampler(tmp_path, utc, kind):
    if kind == "era5":
        make_file(tmp_path/"era5/2024/era5_2024d015.nc", date="2024-01-15", times=(12,18),
                  lat=[55,53,51], lon=[354,356,358], humidity=True, surface_z=True,
                  reverse_levels=True, reorder_dims=True)
        base = ERA5Weather(Archive(tmp_path/"era5",16), utc, ColumnCache(16), 21600)
    else:
        make_wrf_file(tmp_path/"wrfout_d02_test", angle=37)
        base = WRFWeather(scan_wrf(tmp_path)[0][0], ColumnCache(16), 21600)
    when = utc+timedelta(hours=1.25)
    ys, xs = [51,52,53,54,55], [-6,-5,-4,-3,-2]
    prepared = prepare_native(base, when, ys, xs, lambda message: None)
    req = request(utc)
    original, cached = flight_weather(base, req), flight_weather(prepared, req)
    for lat, lon in [(51,-6),(52,-5),(53,-4),(54,-3),(55,-2)]:
        for height in [50,1500,10000]:
            a, b = original.sample(lat,lon,height,when), cached.sample(lat,lon,height,when)
            for name in ("u","v","temperature","pressure","w"):
                # ERA5 hydrostatic heights are stored as float32: scalar vs
                # block arithmetic can differ by a final rounding bit.
                assert getattr(a,name) == pytest.approx(getattr(b,name), rel=2e-6, abs=2e-5)
    assert "raw_column" not in base.__dict__
    assert list(map(list, _runs({0,1,2,7,8}, 2))) == [[0,1],[2],[7,8]]


def test_api_selects_saved_series_member_and_isolates_jobs(tmp_path, utc):
    settings = Settings(tmp_path/"data", era5_root=tmp_path/"era5", wrf_root=tmp_path/"wrf")
    app = create_app(settings)
    a, b = request(utc), request(utc+timedelta(hours=1), burst_altitude=2500, flight_name="Later flight")
    with TestClient(app) as client:
        job = client.post("/api/predictions/series", json={"flights":[a.model_dump(mode="json"),b.model_dump(mode="json")],"duration_hours":1,"interval_hours":1}).json()
        assert wait(client,job)["status"] == "completed"
        url = "/api/predictions/"+job["id"]+"/weather-overlay"
        accepted = client.post(url, json={"flight_index":1,"grid_size":8})
        assert accepted.status_code == 202
        answer = wait(client, accepted.json())
        assert answer["status"] == "completed", answer
        assert answer["result"]["altitude_m"] == 2500
        assert answer["result"]["flight_name"] == "Later flight"
        assert answer["result"]["datetime"] == "2024-01-15T13:00:00Z"
        assert client.post(url,json={"flight_index":99}).status_code == 404
        assert client.post(url,json={"dataset":"other"}).status_code == 422
        overlay_id = accepted.json()["id"]
        assert client.post("/api/predictions/"+overlay_id+"/weather-overlay",json={}).status_code == 409
        assert not (settings.data_dir/"results"/(overlay_id+".json")).exists()
    with TestClient(create_app(Settings(tmp_path/"other", era5_root=tmp_path/"era5", wrf_root=tmp_path/"wrf"))) as other:
        assert other.post(url,json={}).status_code == 404


def test_installer_tests_can_block_external_images(tmp_path, monkeypatch):
    monkeypatch.setenv("BP_TEST_BLOCK_EXTERNAL_TILES", "1")
    with TestClient(create_app(Settings(tmp_path/"data"))) as client:
        assert client.get("/").headers["content-security-policy"] == "img-src 'self' data: blob:"


def test_source_replaced_during_sampling_is_rejected(store, utc):
    store.save("test", make_normalized())
    result = predict(request(utc, dataset="test"), store.get("test"))
    changed = False
    def progress(message):
        nonlocal changed
        if not changed and "row 3/" in message:
            store.settings.data_dir.joinpath("weather/test.nc").touch()
            changed = True
    with pytest.raises(PredictorError, match="changed"):
        sample_overlay(result, WeatherOverlayRequest(grid_size=8), store, progress)
    assert changed


def test_overlay_labels_the_original_near_surface_estimate(store, utc):
    ds = make_normalized()
    ds["terrain"][:] = 500
    ds["height"][:] = ds.height.values + 500
    store.save("coast", ds)
    req = request(utc, dataset="coast", launch_altitude=1, burst_altitude=100, ground_altitude=0)
    result = predict(req, store.get("coast"))
    answer = sample_overlay(result, WeatherOverlayRequest(grid_size=8), store)
    assert answer["estimated_cells"] == answer["valid_cells"] == 64
    assert any("near-surface" in message for message in answer["warnings"])
    expected = flight_weather(store.get("coast"),req).sample(53,-4,100,utc)
    assert answer["grid"]["temperature_c"][3][3] == pytest.approx(expected.temperature-273.15)
