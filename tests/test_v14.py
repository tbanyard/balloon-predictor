"""v1.4 keeps sonde preparation/download state consistent with model boxes."""
from datetime import datetime, timezone
import time
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient
from netCDF4 import Dataset

from conftest import make_normalized
from balloon_predictor.api import create_app
from balloon_predictor.models import PredictionRequest
from balloon_predictor.settings import Settings


def wait(client, job_id):
    for _ in range(300):
        record=client.get('/api/jobs/'+job_id).json()
        if record['status'] in ('completed','failed'):return record
        time.sleep(.01)
    pytest.fail('Background job did not finish')


def body(**changes):
    return {'dataset':'demo','launch_datetime':'2024-01-15T12:00:00Z','flight_mode':'ascent',
            'launch_latitude':53,'launch_longitude':-4,'launch_altitude':100,'burst_altitude':1000,**changes}


@pytest.fixture
def client(tmp_path):
    settings=Settings(tmp_path/'data',era5_root=tmp_path/'era5',wrf_root=tmp_path/'wrf')
    with TestClient(create_app(settings)) as client:yield client


def test_unused_landing_altitude_does_not_block_ascent():
    req=PredictionRequest.model_validate(body(landing_mode='fixed',landing_altitude=3000))
    assert req.flight_mode=='ascent'
    with pytest.raises(ValueError,match='Landing altitude must be below burst altitude'):
        PredictionRequest.model_validate(body(flight_mode='full',landing_mode='fixed',landing_altitude=3000))


def test_sonde_background_job_and_reusable_download_preserve_schema(client,tmp_path):
    prediction=client.post('/api/predictions',json=body(landing_mode='fixed',landing_altitude=3000)).json()['id']
    assert wait(client,prediction)['status']=='completed'
    exported=client.post(f'/api/predictions/{prediction}/sonde-export',json={'scope':'ascent','flight_index':0})
    assert exported.status_code==202
    job=exported.json()['id'];record=wait(client,job)
    assert record['status']=='completed',record
    assert record['kind']=='export' and record['result']['type']=='sonde'
    path=Path(record['result']['path']);assert path.is_file()
    first=client.get(f'/api/exports/{job}/download');second=client.get(f'/api/exports/{job}/download')
    assert first.status_code==second.status_code==200
    assert first.content==second.content and first.content[:3]==b'\x89HD'
    assert '.nc' in first.headers['content-disposition']
    # Old direct sonde URLs remain compatible with scripts and pop-out profiles.
    legacy=client.get(f'/api/predictions/{prediction}/sonde.nc?scope=ascent')
    old=tmp_path/'legacy.nc';old.write_bytes(legacy.content)
    with Dataset(path) as a,Dataset(old) as b:
        assert list(a.variables)==list(b.variables) and len(a.variables)==22
        for name in a.variables:
            assert a[name].dtype==b[name].dtype
            assert np.allclose(np.ma.filled(a[name][:],np.nan),np.ma.filled(b[name][:],np.nan),equal_nan=True)
    # A stored export receipt remains usable after the in-memory record is evicted.
    del client.app.state.jobs.records[job]
    assert client.get(f'/api/jobs/{job}').json()['kind']=='export'
    assert client.get(f'/api/exports/{job}/download').status_code==200


def test_sonde_export_uses_selected_series_flight(client):
    prediction=client.post('/api/predictions/series',json={'flights':[body()], 'duration_hours':2,'interval_hours':1}).json()['id']
    assert wait(client,prediction)['result']['completed']==2
    job=client.post(f'/api/predictions/{prediction}/sonde-export',json={'flight_index':1,'scope':'full'}).json()['id']
    record=wait(client,job);assert record['status']=='completed'
    assert record['result']['flight_index']==1 and record['result']['scope']=='full'
    with Dataset(record['result']['path']) as nc:
        assert nc.getncattr('trajectory_scope')=='full'
        assert nc.getncattr('launch_time')=='2024-01-15T13:00:00Z'
    assert client.post(f'/api/predictions/{prediction}/sonde-export',json={'flight_index':9}).status_code==404


def test_changed_source_fails_sonde_job_without_leftover_file(client):
    store=client.app.state.store;store.save('test-data',make_normalized())
    prediction=client.post('/api/predictions',json=body(dataset='test-data')).json()['id']
    assert wait(client,prediction)['status']=='completed'
    store.save('test-data',make_normalized(),overwrite=True)
    job=client.post(f'/api/predictions/{prediction}/sonde-export',json={}).json()['id']
    record=wait(client,job)
    assert record['status']=='failed' and 'changed or was removed' in record['error']
    assert not list((store.settings.data_dir/'exports').glob('*.sonde.nc'))


@pytest.mark.parametrize('options',[{'scope':'bad'},{'flight_index':-1},{'selection':'plan'}])
def test_sonde_export_rejects_invalid_options(client,options):
    assert client.post('/api/predictions/'+'a'*32+'/sonde-export',json=options).status_code==422
