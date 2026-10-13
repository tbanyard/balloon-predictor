from datetime import timedelta
import numpy as np
import pytest
import xarray as xr

from balloon_predictor.errors import CoverageError, PredictorError
from balloon_predictor.models import PredictionRequest
from balloon_predictor.predictor import run_flight
from balloon_predictor.weather import DatasetWeather
from balloon_predictor.weather.readers import read_grib, read_netcdf
from balloon_predictor.weather.schema import describe_dataset
from conftest import make_grib, make_normalized, make_pressure_file


def test_endpoint_cannot_extend_past_weather_window(utc):
    ds=make_normalized()
    weather=DatasetWeather(ds,describe_dataset(ds,"test"))
    r=PredictionRequest(launch_datetime=utc+timedelta(hours=6,seconds=-20),
                        launch_latitude=52,launch_longitude=-5,launch_altitude=100,burst_altitude=200,
                        landing_mode="fixed",descent_model="constant")
    with pytest.raises(CoverageError,match="Time"):
        run_flight(r,weather)


def test_static_netcdf_terrain_with_full_time_coordinate(tmp_path):
    pressure=tmp_path/"pressure.nc";original=make_pressure_file(pressure)
    surface=xr.Dataset({"z":(("latitude","longitude"),np.zeros((3,3)))},
                       coords={"valid_time":original.valid_time,"latitude":original.latitude,"longitude":original.longitude})
    surface.to_netcdf(tmp_path/"surface.nc")
    ds=read_netcdf([pressure,tmp_path/"surface.nc"],start=np.datetime64("2024-01-15T13:00"))
    assert "terrain" in ds and ds.sizes["time"]==1


def test_grib_keeps_terrain_from_cycle_before_selected_window(tmp_path):
    path=tmp_path/"weather.grib";make_grib(path)
    ds=read_grib([path],start=np.datetime64("2024-01-15T15:00"))
    assert "terrain" in ds
    assert ds.sizes["time"]==1


def test_single_time_slice_cannot_be_used_as_constant_weather(utc):
    ds=make_normalized().isel(time=slice(0,1))
    weather=DatasetWeather(ds,describe_dataset(ds,"single"))
    assert weather.sample(52,-5,1000,utc).u>0
    with pytest.raises(CoverageError,match="Time"):weather.sample(52,-5,1000,utc+timedelta(seconds=1))


def test_pressure_coordinates_in_pascal(tmp_path):
    path=tmp_path/"weather.nc";ds=make_pressure_file(path)
    ds=ds.assign_coords(pressure_level=ds.pressure_level*100)
    ds.pressure_level.attrs["units"]="Pa"
    ds.to_netcdf(tmp_path/"pascal.nc")
    normalized=read_netcdf([tmp_path/"pascal.nc"])
    assert normalized.pressure[0,0,0,0].item()==100000


def test_missing_temperature_is_rejected(tmp_path):
    path=tmp_path/"weather.nc";ds=make_pressure_file(path)
    ds.drop_vars("t").to_netcdf(tmp_path/"missing.nc")
    with pytest.raises(PredictorError,match="temperature"):read_netcdf([tmp_path/"missing.nc"])
