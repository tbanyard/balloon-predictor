from datetime import timedelta
import math
import inspect

import numpy as np
import pytest
from pydantic import ValidationError

from balloon_predictor.models import DownloadRequest, WeatherOverlayRequest
from balloon_predictor.predictor import predict
from balloon_predictor.store import WeatherStore
from balloon_predictor.settings import Settings
from balloon_predictor.weather_pressure import pressure_column, sample_pressure
from balloon_predictor.weather_overlay import sample_overlay, prepare_native
from balloon_predictor.weather.native import ERA5Weather, WRFWeather, ColumnCache, scan_wrf
from balloon_predictor.weather.era5_native.data import Archive
from balloon_predictor.weather.download import forecast_steps
from balloon_predictor.errors import CoverageError
from balloon_predictor.migrate_storage import prepare
from conftest import make_normalized, make_wrf_file
from scripts.make_test_data import make_file
from test_v161 import request


@pytest.fixture
def saved(tmp_path,utc):
    store=WeatherStore(Settings(tmp_path/'data',era5_root=tmp_path/'era5',wrf_root=tmp_path/'wrf'))
    store.save('test',make_normalized())
    return store,predict(request(utc,dataset='test'),store.get('test'))


def test_regional_overlay_uses_the_whole_model_box(saved):
    store,result=saved
    data=sample_overlay(result,WeatherOverlayRequest(coverage='auto',area=(53.1,-4.1,52.9,-3.9),grid_size=8),store)
    assert data['coverage_mode']=='model'
    assert data['bounds']==[55,-6,51,-2]
    assert data['valid_cells']==64
    # Explicit v1.6.1 API areas are still honoured when coverage is requested.
    cropped=sample_overlay(result,WeatherOverlayRequest(area=(53.1,-4.1,52.9,-3.9),grid_size=8),store)
    assert cropped['bounds']==[53.1,-4.1,52.9,-3.9]


def test_global_overlay_uses_the_buffered_view_and_handles_dateline(saved,utc):
    store,_=saved
    result=predict(request(utc,launch_longitude=179.9,launch_latitude=0),store.get('demo'))
    data=sample_overlay(result,WeatherOverlayRequest(coverage='auto',area=(15,170,-15,190),grid_size=8),store)
    assert data['coverage_mode']=='viewport' and data['bounds']==[15,170,-15,190]
    assert data['valid_cells']==64


def test_custom_altitude_changes_weather_without_changing_flight(saved):
    store,result=saved
    data=sample_overlay(result,WeatherOverlayRequest(altitude_m=8000,grid_size=8),store)
    assert data['height_kind']=='custom' and data['altitude_m']==8000
    assert np.allclose(data['grid']['u'],10)
    assert result['summary']['burst']['altitude']==1500


def test_pressure_uses_model_height_not_standard_atmosphere(saved):
    store,result=saved
    data=sample_overlay(result,WeatherOverlayRequest(pressure_hpa=500,grid_size=8),store)
    # In this model 500 hPa is exactly half way in log p from 100 m/1000
    # hPa to 10 km/250 hPa, i.e. 5050 m (not an ISA height conversion).
    assert data['altitude_m'] is None and data['pressure_hpa']==500
    assert data['vertical_coordinate']=='pressure'
    assert np.allclose(data['grid']['altitude_m'],5050)
    assert np.allclose(data['grid']['u'],7.05)
    assert np.allclose(data['grid']['temperature_c'],-3.15)
    assert result['summary']['burst']['altitude']==1500


def test_isobaric_time_interpolation_and_surface_anchor(saved,utc):
    store,_=saved
    model=store.get('test')
    sample,height=sample_pressure(model,53,-4,50000,utc+timedelta(hours=3))
    assert sample.u==pytest.approx(10.05) and sample.pressure==50000
    assert height==pytest.approx(5050)
    surface,height=sample_pressure(model,53,-4,100500,utc)
    f=math.log(101000/100500)/math.log(101000/100000)
    assert height==pytest.approx(10+90*f)
    assert surface.temperature==pytest.approx(280-10*f)


def test_pressure_height_varies_with_the_model_columns(tmp_path,utc):
    ds=make_normalized()
    ds['pressure'][:,:,:,2]=ds.pressure.values[:,:,:,2]*.8
    store=WeatherStore(Settings(tmp_path/'data'))
    store.save('tilted',ds)
    result=predict(request(utc,dataset='tilted'),store.get('tilted'))
    data=sample_overlay(result,WeatherOverlayRequest(coverage='auto',pressure_hpa=500,grid_size=9),store)
    west,east=data['grid']['altitude_m'][4][0],data['grid']['altitude_m'][4][-1]
    assert west==pytest.approx(5050)
    expected=100+9900*math.log(80000/50000)/math.log(80000/20000)
    assert east==pytest.approx(expected) and abs(east-west)>1000


