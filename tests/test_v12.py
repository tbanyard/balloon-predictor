from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import time
from urllib.parse import parse_qs, urlsplit

import numpy as np
import pytest
from fastapi.testclient import TestClient
from netCDF4 import Dataset, num2date

from balloon_predictor.api import create_app
from balloon_predictor.coordination import plan_arrivals
from balloon_predictor.errors import PredictorError
from balloon_predictor.models import ArrivalPlanRequest, DownloadRequest, PredictionRequest
from balloon_predictor.predictor import predict
from balloon_predictor.settings import Settings
from balloon_predictor.sonde import SCHEMA_PATH, sampled_track, write_sonde
from balloon_predictor.store import WeatherStore
from balloon_predictor.weather import DemoWeather
from balloon_predictor.weather.download import check_download_size, download_weather, forecast_steps, ifs_stream
from balloon_predictor.weather.gfs import latest_cycle, subset_url
from balloon_predictor.weather.interpolate import Sample
from balloon_predictor.weather.readers import read_grib
from conftest import make_grib, make_normalized


class ConstantWeather(DemoWeather):
    info = {**DemoWeather.info, "id": "constant", "source": "fixture", "label": "Constant test winds",
            "provenance": {"description": "Analytical fixture"}}
    def sample(self, lat, lon, altitude, when):
        return Sample(10., 3., 280., 90000.*np.exp(-altitude/8000), .5, .004)


class FixedStore:
    def get(self, name, when=None):
        return ConstantWeather()


@pytest.mark.parametrize("rate", [2, 3, 4, 5, 5.01, 5.05])
def test_whole_and_fractional_ascent_rates(rate, utc):
    body = PredictionRequest(launch_datetime=utc, ascent_rate=rate, burst_altitude=1500, flight_mode="ascent")
    result = predict(body, ConstantWeather())
    assert result["summary"]["ascent_seconds"] == pytest.approx(1400/rate, abs=.001)


def test_coordinated_rates_reach_exact_altitude_and_time(utc):
    body = PredictionRequest(dataset="constant", launch_datetime=utc, launch_altitude=936.56, burst_altitude=25000)
    target = utc+timedelta(hours=4)
    request = ArrivalPlanRequest(flights=[body], arrival_datetime=target, target_altitude=20000, ascent_rates=[5,4,3,2])
    plan = plan_arrivals(request, FixedStore())
    assert plan["completed"] == 4 and plan["schedule_type"] == "coordinated"
    launches = []
    for entry in plan["flights"]:
        flight = entry["result"]
        launch = datetime.fromisoformat(entry["launch_datetime"].replace("Z", "+00:00"))
        launches.append(launch)
        assert (target-launch).total_seconds() == pytest.approx((20000-936.56)/entry["ascent_rate"], abs=.001)
        assert flight["summary"]["burst"]["altitude"] == 20000
        assert abs(entry["arrival_error_seconds"]) <= .25
        assert flight["summary"]["landing"] is None
        assert len(flight["prediction"]) == 1
    assert launches == sorted(launches, reverse=True)  # Slowest balloon first.
    assert len({round(p["longitude"],5) for p in plan["arrival_positions"]}) == 4


def test_coordinated_saved_rates_and_vertical_wind(utc):
    a = PredictionRequest(launch_datetime=utc, ascent_rate=5, use_vertical_wind=True, launch_altitude=100)
    b = a.model_copy(update={"ascent_rate": 2, "flight_name": "Slow", "launch_altitude": 500})
    plan = plan_arrivals(ArrivalPlanRequest(flights=[a,b],arrival_datetime=utc+timedelta(hours=3),
        target_altitude=2000,ascent_rates=None), FixedStore())
    assert plan["completed"] == 2
    for entry in plan["flights"]:
        height = 2000 - entry["result"]["request"]["launch_altitude"]
        assert entry["result"]["summary"]["ascent_seconds"] == pytest.approx(height/(entry["ascent_rate"]+.5), abs=.001)
        assert abs(entry["arrival_error_seconds"]) <= .25


def test_coordinated_partial_failures_are_kept(utc):
    flight = PredictionRequest(launch_datetime=utc, max_duration_hours=.1)
    plan = plan_arrivals(ArrivalPlanRequest(flights=[flight], arrival_datetime=utc,
        target_altitude=1500, ascent_rates=[5,2]), FixedStore())
    assert plan["completed"] == plan["failed"] == 1
    assert "duration" in plan["flights"][1]["error"]


