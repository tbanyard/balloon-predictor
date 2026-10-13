from datetime import timedelta
import json
from pathlib import Path

import numpy as np
import pytest

from balloon_predictor.errors import PredictorError
from balloon_predictor.weather import DatasetWeather
from balloon_predictor.weather.readers import read_grib, read_netcdf
from balloon_predictor.weather.schema import describe_dataset, geometric_height
from balloon_predictor.weather.wrf import read_wrf
from conftest import make_grib, make_pressure_file, make_wrf_file


@pytest.mark.parametrize("forecast,expver",[(False,False),(True,False),(False,True)])
def test_pressure_netcdf_valid_time_axes_and_expver(tmp_path,utc,forecast,expver):
    path=tmp_path/"weather.nc";make_pressure_file(path,forecast,expver)
    ds=read_netcdf([path],attrs={"source":"era5"})
    assert ds.sizes["time"]==2
    assert list(ds.latitude[:,0].values)==[51,53,55]
    assert list(ds.longitude[0].values)==[-6,-4,-2]
    assert ds.height[0,2,0,0].item()==pytest.approx(geometric_height(31000),rel=1e-6)
    weather=DatasetWeather(ds,describe_dataset(ds,"era5"))
    assert weather.sample(52,-5,1000,utc+timedelta(hours=1)).u==pytest.approx(10)
    assert "terrain" not in ds


def test_native_grib_expver_wind_omega_and_surface(tmp_path,utc):
    path=tmp_path/"weather.grib";make_grib(path,version=5)
    ds=read_grib([path])
    provenance=json.loads(ds.attrs["provenance_json"])
    assert provenance["expver"]==[5]
    assert "w" in ds and "terrain" in ds
    weather=DatasetWeather(ds,describe_dataset(ds,"era5t"))
    assert weather.sample(52,-5,1000,utc).u==10
    assert weather.sample(52,-5,0,utc).u==4
    assert weather.sample(52,-5,1000,utc).w>0  # Negative pressure velocity means upward motion.
    assert weather.terrain(52,-5)==0


def test_grib_cropping_and_final_release_priority(tmp_path):
    a,b=tmp_path/"a.grib",tmp_path/"b.grib";make_grib(a,5);make_grib(b,1)
    ds=read_grib([a,b],area=[54,-6,51,-3])
    assert ds.sizes["y"]==2 and ds.sizes["x"]==2
    assert json.loads(ds.attrs["provenance_json"])["expver"]==[1,5]


def test_wrf_rotation_staggering_temperature_and_surface(tmp_path,utc):
    path=tmp_path/"wrfout_d02_test";make_wrf_file(path,angle=90)
    ds=read_wrf([path])
    assert ds.u[0,0,0,0].item()==pytest.approx(-2,abs=1e-5)
    assert ds.v[0,0,0,0].item()==pytest.approx(10)
    assert ds.height[0,0,0,0].item()==pytest.approx(500)
    assert ds.temperature[0,0,0,0].item()==pytest.approx(300*(95000/100000)**(287/1004),rel=1e-5)
    weather=DatasetWeather(ds,describe_dataset(ds,"wrf"))
    assert weather.sample(52,-5,0,utc).u==pytest.approx(-1)
    assert weather.sample(52,-5,1000,utc).w==pytest.approx(.25)


def test_wrf_multifile_sorted_selection_and_crop(tmp_path):
    a,b=tmp_path/"wrfout1",tmp_path/"wrfout2"
    make_wrf_file(a,times=["2024-01-15_12:00:00"])
    make_wrf_file(b,times=["2024-01-15_18:00:00"])
    ds=read_wrf([b,a],area=[54,-6,51,-3])
    assert ds.sizes["time"]==2
    assert ds.time.values[0]==np.datetime64("2024-01-15T12:00")
    selection=read_wrf([a,b],start=np.datetime64("2024-01-15T13:00"))
    assert selection.sizes["time"]==1


def test_wrf_rejects_moving_nest_or_missing_rotation(tmp_path):
    path=tmp_path/"moving";make_wrf_file(path,moving=True)
    with pytest.raises(PredictorError,match="nest moves"):read_wrf([path])
    clean=tmp_path/"clean";ds=make_wrf_file(clean);ds=ds.drop_vars("COSALPHA");ds.to_netcdf(tmp_path/"missing")
    with pytest.raises(PredictorError,match="COSALPHA"):read_wrf([tmp_path/"missing"])
