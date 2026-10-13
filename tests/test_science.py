from datetime import timedelta
import math

import numpy as np
import pytest

from balloon_predictor.errors import CoverageError, PredictorError
from balloon_predictor.models import PredictionRequest
from balloon_predictor.predictor import predict, run_flight
from balloon_predictor.weather import DatasetWeather, DemoWeather, Sample
from balloon_predictor.weather.schema import EARTH_RADIUS, describe_dataset
from conftest import make_normalized


class ConstantWeather:
    info = {**DemoWeather.info, "source":"test","warnings":[]}
    def sample(self,lat,lon,altitude,when):
        return Sample(10,0,288.15,101325)
    def terrain(self,lat,lon):
        return 0


def test_constant_wind_matches_analytical_solution(utc):
    request=PredictionRequest(launch_datetime=utc,launch_latitude=52,launch_altitude=100,burst_altitude=1000,
                              ascent_rate=5,descent_rate=5,descent_model="constant",time_step=17)
    result=run_flight(request,ConstantWeather())
    s=result["summary"]
    assert s["duration_seconds"] == pytest.approx(380,abs=.001)
    assert s["burst"]["altitude"] == 1000
    assert s["landing"]["altitude"] == 0
    assert s["landing"]["latitude"] == 52
    integral=(math.log((EARTH_RADIUS+1000)/(EARTH_RADIUS+100)) + math.log((EARTH_RADIUS+1000)/EARTH_RADIUS))/5
    expected=10*integral/math.cos(math.radians(52))*180/math.pi
    assert s["landing"]["longitude"]-request.launch_longitude == pytest.approx(expected,abs=1e-7)
    times=[p["elapsed_seconds"] for phase in result["prediction"] for p in phase["trajectory"]]
    assert np.all(np.diff(times)>=0)
    assert result["prediction"][0]["trajectory"][-1] == result["prediction"][1]["trajectory"][0]


def test_density_descent_faster_at_altitude(utc):
    r=PredictionRequest(launch_datetime=utc,burst_altitude=25000)
    density=run_flight(r,DemoWeather())
    constant=run_flight(r.model_copy(update={"descent_model":"constant"}),DemoWeather())
    assert density["summary"]["descent_seconds"] < constant["summary"]["descent_seconds"]


def test_interpolation_in_altitude_and_time(utc):
    ds=make_normalized();weather=DatasetWeather(ds,describe_dataset(ds,"test"))
    sample=weather.sample(52,-5,5000,utc+timedelta(hours=3))
    assert sample.u == pytest.approx(10,abs=1e-5)
    assert sample.v == pytest.approx(3)
    assert sample.pressure > 0
    assert weather.sample(52,-5,0,utc).u == 2
    assert weather.terrain(52,-5)==0


def test_outside_time_domain_and_top_are_errors(utc):
    ds=make_normalized();weather=DatasetWeather(ds,describe_dataset(ds,"test"))
    for lat,lon,z,t in [(52,-5,36000,utc),(50,-5,1000,utc),(52,-7,1000,utc),
                        (52,-5,1000,utc-timedelta(seconds=1)),(52,-5,1000,utc+timedelta(hours=7))]:
        with pytest.raises(CoverageError):weather.sample(lat,lon,z,t)


def test_terrain_landing_event(utc):
    class Hill(ConstantWeather):
        def terrain(self,lat,lon):return 50+100*(lon+4.125)
    r=PredictionRequest(launch_datetime=utc,burst_altitude=2000,descent_model="constant")
    weather=Hill();p=run_flight(r,weather)["summary"]["landing"]
    assert p["altitude"]==pytest.approx(weather.terrain(p["latitude"],p["longitude"]),abs=1e-6)


def test_curvilinear_grid(utc):
    ds=make_normalized()
    ds["latitude"].values[:]+=np.array([0,.1,.2])[None,:]
    ds["longitude"].values[:]+=np.array([0,.1,.2])[:,None]
    weather=DatasetWeather(ds,describe_dataset(ds,"curve"))
    assert not weather.regular
    assert weather.sample(53.1,-3.9,5000,utc).u==pytest.approx(7,abs=1e-5)
    with pytest.raises(CoverageError):weather.sample(50,-5,5000,utc)


def test_periodic_global_longitude_seam(utc):
    ds=make_normalized()
    ds["longitude"].values[:]=np.array([-180,-60,60])[None,:]
    weather=DatasetWeather(ds,describe_dataset(ds,"global"))
    assert weather.periodic
    assert weather.sample(52,179.9,5000,utc).u==pytest.approx(7,abs=1e-5)
    assert weather.sample(52,-180.1,5000,utc).u==pytest.approx(7,abs=1e-5)


def test_sensitivity_is_seeded(utc):
    r=PredictionRequest(launch_datetime=utc,burst_altitude=1000,ensemble_members=3,burst_sigma=30)
    a,b=predict(r,DemoWeather()),predict(r,DemoWeather())
    assert a["ensemble"]==b["ensemble"]
    assert len(a["ensemble"])==3
    assert "not a meteorological ensemble" in a["warnings"][-1]


def test_invalid_rates_dates_and_duration(utc):
    with pytest.raises(ValueError):PredictionRequest(launch_datetime="2024-01-15T12:00:00")
    with pytest.raises(ValueError):PredictionRequest(launch_datetime=utc,ascent_rate=0)
    with pytest.raises(ValueError):PredictionRequest(launch_datetime=utc,burst_altitude=50)
    with pytest.raises(PredictorError):
        run_flight(PredictionRequest(launch_datetime=utc,max_duration_hours=.01),ConstantWeather())
    assert PredictionRequest(launch_datetime=utc,launch_longitude=355.875).launch_longitude==-4.125
