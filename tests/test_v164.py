import copy
from datetime import datetime,timezone
import json
from pathlib import Path
import time
import numpy as np
import pytest
from fastapi.testclient import TestClient
from netCDF4 import Dataset
from balloon_predictor.api import create_app
from balloon_predictor.disk import personal_data_dir
from balloon_predictor.errors import PredictorError
from balloon_predictor.models import PredictionRequest
from balloon_predictor.settings import Settings
from balloon_predictor.store import WeatherStore
from balloon_predictor.weather.native import scan_wrf
from balloon_predictor.weather.wrf_rotation import rotation
from balloon_predictor.modelbox import wrf_block
from conftest import make_wrf_file,make_normalized


def done(client,ident):
    for _ in range(200):
        value=client.get('/api/jobs/'+ident).json()
        if value['status'] in ('completed','failed'):return value
        time.sleep(.025)
    pytest.fail('Prediction did not finish')


def test_skysight_cycle_total_fields_rotation_and_block_match(tmp_path):
    folder=tmp_path/'common'/'wrf';folder.mkdir(parents=True)
    for hour in (12,18):
        destination=folder if hour==12 else folder/'later-frame'
        destination.mkdir(exist_ok=True)
        path=destination/f'wrfout_d02_2024-01-15_{hour}:00:00'
        ds=make_wrf_file(path,times=[f'2024-01-15_{hour}:00:00']).drop_vars(['COSALPHA','SINALPHA'])
        # Converted archive: per-frame START_DATE and zero base states.
        ds['P'][:]=ds['P']+ds['PB'];ds['PB'][:]=0
        ds['PH'][:]=ds['PH']+ds['PHB'];ds['PHB'][:]=0
        ds.attrs.update(MAP_PROJ=1,TRUELAT1=55.434,TRUELAT2=55.434,STAND_LON=-4.607,parent_cycle_utc='2024-01-15T00:00:00Z',SIMULATION_START_DATE=f'2024-01-15_{hour}:00:00')
        ds.to_netcdf(path);ds.close()
    info,messages=scan_wrf(folder,shared=True)
    assert not messages and len(info)==1 and len(info[0]['records'])==2
    store=WeatherStore(Settings(tmp_path/'alice',shared_data_root=tmp_path/'common'))
    name=next(x['id'] for x in store.list() if x.get('shared'))
    weather=store.get(name)
    col=weather.raw_column(0,1,2)
    angle=-np.radians((-2+4.607)*np.sin(np.radians(55.434)))
    assert col['u'][0]==pytest.approx(10*np.cos(angle)-2*np.sin(angle))
    assert col['v'][0]==pytest.approx(2*np.cos(angle)+10*np.sin(angle))
    assert col['temperature'][0]==pytest.approx(300*(95000/100000)**(287/1004))
    fields,surface=wrf_block(weather,0,np.array([1]),np.array([2]))
    for key in ('u','v','height','temperature','pressure'):
        np.testing.assert_allclose(fields[key][:,0,0],col[key])
    assert surface['surface_u'][0,0]==pytest.approx(col['surface']['u'])
    earth=WeatherStore(Settings(tmp_path/'bob',shared_data_root=tmp_path/'common',wrf_wind_reference='earth')).get(name)
    assert earth.raw_column(0,1,2)['u'][0]==10
    assert earth.raw_column(0,1,2)['v'][0]==2


def test_lambert_rotation_matches_ncar_formula_in_both_hemispheres():
    # Independent NCAR uvmet convention: u_e=u*cos(beta)+v*sin(beta).
    for latitude,longitude in [(55,10),(-55,10),(55,179.5)]:
        attrs={'MAP_PROJ':1,'TRUELAT1':30 if latitude>0 else -30,'TRUELAT2':60 if latitude>0 else -60,'STAND_LON':0}
        cone=(np.log(np.cos(np.deg2rad(30)))-np.log(np.cos(np.deg2rad(60))))/(np.log(np.tan(np.deg2rad(30)))-np.log(np.tan(np.deg2rad(15))))
        beta=np.deg2rad(longitude)*cone*(1 if latitude>0 else -1)
        ca,sa=rotation(attrs,latitude,longitude)
        assert 8*ca-3*sa==pytest.approx(8*np.cos(beta)+3*np.sin(beta))
        assert 3*ca+8*sa==pytest.approx(3*np.cos(beta)-8*np.sin(beta))


