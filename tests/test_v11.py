from datetime import datetime, timedelta, timezone
import time

import numpy as np
import pytest
from netCDF4 import Dataset
from fastapi.testclient import TestClient

from balloon_predictor.api import create_app
from balloon_predictor.errors import CoverageError, PredictorError
from balloon_predictor.exports import csv_export, geojson, kml_export
from balloon_predictor.models import BatchPredictionRequest, PredictionRequest
from balloon_predictor.predictor import predict, predict_batch
from balloon_predictor.profiles import dewpoint_c
from balloon_predictor.settings import Settings
from balloon_predictor.store import WeatherStore
from balloon_predictor.weather import DemoWeather
from balloon_predictor.weather.native import ColumnCache, interpolate_column
from balloon_predictor.weather.schema import geometric_height
from conftest import make_wrf_file
from scripts.make_test_data import make_file, make_topography


def local_settings(tmp):
    return Settings(tmp/'data', era5_root=tmp/'era5', wrf_root=tmp/'wrf', cache_mb=16)


def era_archive(root, humidity=False, missing=False):
    opts=dict(lat=[55,53,51],lon=[354,356,358],constant=True,humidity=humidity,reverse_levels=True,reorder_dims=True)
    make_file(root/'2024'/'era5_2024d015.nc',date='2024-01-15',times=(21,23),missing=missing,**opts)
    make_file(root/'2024'/'era5_2024d016.nc',date='2024-01-16',times=(0,3),**opts)
    make_topography(root/'era5_topo.nc',lat=[55,53,51],lon=[354,356,358],heights=100)


def test_era5_native_packing_reordered_axes_topography_and_midnight(tmp_path):
    settings=local_settings(tmp_path);era_archive(settings.era5_root)
    store=WeatherStore(settings)
    when=datetime(2024,1,15,23,30,tzinfo=timezone.utc)
    weather=store.get('era5',when)
    sample=weather.sample(52,-5,5000,when)
    assert sample.u==pytest.approx(11,abs=.004)
    assert sample.v==pytest.approx(0,abs=.004)
    assert sample.temperature==pytest.approx(250,abs=.004)
    assert sample.specific_humidity is None
    assert weather.terrain(52,-5)==pytest.approx(geometric_height(100),abs=.001)
    assert store.native.cache.bytes < 100000
    assert not list((settings.data_dir/'weather').glob('*.nc'))
    request=PredictionRequest(dataset='era5',launch_datetime=when,launch_latitude=52,launch_longitude=-5,launch_altitude=101,burst_altitude=1500,flight_mode='ascent')
    result=predict(request,weather)
    assert result['summary']['landing'] is None
    assert result['summary']['endpoint']['altitude']==1500
    assert result['sounding']['humidity_available'] is False
    assert all(r['dewpoint_c'] is None for r in result['sounding']['rows'])


def test_era5_humidity_and_missing_terrain_are_explicit(tmp_path):
    settings=local_settings(tmp_path);era_archive(settings.era5_root,humidity=True)
    store=WeatherStore(settings);when=datetime(2024,1,15,23,tzinfo=timezone.utc)
    assert store.get('era5',when).sample(52,-5,1000,when).specific_humidity==pytest.approx(.01)
    (settings.era5_root/'era5_topo.nc').unlink();store.native.scan()
    with pytest.raises(PredictorError,match='era5_topo'):store.get('era5',when)


def test_missing_dates_and_large_temporal_gaps_are_rejected(tmp_path):
    settings=local_settings(tmp_path);era_archive(settings.era5_root)
    store=WeatherStore(settings)
    with pytest.raises(CoverageError):store.get('era5',datetime(2024,1,14,tzinfo=timezone.utc))
    path=settings.wrf_root/'wrfout_d02_2024-01-15_12:00:00';path.parent.mkdir(parents=True)
    make_wrf_file(path,times=['2024-01-15_12:00:00','2024-01-16_12:00:00'])
    store.native.scan();ident=store.native.wrf[0]['id']
    weather=store.get(ident,datetime(2024,1,15,12,tzinfo=timezone.utc))
    with pytest.raises(CoverageError,match='time gap'):weather.sample(52,-5,1000,datetime(2024,1,15,18,tzinfo=timezone.utc))


