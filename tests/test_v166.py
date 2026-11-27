import copy
from datetime import timedelta
import json
from pathlib import Path
import threading
import time

from netCDF4 import Dataset
import numpy as np
import pytest

from balloon_predictor.context import flight_weather
from balloon_predictor.errors import CoverageError, JobCancelled, PredictorError
from balloon_predictor.models import PredictionRequest, WeatherOverlayRequest
from balloon_predictor.predictor import predict
from balloon_predictor.settings import Settings
from balloon_predictor.store import WeatherStore
from balloon_predictor.weather.native import WRFWeather, ColumnCache, scan_wrf
from balloon_predictor.weather.wrf_reader import prediction_io, active_reader, MAX_OPEN_FILES
from balloon_predictor.weather_overlay import sample_overlay
from balloon_predictor.weather_pressure import sample_pressure
from conftest import make_wrf_file, make_normalized


def fixture_weather(tmp_path,angle=37,cache_mb=16):
    path=tmp_path/'wrfout_d02_test';make_wrf_file(path,angle=angle).close()
    return path,WRFWeather(scan_wrf(tmp_path)[0][0],ColumnCache(cache_mb),21600)


def test_prediction_blocks_match_columns_rotations_humidity_and_anchors(tmp_path,utc):
    path,weather=fixture_weather(tmp_path)
    with Dataset(path,'a') as nc:
        shape=('Time','bottom_top','south_north','west_east')
        nc.createVariable('QVAPOR','f4',shape)[:]=.007
        nc.createVariable('Q2','f4',('Time','south_north','west_east'))[:]=.009
        nc['Q2'][0,0,0]=np.nan
        nc['W'][0,:,1,1]=np.arange(4)*.1
    weather=WRFWeather(scan_wrf(tmp_path)[0][0],ColumnCache(16),21600)
    expected={(ti,y,x):weather._raw_column_scalar(ti,y,x) for ti in (0,1) for y in range(3) for x in range(3)}
    with prediction_io() as reader:
        for key,original in expected.items():
            actual=weather.raw_column(*key)
            assert set(actual)==set(original)
            for field in actual:
                if field=='surface':
                    assert actual[field]==original[field]
                else:np.testing.assert_array_equal(actual[field],original[field])
        assert reader.opens==1 and reader.block_reads==2
        assert reader.cache_hits>=16
        handles=[value[0] for value in reader.handles.values()]
    assert active_reader() is None and all(not h.isopen() for h in handles)
    assert weather.cache.bytes<=weather.cache.limit


@pytest.mark.parametrize('mode',['ascent','full'])
def test_prediction_and_sensitivity_identical_to_scalar(tmp_path,utc,monkeypatch,mode):
    path,weather=fixture_weather(tmp_path,angle=12)
    request=PredictionRequest(dataset=weather.info['id'],launch_datetime=utc,launch_latitude=53,launch_longitude=-4,
        flight_mode=mode,burst_altitude=3000,ensemble_members=3,seed=9,ground_altitude=0)
    original=WRFWeather(scan_wrf(tmp_path)[0][0],ColumnCache(16),21600)
    with monkeypatch.context() as patch:
        patch.setattr(WRFWeather,'raw_column',WRFWeather._raw_column_scalar)
        reference=predict(request,original)
    actual=predict(request,weather)
    assert actual==reference
    assert active_reader() is None


def test_prediction_reader_closes_on_failure_and_cancellation(tmp_path):
    path,weather=fixture_weather(tmp_path)
    for exception in (JobCancelled(),CoverageError('Outside coverage')):
        with pytest.raises(type(exception)):
            with prediction_io() as reader:
                weather.raw_column(0,1,1)
                handles=[h for h,_ in reader.handles.values()]
                raise exception
        assert all(not h.isopen() for h in handles)
        assert active_reader() is None
    with prediction_io() as reader:
        with prediction_io() as nested:
            assert reader is nested
            weather.raw_column(1,1,1)
        assert reader.handles
    assert not reader.handles