def test_arrival_validation_work_limits_timezone_and_altitude(utc):
    flight = PredictionRequest(launch_datetime=utc)
    for updates in [{"arrival_datetime":"2024-01-01T12:00:00"},{"target_altitude":50},
                    {"ascent_rates":[0]},{"ascent_rates":[float("nan")]},
                    {"flights":[flight]*20,"ascent_rates":[2,3,4,5,6]}]:
        with pytest.raises(ValueError):
            ArrivalPlanRequest(**({"flights":[flight],"arrival_datetime":utc}|updates))


def test_sonde_contract_values_metadata_and_humidity(tmp_path, utc):
    body = PredictionRequest(launch_datetime=utc, dataset="constant", launch_altitude=100,
        burst_altitude=1234.5, ascent_rate=5.01, time_step=7.3, flight_mode="ascent")
    result = predict(body, ConstantWeather())
    path = write_sonde(tmp_path/"sonde.nc",result,ConstantWeather())
    schema = json.loads(SCHEMA_PATH.read_text())
    with Dataset(path) as ds:
        assert ds.data_model == "NETCDF4" and not ds.dimensions["time"].isunlimited()
        assert list(ds.variables) == list(schema["variables"])
        assert len(ds.variables) == 22
        for name,var in ds.variables.items():
            spec = schema["variables"][name]
            assert str(var.dtype) == spec["dtype"] and var.dimensions == ("time",)
            if spec["nan_fill"]: assert np.isnan(var.getncattr("_FillValue"))
            if name != "time": assert var.units == spec["attributes"]["units"]
        seconds = ds.variables["time"][:]
        assert np.array_equal(np.diff(seconds), np.ones(len(seconds)-1))
        assert ds.variables["time"].calendar == "proleptic_gregorian"
        assert str(num2date(0,ds.variables["time"].units)) == "2024-01-15 12:00:00"
        assert ds.variables["alt"][-1] == pytest.approx(100+seconds[-1]*5.01, abs=.001)
        assert ds.variables["specific_humidity"][0] == pytest.approx(.004)
        assert ds.variables["temp"][0] == 280
        assert ds.variables["vel_h"][0] == pytest.approx(np.hypot(10,3))
        assert ds.variables["windDir"][0] == pytest.approx(np.degrees(np.arctan2(-10,-3)) % 360)
        assert ds.variables["CompRng"][0] == 0
        assert np.ma.getmaskarray(ds.variables["vel_z_prime"][:]).all()
        assert np.ma.getmaskarray(ds.variables["vel_z_prime_smoothed"][:]).all()
        assert ds.source.startswith("SIMULATION:") and ds.serial == "SIMULATED"
        assert ds.contact == "" and ds.site_name == body.flight_name
        assert set(schema["global_attributes"]) <= set(ds.ncattrs())
        assert json.loads(ds.exact_endpoint_json)["altitude"] == 1234.5
        assert "sensor noise" not in ds.variables["vel_z_smoothed"].description


def test_dry_sonde_and_full_flight_sampling(tmp_path,utc):
    body = PredictionRequest(launch_datetime=utc,dataset="demo",burst_altitude=500,descent_model="constant")
    result = predict(body,DemoWeather())
    path = write_sonde(tmp_path/"dry.nc",result,DemoWeather(),"full")
    with Dataset(path) as ds:
        assert ds.trajectory_scope == "full"
        assert np.min(ds.variables["vel_z"][:]) < 0
        for name in ["dewp","relative_humidity","specific_humidity"]:
            assert np.ma.getmaskarray(ds.variables[name][:]).all()
    with pytest.raises(PredictorError): sampled_track(result,"unknown")


def test_sonde_longitude_interpolation_crosses_dateline_locally(utc):
    body = PredictionRequest(launch_datetime=utc,dataset="demo",launch_longitude=179.999,flight_mode="ascent",burst_altitude=1000)
    result = predict(body,ConstantWeather())
    _,lat,lon,_ = sampled_track(result)
    assert lon.min() < -179 and lon.max() > 179
    assert np.max(abs(np.diff(np.unwrap(np.radians(lon))))) < .001