def test_wrf_direct_columns_rotation_humidity_and_time_interpolation(tmp_path,utc):
    settings=local_settings(tmp_path);settings.wrf_root.mkdir(parents=True)
    path=settings.wrf_root/'wrfout_d02_2024-01-15_12:00:00'
    make_wrf_file(path,angle=90)
    with Dataset(path,'a') as nc:
        nc.SIMULATION_START_DATE='2024-01-15_00:00:00'
        q=nc.createVariable('QVAPOR','f4',nc.variables['T'].dimensions);q[:]=.01
        nc.variables['U'][1]=20
    store=WeatherStore(settings);info=store.native.wrf[0]
    weather=store.get(info['id'],utc)
    sample=weather.sample(52,-5,1000,utc+timedelta(hours=3))
    assert sample.u==pytest.approx(-2,abs=1e-5)
    assert sample.v==pytest.approx(15,abs=1e-5)
    assert sample.w==pytest.approx(.25)
    assert sample.specific_humidity==pytest.approx(.01/1.01,rel=1e-6)
    request=PredictionRequest(dataset=info['id'],launch_datetime=utc,launch_latitude=52,launch_longitude=-5,burst_altitude=1500,flight_mode='ascent')
    result=predict(request,weather)
    assert result['sounding']['humidity_available']
    assert result['sounding']['rows'][-1]['altitude']==1500
    assert store.native.cache.bytes < 10000
    assert not list((settings.data_dir/'weather').glob('*.nc'))


def test_wrf_run_and_domain_selection_and_moving_nest_rejection(tmp_path,utc):
    settings=local_settings(tmp_path)
    for folder,domain in [('run-a','01'),('run-a','02'),('run-b','02')]:
        path=settings.wrf_root/folder/f'wrfout_d{domain}_2024-01-15_12:00:00';path.parent.mkdir(parents=True,exist_ok=True)
        make_wrf_file(path,moving=folder=='run-b')
    store=WeatherStore(settings);assert len(store.native.wrf)==3
    bad=next(i for i in store.native.wrf if 'run-b' in i['records'][0][1])
    weather=store.get(bad['id'],utc)
    with pytest.raises(PredictorError,match='Moving nests'):weather.sample(52,-5,1000,utc+timedelta(hours=3))


def test_duplicate_wrf_times_never_mix_runs(tmp_path):
    settings=local_settings(tmp_path);settings.wrf_root.mkdir(parents=True)
    make_wrf_file(settings.wrf_root/'wrfout_d02_a')
    make_wrf_file(settings.wrf_root/'wrfout_d02_b')
    store=WeatherStore(settings);info=store.native.wrf[0]
    assert not info['available']
    with pytest.raises(PredictorError,match='Duplicate'):store.get(info['id'])


def test_ascent_stops_at_exact_target_and_ensemble_targets_export(utc):
    request=PredictionRequest(dataset='demo',launch_datetime=utc,burst_altitude=1234,flight_mode='ascent',ensemble_members=3,burst_sigma=40)
    result=predict(request,DemoWeather());s=result['summary']
    assert s['duration_seconds']==pytest.approx((1234-100)/5,abs=.001)
    assert len(result['prediction'])==1 and s['landing'] is None
    assert s['endpoint_kind']=='target' and s['endpoint']==s['burst']
    assert all(m['summary']['landing'] is None for m in result['ensemble'])
    assert 'sensitivity_target' in {f['properties'].get('event') for f in geojson(result)['features']}
    assert 'ascent' in csv_export(result)
    assert '<name>landing</name>' not in kml_export(result)