def test_prediction_reader_rejects_changed_grid_source_and_bad_rotation(tmp_path):
    path=tmp_path/'wrfout_d02_test';make_wrf_file(path,moving=True).close()
    weather=WRFWeather(scan_wrf(tmp_path)[0][0],ColumnCache(16),21600)
    with prediction_io():
        weather.raw_column(0,1,1)
        with pytest.raises(PredictorError,match='grid or terrain changes'):weather.raw_column(1,1,1)
    with prediction_io():
        weather.raw_column(0,1,1);path.touch()
        with pytest.raises(PredictorError,match='changed'):weather.raw_column(0,1,1)
    # An invalid rotation in an unused neighbouring column must not alter a
    # valid flight; requesting that invalid column still fails explicitly.
    with Dataset(path,'a') as nc:nc['COSALPHA'][0,0,0]=.7
    weather=WRFWeather(scan_wrf(tmp_path)[0][0],ColumnCache(16),21600)
    with prediction_io():
        assert np.isfinite(weather.raw_column(0,1,1)['u']).all()
        with pytest.raises(PredictorError,match='rotation coefficients'):weather.raw_column(0,0,0)


def test_frame_lru_context_isolation_and_small_memory_budget(tmp_path):
    frames=MAX_OPEN_FILES+2
    path=tmp_path/'wrfout_d02_test';make_wrf_file(path,times=[f'2024-01-15_{12+i}:00:00' for i in range(frames)]).close()
    weather=WRFWeather(scan_wrf(tmp_path)[0][0],ColumnCache(.015),21600)
    with prediction_io() as reader:
        for ti in range(frames):weather.raw_column(ti,1,1)
        assert reader.opens==1 and len(reader.handles)==1
        assert weather.cache.bytes<=weather.cache.limit
    seen=[]
    def task():
        with prediction_io() as local:seen.append(local)
    a,b=threading.Thread(target=task),threading.Thread(target=task);a.start();b.start();a.join();b.join()
    assert len(seen)==2 and seen[0] is not seen[1]


def test_multiple_files_lru_evicts_and_closes(tmp_path):
    for i in range(MAX_OPEN_FILES+2):
        path=tmp_path/f'wrfout_d02_2024-01-15_{12+i}:00:00'
        make_wrf_file(path,times=[f'2024-01-15_{12+i}:00:00']).close()
    weather=WRFWeather(scan_wrf(tmp_path)[0][0],ColumnCache(16),21600)
    with prediction_io() as reader:
        opened=[]
        for ti in range(len(weather.times)):
            weather.raw_column(ti,1,1);opened.append(next(reversed(reader.handles.values()))[0])
            assert len(reader.handles)<=MAX_OPEN_FILES
        assert reader.opens==MAX_OPEN_FILES+2
        assert not opened[0].isopen()
    assert all(not h.isopen() for h in opened)


@pytest.mark.parametrize('pressure',[None,700.])
def test_wrf_vertical_overlay_matches_original_interpolation(tmp_path,utc,pressure):
    root=tmp_path/'wrf';root.mkdir();path,_=fixture_weather(root,angle=18)
    with Dataset(path,'a') as nc:
        nc['W'][0,:,1,1]=[-.7,.1,.9,1.4]
    store=WeatherStore(Settings(tmp_path/'data',wrf_root=root));dataset=next(d['id'] for d in store.list() if d['source']=='wrf')
    weather=store.get(dataset);req=PredictionRequest(dataset=dataset,launch_datetime=utc,launch_latitude=53,launch_longitude=-4,flight_mode='ascent',burst_altitude=3000)
    result=predict(req,weather)
    options=WeatherOverlayRequest(grid_size=9,area=(54,-5,52,-3),include_vertical_velocity=True,altitude_m=3000 if pressure is None else None,pressure_hpa=pressure)
    overlay=sample_overlay(result,options,store)
    for y,x in [(0,0),(4,4),(8,8)]:
        lat,lon=overlay['grid']['latitude'][y],overlay['grid']['longitude'][x]
        if pressure is None:expected=flight_weather(weather,req).sample(lat,lon,3000,utc).w
        else:expected=sample_pressure(weather,lat,lon,pressure*100,utc,include_w=True)[0].w
        assert overlay['grid']['w'][y][x]==pytest.approx(expected,rel=2e-6,abs=2e-6)
    assert overlay['vertical_velocity_available'] and overlay['vertical_velocity_valid_cells']>0
    assert overlay['vertical_velocity_units']=='m/s; positive upwards'
    old=sample_overlay(result,options.model_copy(update={'include_vertical_velocity':False}),store)
    assert 'w' not in old['grid']
    for field in ('u','v','temperature_c','wind_speed'):
        np.testing.assert_allclose(old['grid'][field],overlay['grid'][field],rtol=2e-6,atol=2e-5)