def test_pressure_missing_layers_are_not_bridged():
    raw={'height':np.array([100,1000,3000,6000]),'pressure':np.array([100000,90000,70000,40000]),'u':np.array([1,2,3,4]),'v':np.ones(4),'temperature':np.array([280,270,np.nan,240]),'terrain':0}
    assert pressure_column(raw,90000)['u']==2
    for target in [80000,60000,110000,30000]:
        with pytest.raises(CoverageError):pressure_column(raw,target)
    # Even missing height/pressure rows stay in their native adjacency.
    raw['height'][2]=2000
    raw['height']=raw['height'].astype(float);raw['height'][2]=np.nan
    raw['temperature'][2]=260
    with pytest.raises(CoverageError):pressure_column(raw,80000)


def test_pressure_surface_below_ground_or_above_top_is_masked(saved):
    store,result=saved
    for p in [1100,1]:
        with pytest.raises(CoverageError,match='No valid weather'):
            sample_overlay(result,WeatherOverlayRequest(pressure_hpa=p,grid_size=8),store)


@pytest.mark.parametrize('kind',['era5','wrf'])
def test_native_pressure_blocks_match_source_columns(tmp_path,utc,kind):
    if kind=='era5':
        make_file(tmp_path/'era5/2024/era5_2024d015.nc',date='2024-01-15',times=(12,18),lat=[55,53,51],lon=[354,356,358],humidity=True,surface_z=True,reverse_levels=True,reorder_dims=True)
        base=ERA5Weather(Archive(tmp_path/'era5',16),utc,ColumnCache(16),21600)
    else:
        make_wrf_file(tmp_path/'wrfout_d02_test',angle=37)
        base=WRFWeather(scan_wrf(tmp_path)[0][0],ColumnCache(16),21600)
    when=utc+timedelta(hours=2)
    cached=prepare_native(base,when,[51,52,53,54,55],[-6,-5,-4,-3,-2],lambda message:None)
    for p in [90000,50000,10000]:
        a,ha=sample_pressure(base,52,-5,p,when)
        b,hb=sample_pressure(cached,52,-5,p,when)
        assert [a.u,a.v,a.temperature,ha]==pytest.approx([b.u,b.v,b.temperature,hb],rel=2e-6,abs=2e-4)
    if kind=='wrf':
        assert a.u==pytest.approx(10*math.cos(math.radians(37))-2*math.sin(math.radians(37)))
        assert a.v==pytest.approx(10*math.sin(math.radians(37))+2*math.cos(math.radians(37)))


@pytest.mark.parametrize('kwargs',[{'altitude_m':100,'pressure_hpa':500},{'pressure_hpa':0},{'pressure_hpa':1101},{'pressure_hpa':float('nan')},{'altitude_m':100001},{'altitude_m':-501},{'coverage':'unbounded'}])
def test_vertical_requests_are_bounded(kwargs):
    with pytest.raises(ValidationError):WeatherOverlayRequest(**kwargs)


@pytest.mark.parametrize('source',['gfs','ifs','era5','era5t'])
def test_144_hour_window_is_allowed_after_confirmation(utc,source):
    values=dict(source=source,name='extended',start=utc,end=utc+timedelta(hours=144))
    with pytest.raises(ValidationError,match='confirmation'):DownloadRequest(**values)
    assert DownloadRequest(**values,confirm_large_download=True).end==utc+timedelta(hours=144)
    with pytest.raises(ValidationError,match='144 hours'):
        DownloadRequest(**{**values,'end':utc+timedelta(hours=145)},confirm_large_download=True)
    assert DownloadRequest(**{**values,'end':utc+timedelta(hours=72)}).confirm_large_download is False
    if source in ('gfs','ifs'):
        steps=forecast_steps(utc,utc,utc+timedelta(hours=144),source)
        assert steps[0]==0 and steps[-1]==144


def test_new_server_default_port():
    assert inspect.signature(prepare).parameters['port'].default==8009


def test_overlay_pressure_api_and_long_download_validation(tmp_path,utc,monkeypatch):
    from fastapi.testclient import TestClient
    from balloon_predictor.api import create_app
    from test_v161 import wait
    app=create_app(Settings(tmp_path/'data',era5_root=tmp_path/'era5',wrf_root=tmp_path/'wrf'))
    app.state.store.save('test',make_normalized())
    with TestClient(app) as client:
        prediction=client.post('/api/predictions',json=request(utc,dataset='test').model_dump(mode='json')).json()
        assert wait(client,prediction)['status']=='completed'
        job=client.post('/api/predictions/'+prediction['id']+'/weather-overlay',json={'coverage':'auto','pressure_hpa':500,'grid_size':8}).json()
        answer=wait(client,job)
        assert answer['status']=='completed'
        assert answer['result']['pressure_hpa']==500 and answer['result']['bounds']==[55,-6,51,-2]
        called=[]
        def fake(request,settings,progress):
            called.append(request)
            raise CoverageError('Synthetic download endpoint; no network used.')
        monkeypatch.setattr('balloon_predictor.api.download_weather',fake)
        body={'source':'ifs','name':'long','start':utc.isoformat(),'end':(utc+timedelta(hours=144)).isoformat()}
        assert client.post('/api/weather/download',json=body).status_code==422
        assert not called
        accepted=client.post('/api/weather/download',json={**body,'confirm_large_download':True})
        assert accepted.status_code==202
        state=wait(client,accepted.json())
        assert state['status']=='failed' and 'Synthetic download' in state['error']
        assert len(called)==1 and called[0].confirm_large_download