def test_series_end_exclusive_multisite_and_partial_coverage(tmp_path,utc):
    settings=local_settings(tmp_path);settings.wrf_root.mkdir(parents=True)
    make_wrf_file(settings.wrf_root/'wrfout_d02_test')
    store=WeatherStore(settings);ident=store.native.wrf[0]['id']
    a=PredictionRequest(dataset=ident,flight_name='WRF point',launch_datetime=utc,launch_latitude=52,launch_longitude=-5,burst_altitude=1000,flight_mode='ascent')
    b=a.model_copy(update={'dataset':'demo','flight_name':'Demo point'})
    result=predict_batch(BatchPredictionRequest(flights=[a,b],duration_hours=12,interval_hours=3),store)
    assert result['total']==8 and result['completed']==6 and result['failed']==2
    assert [e['launch_datetime'][11:16] for e in result['flights'][::2]]==['12:00','15:00','18:00','21:00']
    assert result['flights'][4]['status']=='failed'
    assert result['flights'][5]['status']=='completed'
    assert geojson(result)['type']=='FeatureCollection'
    assert csv_export(result).startswith('flight_index,flight_name,member')
    from xml.etree.ElementTree import fromstring
    assert len(fromstring(kml_export(result)).findall('.//{http://www.opengis.net/kml/2.2}Folder'))==6


def test_schedule_work_limits(utc):
    f=PredictionRequest(launch_datetime=utc)
    with pytest.raises(ValueError,match='256'):BatchPredictionRequest(flights=[f,f],duration_hours=168,interval_hours=1)
    with pytest.raises(ValueError,match='1024'):BatchPredictionRequest(flights=[f.model_copy(update={'ensemble_members':50})],duration_hours=24,interval_hours=1)


def test_column_holes_no_fabricated_moisture_and_cache_bound():
    c={'height':np.array([100,1000,2000,3000]),'u':np.array([5,5,np.nan,5]),'v':np.zeros(4),'temperature':np.full(4,280),'pressure':np.array([100000,90000,80000,70000]),'terrain':0}
    with pytest.raises(CoverageError,match='missing'):interpolate_column(c,1500)
    assert 'specific_humidity' not in interpolate_column(c,1000)
    assert dewpoint_c(100000,None) is None and dewpoint_c(100000,0) is None
    assert dewpoint_c(100000,.01)==pytest.approx(14,abs=.2)
    cache=ColumnCache(.001)
    for i in range(20):cache.put(i,{'u':np.ones(50)})
    assert cache.bytes<=cache.limit and len(cache.items)<20


def test_new_api_series_soundings_exports_and_persistence(tmp_path):
    settings=local_settings(tmp_path)
    with TestClient(create_app(settings)) as client:
        assert client.get('/api/weather').json()['defaults']['dataset']=='era5'
        assert client.post('/predict',json={'dataset':'era5','launch_datetime':'2024-01-15T12:00:00Z'}).status_code==422
        body={'flights':[{'dataset':'demo','launch_datetime':'2024-01-15T12:00:00Z','flight_mode':'ascent','burst_altitude':1000}], 'duration_hours':3,'interval_hours':1}
        response=client.post('/api/predictions/series',json=body);assert response.status_code==202
        ident=response.json()['id']
        for _ in range(200):
            job=client.get('/api/jobs/'+ident).json()
            if job['status'] in ('completed','failed'):break
            time.sleep(.01)
        assert job['status']=='completed',job
        assert job['result']['completed']==3
        for mode in ['ascent','launch']:
            profile=client.get(f'/api/predictions/{ident}/sounding',params={'flight_index':1,'mode':mode})
            assert profile.status_code==200 and profile.json()['mode']==mode
            assert client.get(f'/api/predictions/{ident}/sounding/export/csv',params={'flight_index':1,'mode':mode}).status_code==200
        assert client.get(f'/api/predictions/{ident}/sounding',params={'flight_index':999}).status_code==404
        for fmt in ['json','csv','geojson','kml']:assert client.get(f'/api/predictions/{ident}/export/{fmt}').status_code==200
    with TestClient(create_app(settings)) as client:assert client.get('/api/jobs/'+ident).json()['result']['total']==3