def test_shared_forecast_is_read_only_and_personal_data_stays_separate(tmp_path):
    root=tmp_path/'common';(root/'ifs').mkdir(parents=True)
    path=root/'ifs'/'forecast.nc';ds=make_normalized();ds.attrs['source']='ifs';ds.to_netcdf(path);ds.close()
    before=path.read_bytes()
    a=WeatherStore(Settings(tmp_path/'alice',shared_data_root=root));b=WeatherStore(Settings(tmp_path/'bob',shared_data_root=root))
    info=next(i for i in a.list() if i.get('shared'))
    assert info['id'] in [i['id'] for i in b.list()] and info['read_only']
    weather=a.get(info['id']);col=weather.raw_column(0,0,0)
    assert col['temperature'].shape[0]>=2 and path.read_bytes()==before
    from balloon_predictor.predictor import predict
    from balloon_predictor.weather_overlay import sample_overlay
    from balloon_predictor.models import WeatherOverlayRequest
    request=PredictionRequest(dataset=info['id'],launch_datetime='2024-01-15T12:00Z',flight_mode='ascent',burst_altitude=1000)
    flight=predict(request,weather)
    overlay=sample_overlay(flight,WeatherOverlayRequest(grid_size=8,time_basis='launch',altitude_m=1000),a)
    assert any(value is not None for row in overlay['grid']['u'] for value in row)
    assert path.read_bytes()==before
    ds=make_normalized();a.save('my-private-copy',ds);ds.close()
    assert 'my-private-copy' not in [i['id'] for i in b.list()]
    with pytest.raises(PredictorError):personal_data_dir(root,'wrf')
    with pytest.raises(PredictorError):personal_data_dir(root,'ifs')


def test_saved_flight_registration_needs_no_weather_or_calculation(tmp_path,monkeypatch):
    app=create_app(Settings(tmp_path/'data'))
    req=PredictionRequest(launch_datetime='2024-01-15T12:00Z',flight_mode='ascent',burst_altitude=1000)
    with TestClient(app) as client:
        value=done(client,client.post('/api/predictions',json=req.model_dump(mode='json')).json()['id'])['result']
        from balloon_predictor import api
        monkeypatch.setattr(api,'predict',lambda *a:pytest.fail('Restoring recalculated the flight'))
        monkeypatch.setattr(app.state.store,'get',lambda *a:pytest.fail('Restoring loaded weather'))
        reply=client.post('/api/flights/restore',json={'result':value})
        assert reply.status_code==201,reply.text
        restored=client.get('/api/jobs/'+reply.json()['id']).json()['result']
        assert restored['prediction']==value['prediction'] and restored['sounding']==value['sounding']
        bad=copy.deepcopy(value);bad['prediction'][0]['trajectory'][0]['latitude']=900
        assert client.post('/api/flights/restore',json={'result':bad}).status_code==422


def test_prediction_retry_is_idempotent_and_rejects_changed_body(tmp_path):
    with TestClient(create_app(Settings(tmp_path/'data'))) as client:
        request=PredictionRequest(launch_datetime='2024-01-15T12:00Z',flight_mode='ascent',burst_altitude=1000).model_dump(mode='json')
        headers={'X-BP-Request-ID':'a-unique-user-request'}
        first=client.post('/api/predictions',json=request,headers=headers).json()
        second=client.post('/api/predictions',json=request,headers=headers).json()
        assert first['id']==second['id']
        assert done(client,first['id'])['status']=='completed'
        request['ascent_rate']=6
        assert client.post('/api/predictions',json=request,headers=headers).status_code==409