def test_humidity_grib_is_retained_and_wrong_cycle_rejected(tmp_path,utc):
    import eccodes as ec
    raw=tmp_path/"q.grib";make_grib(raw)
    with raw.open("ab") as output:
        for step in [0,6]:
            for level in [1000,500,10]:
                gid=ec.codes_grib_new_from_samples("regular_ll_pl_grib2")
                try:
                    for key,value in {"Ni":3,"Nj":3,"latitudeOfFirstGridPointInDegrees":55,
                        "latitudeOfLastGridPointInDegrees":51,"longitudeOfFirstGridPointInDegrees":354,
                        "longitudeOfLastGridPointInDegrees":358,"iDirectionIncrementInDegrees":2,
                        "jDirectionIncrementInDegrees":2,"jScansPositively":0,"dataDate":20240115,
                        "dataTime":1200,"forecastTime":step,"level":level,"paramId":133}.items():
                        ec.codes_set(gid,key,value)
                    ec.codes_set_values(gid,np.full(9,.004));ec.codes_write(gid,output)
                finally:ec.codes_release(gid)
    ds=read_grib([raw],expected_cycle=utc)
    assert np.allclose(ds.specific_humidity.values,.004)
    with pytest.raises(PredictorError,match="initialization"):
        read_grib([raw],expected_cycle=utc-timedelta(hours=6))


def test_gfs_regional_request_step_boundaries_and_cached_download(tmp_path,utc,monkeypatch):
    import requests
    import balloon_predictor.weather.gfs as gfs
    raw=tmp_path/"gfs.grib";make_grib(raw);calls=[]
    class Response:
        status_code=200
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def raise_for_status(self):pass
        def iter_content(self,size):yield raw.read_bytes()
    def get(url,**kwargs):calls.append(url);return Response()
    monkeypatch.setattr(requests,"get",get);monkeypatch.setattr(gfs.time,"sleep",lambda *_:None)
    body=DownloadRequest(source="gfs",name="gfs-test",cycle=utc,start=utc+timedelta(minutes=15),
        end=utc+timedelta(hours=5,minutes=45),area=[55,-6,51,-2])
    ds=download_weather(body,Settings(tmp_path/"data"))
    assert ds.attrs["source"] == "gfs" and len(calls) == 7
    params=parse_qs(urlsplit(calls[0]).query,keep_blank_values=True)
    assert all(key in params for key in ["var_UGRD","var_VGRD","var_SPFH","var_VVEL","lev_1_mb","lev_1000_mb","lev_surface","lev_10_m_above_ground"])
    assert params["leftlon"] == ["-6.0"]
    assert "lev_875_mb" not in params and "lev_0.01_mb" in params
    assert json.loads(ds.attrs["provenance_json"])["forecast_steps_hours"] == list(range(7))
    download_weather(body,Settings(tmp_path/"data"));assert len(calls) == 7
    assert forecast_steps(utc,utc+timedelta(hours=120.1),utc+timedelta(hours=122),"gfs") == [120,123]
    assert forecast_steps(utc,utc+timedelta(hours=359),utc+timedelta(hours=360)) == [354,360]
    with pytest.raises(PredictorError):forecast_steps(utc,utc,utc+timedelta(hours=385),"gfs")


def test_latest_gfs_falls_back_to_available_cycle(utc,monkeypatch):
    import requests
    import balloon_predictor.weather.gfs as gfs
    class Clock(datetime):
        @classmethod
        def now(cls,tz=None):return utc+timedelta(hours=2)
    class Response:
        def __init__(self,url):self.status_code=404 if "/12/atmos/" in url else 200
        def __enter__(self):return self
        def __exit__(self,*args):pass
    monkeypatch.setattr(gfs,"datetime",Clock)
    monkeypatch.setattr(requests,"get",lambda url,**kwargs:Response(url))
    body=DownloadRequest(source="gfs",name="latest",start=utc,end=utc+timedelta(hours=3))
    assert latest_cycle(body,lambda *_:None) == utc-timedelta(hours=6)


def test_ifs_stream_transition_and_download_memory_guard(utc,tmp_path):
    assert ifs_stream(datetime(2026,5,11,6,tzinfo=timezone.utc)) == "scda"
    assert ifs_stream(datetime(2026,5,12,6,tzinfo=timezone.utc)) == "oper"
    request=DownloadRequest(source="gfs",name="oversized",start=utc,end=utc+timedelta(hours=72),area=[90,-180,-90,180])
    with pytest.raises(PredictorError,match="Reduce the area"):
        check_download_size(request,Settings(tmp_path),73,41)