def test_vertical_overlay_normalized_omega_missing_and_w_masks(tmp_path,utc):
    ds=make_normalized();ds['w']=(ds.u.dims,np.full(ds.u.shape,.4));ds['w'].attrs['units']='m s-1'
    store=WeatherStore(Settings(tmp_path/'data'));store.save('with-w',ds)
    req=PredictionRequest(dataset='with-w',launch_datetime=utc,flight_mode='ascent',burst_altitude=2000)
    flight=predict(req,store.get('with-w'))
    answer=sample_overlay(flight,WeatherOverlayRequest(grid_size=8,pressure_hpa=700,include_vertical_velocity=True),store)
    assert np.nanmean(np.asarray(answer['grid']['w'],float))==pytest.approx(.4)
    store.save('without-w',ds.drop_vars('w'))
    req=req.model_copy(update={'dataset':'without-w'});flight=predict(req,store.get('without-w'))
    with pytest.raises(PredictorError,match='unavailable'):sample_overlay(flight,WeatherOverlayRequest(include_vertical_velocity=True),store)
    ordinary=sample_overlay(flight,WeatherOverlayRequest(grid_size=8),store)
    assert 'w' not in ordinary['grid'] and not ordinary['vertical_velocity_available']


def test_missing_w_masks_only_vertical_overlay_and_leaves_core_map(tmp_path,utc):
    root=tmp_path/'wrf';root.mkdir();path,_=fixture_weather(root)
    with Dataset(path,'a') as nc:nc['W'][0,:,1,1]=np.nan
    store=WeatherStore(Settings(tmp_path/'data',wrf_root=root));name=next(i['id'] for i in store.list() if i['source']=='wrf')
    # Build a valid saved result on the unaffected corner of the domain.
    req=PredictionRequest(dataset=name,launch_datetime=utc,launch_latitude=51.02,launch_longitude=-5.98,flight_mode='ascent',burst_altitude=2000)
    # The original sampler requires finite W in every used column, even with
    # vertical motion disabled. Use a receipt-verified nominal result from an
    # unaffected source, then sample the requested map containing the hole.
    from balloon_predictor.predictor import predict as predict_flight
    with Dataset(path,'a') as nc:nc['W'][0,:,1,1]=.25
    store.native.scan();weather=store.get(name);result=predict_flight(req,weather)
    with Dataset(path,'a') as nc:nc['W'][0,:,1,1]=np.nan
    store.native.scan();weather=store.get(name)
    from balloon_predictor.provenance import snapshot
    result['input_manifest']=snapshot(weather)
    response=sample_overlay(result,WeatherOverlayRequest(grid_size=9,area=(55,-6,51,-2),altitude_m=2000,include_vertical_velocity=True),store)
    assert response['valid_cells']==81
    assert 0<response['vertical_velocity_valid_cells']<81
    assert response['grid']['w'][4][4] is None
    assert response['grid']['u'][4][4] is not None


def test_classic_netcdf_wrf_prediction_scope(tmp_path):
    path=tmp_path/'wrfout_d02_classic';ds=make_wrf_file(path)
    ds.to_netcdf(path,engine='scipy',format='NETCDF3_64BIT');ds.close()
    weather=WRFWeather(scan_wrf(tmp_path)[0][0],ColumnCache(16),21600)
    reference=weather._raw_column_scalar(0,1,1)
    with prediction_io():
        actual=weather.raw_column(0,1,1)
    for field in ('u','v','height','pressure','temperature','w'):np.testing.assert_array_equal(actual[field],reference[field])


