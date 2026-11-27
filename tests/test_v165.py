from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import base64
import threading
import time

from netCDF4 import Dataset
import numpy as np
import pytest

from balloon_predictor.api import Jobs, create_app
from balloon_predictor.context import flight_weather
from balloon_predictor.errors import CoverageError, JobCancelled
from balloon_predictor.models import PredictionRequest, WeatherOverlayRequest
from balloon_predictor.settings import Settings
from balloon_predictor.store import WeatherStore
from balloon_predictor.weather.native import WRFWeather, ColumnCache, scan_wrf
from balloon_predictor.weather_pressure import sample_pressure, pressure_column
from balloon_predictor.wrf_overlay import plane, read_plane, sample, dimensions
from balloon_predictor.modelbox import wrf_block
from conftest import make_wrf_file


@pytest.mark.parametrize('pressure', [None, 100000., 95000., 70000., 1500., 110000., 100.])
def test_wrf_plane_matches_native_columns(tmp_path, utc, pressure):
    path=tmp_path/'wrfout_d02_test';make_wrf_file(path,angle=37).close()
    weather=WRFWeather(scan_wrf(tmp_path)[0][0],ColumnCache(16),21600)
    req=PredictionRequest(launch_datetime=utc)
    for altitude in (-100,10,499.6,500,1500,10000,25000,25000.4,26000):
        with Dataset(path) as nc:
            values=read_plane(nc,weather,0,np.arange(3),np.arange(3),altitude,pressure,'bridge')
        context=flight_weather(weather,req)
        for y,x in [(0,0),(1,1),(2,2)]:
            try:
                if pressure is None:
                    expected=context.sample(weather.lat[y,x],weather.lon[y,x],altitude,utc);height=altitude
                else:
                    expected,height=sample_pressure(weather,float(weather.lat[y,x]),float(weather.lon[y,x]),pressure,utc)
            except CoverageError:
                assert np.isnan(values['u'][y,x])
            else:
                for key in ('u','v','temperature','pressure'):
                    assert values[key][y,x]==pytest.approx(getattr(expected,key),rel=2e-6,abs=3e-5)
                assert values['height'][y,x]==pytest.approx(height,rel=2e-6,abs=.001)


@pytest.mark.parametrize('policy',['bridge','strict'])
def test_wrf_vector_masks_surface_and_holes(tmp_path,utc,policy):
    path=tmp_path/'wrfout_d02_test';ds=make_wrf_file(path,angle=17)
    ds['T']=ds['T'].astype(float);ds['U']=ds['U'].astype(float)
    ds['T'][0,1,1,1]=np.nan;ds['U'][0,0,0,:]=np.nan;ds['HGT'][:]=50
    ds.to_netcdf(path);ds.close()
    weather=WRFWeather(scan_wrf(tmp_path)[0][0],ColumnCache(16),21600)
    request=PredictionRequest(launch_datetime=utc,below_model_policy=policy)
    fields,surface=wrf_block(weather,0,np.arange(3),np.arange(3))
    for altitude in (-100,60,500,3000,15000,25000):
        for pressure in (None,100000,70000,1500):
            answer=plane(fields,surface,altitude,pressure,policy)
            context=flight_weather(weather,request)
            for y,x in [(0,0),(1,1),(2,2)]:
                try:
                    if pressure is None:expected=context.model._column(0,y,x,altitude)
                    else:expected=pressure_column(weather.raw_column(0,y,x),pressure)
                except CoverageError:assert np.isnan(answer['u'][y,x])
                else:
                    for name in ('u','v','temperature','pressure'):assert answer[name][y,x]==pytest.approx(expected[name],rel=2e-6,abs=3e-5)