def test_surface_pressure_masks_underground_levels_and_does_not_invent_moisture(utc):
    from balloon_predictor.weather import DatasetWeather
    from balloon_predictor.weather.schema import describe_dataset
    ds=make_normalized()
    ds["specific_humidity"]=(ds.u.dims,np.full(ds.u.shape,.004))
    weather=DatasetWeather(ds,describe_dataset(ds,"fixture"))
    assert weather.sample(53,-4,10,utc).specific_humidity is None
    assert weather.sample(53,-4,10000,utc).specific_humidity == pytest.approx(.004)


def test_optional_missing_vertical_motion_only_blocks_enabled_physics(utc):
    from balloon_predictor.weather import DatasetWeather
    from balloon_predictor.weather.schema import describe_dataset
    ds=make_normalized();ds["w"]=(ds.u.dims,np.full(ds.u.shape,np.nan))
    weather=DatasetWeather(ds,describe_dataset(ds,"fixture"))
    body=PredictionRequest(dataset="fixture",launch_datetime=utc,launch_latitude=53,launch_longitude=-4,
        burst_altitude=1000,flight_mode="ascent")
    assert predict(body,weather)["summary"]["burst"]["altitude"]==1000
    with pytest.raises(PredictorError,match="vertical wind is missing"):
        predict(body.model_copy(update={"use_vertical_wind":True}),weather)


def test_real_surface_humidity_is_used_when_provided(utc):
    from balloon_predictor.weather import DatasetWeather
    from balloon_predictor.weather.schema import describe_dataset
    ds=make_normalized()
    ds["specific_humidity"]=(ds.u.dims,np.full(ds.u.shape,.004))
    ds["surface_specific_humidity"]=(ds.surface_u.dims,np.full(ds.surface_u.shape,.006))
    weather=DatasetWeather(ds,describe_dataset(ds,"fixture"))
    assert weather.sample(53,-4,10,utc).specific_humidity == pytest.approx(.006)
    assert weather.sample(53,-4,10000,utc).specific_humidity == pytest.approx(.004)


def test_arrival_api_persistence_and_selected_sonde_download(tmp_path,utc):
    settings=Settings(tmp_path/"data",era5_root=tmp_path/"era5",wrf_root=tmp_path/"wrf")
    with TestClient(create_app(settings)) as client:
        body={"flights":[{"dataset":"demo","launch_datetime":utc.isoformat(),"burst_altitude":1500}],
            "arrival_datetime":(utc+timedelta(hours=1)).isoformat(),"target_altitude":1000,"ascent_rates":[5,2]}
        response=client.post("/api/predictions/arrival",json=body)
        assert response.status_code==202
        ident=response.json()["id"]
        for _ in range(200):
            job=client.get("/api/jobs/"+ident).json()
            if job["status"] in ("completed","failed"):break
            time.sleep(.01)
        assert job["status"]=="completed",job
        assert job["result"]["completed"]==2
        response=client.get(f"/api/predictions/{ident}/sonde.nc?flight_index=1")
        assert response.status_code==200 and response.content[:4]==b"\x89HDF"
        file=tmp_path/"download.nc";file.write_bytes(response.content)
        with Dataset(file) as ds:
            assert ds.site_name.endswith("2 m/s")
            assert len(ds.dimensions["time"])==451
        assert not list((settings.data_dir/"results").glob("*.sonde.nc"))
        assert client.get(f"/api/predictions/{ident}/sonde.nc?flight_index=9").status_code==404
        assert client.get(f"/api/predictions/{ident}/sonde.nc?scope=wrong").status_code==422
        for fmt in ["json","csv","geojson","kml"]:
            assert client.get(f"/api/predictions/{ident}/export/{fmt}").status_code==200
        timetable=client.get(f"/api/predictions/{ident}/export/schedule")
        assert timetable.status_code==200 and "ascent_rate_m_s,launch_utc" in timetable.text
    with TestClient(create_app(settings)) as client:
        assert client.get("/api/jobs/"+ident).json()["result"]["schedule_type"]=="coordinated"