def test_large_wrf_prediction_tiles_keep_native_values_at_boundaries(tmp_path):
    import importlib.util
    spec=importlib.util.spec_from_file_location('large_fixture',Path(__file__).resolve().parents[1]/'scripts/wrf-overlay-fixture.py');module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    module.create(tmp_path/'wrfout_d02_test',ny=72,nx=80,nz=60)
    weather=WRFWeather(scan_wrf(tmp_path)[0][0],ColumnCache(16),21600)
    points=[(ti,y,x) for ti in (0,1) for y,x in [(0,0),(1,1),(31,31),(31,32),(32,31),(32,32),(71,79)]]
    expected={point:weather._raw_column_scalar(*point) for point in points}
    with prediction_io() as reader:
        for point in points:
            result=weather.raw_column(*point)
            for name in ('u','v','temperature','height','pressure','w'):
                np.testing.assert_allclose(result[name],expected[point][name],rtol=1e-14,atol=1e-12)
        assert reader.opens==1 and reader.block_reads<len(points)
        assert weather.cache.bytes<=weather.cache.limit


def test_mixed_model_manual_plan_exports_keep_sources(tmp_path,utc):
    import csv
    from io import StringIO
    from xml.etree.ElementTree import fromstring
    from balloon_predictor.exports import csv_export,geojson,kml_export
    store=WeatherStore(Settings(tmp_path/'data'));flights=[]
    for model in ('ifs','gfs'):
        ds=make_normalized();ds.attrs.update(source=model,provenance_json=json.dumps({'cycle':'2024-01-15T00:00:00Z'}));store.save(model,ds)
        req=PredictionRequest(dataset=model,launch_datetime=utc,flight_mode='ascent',burst_altitude=2000)
        value=predict(req,store.get(model));flights.append({'index':len(flights),'site_index':0,'flight_name':'Same launch point','launch_datetime':req.launch_datetime.isoformat(),'status':'completed','result':value})
    plan={'type':'series','schedule_type':'manual','flights':flights,'completed':2,'failed':0,'total':2}
    exported=geojson(plan)
    assert exported['metadata']['request']['mode']=='manual'
    assert {f['properties']['dataset'] for f in exported['features']}=={'ifs','gfs'}
    rows=list(csv.DictReader(StringIO(csv_export(plan))))
    assert {r['weather_source'] for r in rows}=={'ifs','gfs'}
    assert {r['forecast_initialization_utc'] for r in rows}=={'2024-01-15T00:00:00Z'}
    assert all(r['dataset']==r['weather_source'] for r in rows)
    kml=fromstring(kml_export(plan));descriptions=kml.findall('.//{http://www.opengis.net/kml/2.2}Folder/{http://www.opengis.net/kml/2.2}description')
    assert {json.loads(d.text)['weather']['source'] for d in descriptions}=={'ifs','gfs'}


def test_prediction_tile_cache_identifies_symlink_target(tmp_path):
    import os
    import shutil
    archive=tmp_path/'archive';archive.mkdir();backing=archive/'backing';backing.mkdir()
    first=backing/'first.nc';second=backing/'second.nc';make_wrf_file(first,angle=0).close();shutil.copyfile(first,second)
    with Dataset(second,'a') as nc:nc['U'][:]=13
    stamp=first.stat();os.utime(second,ns=(stamp.st_atime_ns,stamp.st_mtime_ns))
    assert second.stat().st_size==stamp.st_size
    link=archive/'wrfout_d02_test';link.symlink_to(first)
    weather=WRFWeather(scan_wrf(archive)[0][0],ColumnCache(16),21600)
    with prediction_io():assert weather.raw_column(0,1,1)['u'][0]==10
    link.unlink();link.symlink_to(second)
    # Even identically sized/timestamped targets cannot reuse the old tile or
    # its fixed-grid validation. Both resolved identity and stamp are keyed.
    with prediction_io():assert weather.raw_column(0,1,1)['u'][0]==13