def test_wrf_tiles_cache_time_curved_grid_and_invalidation(tmp_path,utc,monkeypatch):
    path=tmp_path/'wrfout_d02_test';ds=make_wrf_file(path,angle=19)
    ds['XLAT'][:,1,1]+=.1;ds['U'][1]+=3;ds['T'][1]+=2;ds.to_netcdf(path);ds.close()
    weather=WRFWeather(scan_wrf(tmp_path)[0][0],ColumnCache(16),21600)
    from balloon_predictor import wrf_overlay
    original=wrf_overlay.read_plane;reads=[]
    def counted(*args,**kwargs):reads.append(1);return original(*args,**kwargs)
    monkeypatch.setattr(wrf_overlay,'read_plane',counted)
    when=utc+timedelta(hours=1.25);ys=np.linspace(51.1,54.9,41);xs=np.linspace(-5.9,-2.1,43)
    fields,mask,_=sample(weather,when,ys,xs,1500,None,'bridge',1,lambda m:None)
    assert len(reads)==2 and mask.all()
    expected=flight_weather(weather,PredictionRequest(launch_datetime=utc)).sample(ys[20],xs[21],1500,when)
    assert fields['u'][20,21]==pytest.approx(expected.u,rel=2e-6)
    second=sample(weather,when,ys,xs,1500,None,'bridge',1,lambda m:None)
    assert len(reads)==2 and np.array_equal(second[0]['u'],fields['u'])
    path.touch()
    with pytest.raises(Exception,match='changed'):sample(weather,when,ys,xs,1500,None,'bridge',1,lambda m:None)


def test_wrf_adaptive_dimensions_and_encoded_api(tmp_path,utc):
    from balloon_predictor.weather_overlay import sample_overlay
    from balloon_predictor.predictor import predict
    path=tmp_path/'wrf'/'wrfout_d02_test';path.parent.mkdir();ds=make_wrf_file(path)
    # Fine native grid and a small view: allow many more than 40 x 40 cells.
    ds['XLAT'][:]=51+np.arange(3)[None,:,None]*.01;ds['XLONG'][:]=-4+np.arange(3)[None,None,:]*.01
    ds.to_netcdf(path);ds.close()
    store=WeatherStore(Settings(tmp_path/'data',wrf_root=path.parent));info=next(d for d in store.list() if d['source']=='wrf');weather=store.get(info['id'])
    # Exercise the budget independently of fixture's three native cells.
    ny,nx,step=dimensions(weather,[55,-6,51,-2],WeatherOverlayRequest(wrf_detail=True,viewport_width=1600,viewport_height=1200))
    assert nx>40 and ny>40 and nx*ny<=262144 and step>=1
    req=PredictionRequest(dataset=info['id'],launch_datetime=utc,launch_latitude=51.01,launch_longitude=-3.99,flight_mode='ascent',burst_altitude=1000,ascent_rate=30)
    flight=predict(req,weather)
    answer=sample_overlay(flight,WeatherOverlayRequest(wrf_detail=True,coverage='auto',area=[51.015,-3.995,51.005,-3.985],altitude_m=1000),store)
    assert answer['coverage_mode']=='viewport' and answer['wrf_native']
    assert answer['grid']['encoding']=='base64-float32-le'
    values=np.frombuffer(base64.b64decode(answer['grid']['u']),dtype='<f4')
    assert values.size==answer['total_cells'] and np.isfinite(values).all()
    assert answer['bounds'][0]<weather.info['area'][0]


def test_queued_and_running_job_cancellation_releases_storage(tmp_path):
    settings=Settings(tmp_path/'data');store=WeatherStore(settings);executor=ThreadPoolExecutor(max_workers=1)
    jobs=Jobs(settings,store,executor);started=threading.Event();finish=threading.Event()
    def task(progress):
        started.set()
        while not finish.wait(.01):progress.check_cancelled()
        return {'example':True}
    first=jobs.submit('prediction',task);assert started.wait(2)
    second=jobs.submit('prediction',lambda progress:pytest.fail('Queued cancelled job ran'))
    assert jobs.cancel(second['id'])['status']=='cancelled'
    jobs.cancel(first['id'])
    for _ in range(100):
        if jobs.get(first['id'])['status']=='cancelled':break
        time.sleep(.01)
    assert jobs.get(first['id'])['status']=='cancelled'
    assert not list((settings.data_dir/'results').glob('*.json'))
    finish.set();executor.shutdown()
    # Cancellation is an ordinary terminal state, not an error log.
    assert not settings.log_path.exists()


def test_prediction_cancellation_reaches_integration(tmp_path,utc):
    store=WeatherStore(Settings(tmp_path/"data"))
    from balloon_predictor.predictor import predict
    calls=[]
    def check():
        calls.append(1)
        if len(calls)>5:raise JobCancelled()
    def progress(message):pass
    progress.check_cancelled=check
    with pytest.raises(JobCancelled):predict(PredictionRequest(launch_datetime=utc),store.get('demo'),progress)
    assert len(calls)==6


def test_large_wrf_slice_matches_native_and_reuses_opened_tiles(tmp_path,utc,monkeypatch):
    import importlib.util
    spec=importlib.util.spec_from_file_location('wrf_fixture',__import__('pathlib').Path(__file__).resolve().parents[1]/'scripts/wrf-overlay-fixture.py');module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    path=module.create(tmp_path/'wrfout_d02_test',ny=96,nx=112,nz=60)
    weather=WRFWeather(scan_wrf(tmp_path)[0][0],ColumnCache(32),21600)
    from balloon_predictor import wrf_overlay
    reads=[];original=wrf_overlay.Dataset
    def opened(*args,**kwargs):reads.append(1);return original(*args,**kwargs)
    monkeypatch.setattr(wrf_overlay,'Dataset',opened)
    latitude,longitude=np.linspace(52.05,52.8,81),np.linspace(-4.9,-3.5,101)
    for pressure in (None,50000.):
        when=utc+timedelta(hours=1)
        fields,mask,_=sample(weather,when,latitude,longitude,9600,pressure,'bridge',1,lambda m:None)
        context=flight_weather(weather,PredictionRequest(launch_datetime=utc))
        for y,x in [(0,0),(40,50),(80,100)]:
            assert mask[y,x]
            expected,height=(sample_pressure(weather,latitude[y],longitude[x],pressure,when) if pressure else (context.sample(latitude[y],longitude[x],9600,when),9600))
            for name in ('u','v'):assert fields[name][y,x]==pytest.approx(getattr(expected,name),rel=2e-6,abs=.00005)
            assert fields['temperature_c'][y,x]==pytest.approx(expected.temperature-273.15,abs=.0001)
            assert fields['altitude_m'][y,x]==pytest.approx(height,abs=.003)
    assert len(reads)==4,'Each time frame should stay open across every tile, once per height/pressure.'
    sample(weather,when,latitude,longitude,9600,50000,'bridge',1,lambda m:None)
    assert len(reads)==4,'Repeated map views should use prepared tiles, without reopening weather volumes.'


def test_saved_source_receipts_survive_javascript_number_roundtrip(tmp_path,utc):
    import json
    from balloon_predictor.predictor import predict
    from balloon_predictor.provenance import validate_manifest
    from balloon_predictor.saved_flights import validate_result
    from conftest import make_normalized
    store=WeatherStore(Settings(tmp_path/'data'));store.save('example',make_normalized())
    flight=predict(PredictionRequest(dataset='example',launch_datetime=utc,flight_mode='ascent',burst_altitude=1000),store.get('example'))
    assert all(isinstance(r['mtime_ns'],str) for r in flight['input_manifest'])
    # Browser JSON numbers have IEEE-754 precision; strings must stay exact.
    restored=json.loads(json.dumps(flight),parse_int=lambda n:int(float(n)))
    validate_result(restored);validate_manifest(restored)
    path=__import__('pathlib').Path(restored['input_manifest'][0]['path']);path.touch()
    with pytest.raises(Exception,match='changed'):validate_manifest(restored)
