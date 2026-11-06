from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import time
import zipfile

import numpy as np
import pytest
from fastapi.testclient import TestClient
from netCDF4 import Dataset

from conftest import make_normalized, make_wrf_file
from balloon_predictor.api import create_app
from balloon_predictor.context import flight_weather
from balloon_predictor.errors import PredictorError
from balloon_predictor.models import PredictionRequest, ModelBoxRequest, CleanupRequest
from balloon_predictor.modelbox import export_model_boxes, bounds, era_block, wrf_block
from balloon_predictor.predictor import predict
from balloon_predictor.provenance import validate_manifest
from balloon_predictor.settings import Settings
from balloon_predictor.storage import activity, cleanup
from balloon_predictor.store import WeatherStore
from balloon_predictor.weather.interpolate import DatasetWeather
from balloon_predictor.weather.native import ERA5Weather, WRFWeather, ColumnCache, scan_wrf
from balloon_predictor.weather.era5_native.data import Archive
from balloon_predictor.weather.schema import describe_dataset
from scripts.make_test_data import make_file


@pytest.fixture
def store(tmp_path):
    return WeatherStore(Settings(tmp_path / "data", era5_root=tmp_path/"era5", wrf_root=tmp_path/"wrf"))


def request(utc, **kwargs):
    return PredictionRequest(launch_datetime=utc, launch_latitude=53, launch_longitude=-4,
                             launch_altitude=1, burst_altitude=1000, flight_mode="ascent", landing_mode="fixed", **kwargs)


def coastal_weather():
    ds=make_normalized()
    ds["terrain"][:]=500
    ds["height"][:]=ds.height.values+500
    return DatasetWeather(ds,describe_dataset(ds,"coast"))


def test_coastal_launch_preserves_height_and_model_ground(utc):
    weather=coastal_weather()
    result=predict(request(utc, ground_altitude=0),weather)
    assert result["summary"]["launch"]["altitude"]==1
    assert weather.terrain(53,-4)==500
    assert result["weather"]["below_model"]["maximum_depth_m"]==pytest.approx(509)
    assert any("approximation used" in w for w in result["warnings"])
    assert "below_model" not in weather.info
    ctx=flight_weather(weather,request(utc))
    sample=ctx.sample(53,-4,1,utc)
    anchor=weather.sample(53,-4,510,utc)
    assert sample.u==anchor.u
    assert sample.temperature>anchor.temperature
    assert sample.pressure>anchor.pressure


def test_strict_and_physical_ground_reject(utc):
    with pytest.raises(PredictorError,match="Strict ground policy"):
        predict(request(utc,below_model_policy="strict"),coastal_weather())
    with pytest.raises(PredictorError,match="below ground"):
        predict(request(utc,ground_altitude=5),coastal_weather())
    with pytest.raises(PredictorError,match="below ground"):
        predict(request(utc,ground_source="model"),coastal_weather())


def test_bridge_does_not_hide_missing_layers(utc):
    weather=coastal_weather()
    weather.fields["temperature"][:]=np.nan
    with pytest.raises(PredictorError):
        predict(request(utc),weather)


def test_geotiff_physical_launch_and_dem_landing(store,utc):
    import rasterio
    from rasterio.transform import from_origin
    with rasterio.open(store.terrain.root/"local.tif","w",driver="GTiff",width=30,height=30,count=1,dtype="float32",crs="EPSG:4326",transform=from_origin(-7,56,.2,.2),nodata=-9999) as ds:
        ds.write(np.full((30,30),2,dtype="float32"),1)
    base=store.get("demo")
    req=request(utc,ground_source="dem").model_copy(update={"launch_altitude":3,"flight_mode":"full","landing_mode":"dem"})
    result=predict(req,base)
    assert result["summary"]["landing"]["altitude"]==2
    assert result["weather"]["physical_ground"]["tiles"]==["local.tif"]
    assert any(e["path"].endswith("local.tif") for e in result["input_manifest"])
    with pytest.raises(PredictorError,match="No valid physical DEM"):
        store.terrain.sample(20,30)


def test_model_box_all_levels_native_brackets_and_values(store,utc):
    source=make_normalized();store.save("test",source)
    result=predict(request(utc).model_copy(update={"dataset":"test","launch_datetime":utc+timedelta(minutes=30)}),store.get("test"))
    exported=export_model_boxes(result,ModelBoxRequest(),store,lambda _:None)
    with Dataset(exported["path"]) as nc:
        assert len(nc.dimensions["level"])==3
        assert len(nc.dimensions["time"])==2
        assert np.array_equal(nc["u"][:],source.u.values)
        assert nc["time"][-1]-nc["time"][0]==21600
        assert nc["terrain"][0,0]==0
    # Replaced data can never be silently re-sampled for an old prediction.
    store.save("test",source,overwrite=True)
    with pytest.raises(PredictorError,match="changed or was removed"):
        export_model_boxes(result,ModelBoxRequest(),store,lambda _:None)


def test_model_box_clipping_limits_and_mixed_dataset_zip(store,utc):
    store.save("first",make_normalized());store.save("second",make_normalized())
    flights=[]
    for i,name in enumerate(("first","second")):
        req=request(utc).model_copy(update={"dataset":name,"launch_latitude":51.01,"launch_longitude":-5.99})
        flights.append({"index":i,"status":"completed","result":predict(req,store.get(name))})
    result={"type":"series","flights":flights}
    exported=export_model_boxes(result,ModelBoxRequest(selection="plan"),store,lambda _:None)
    assert exported["filename"]=="model-boxes.zip"
    with zipfile.ZipFile(exported["path"]) as z:
        assert len([p for p in z.namelist() if p.endswith(".nc")])==2
    assert exported["warnings"]
    store.settings.max_export_mb=0
    before=set((store.settings.data_dir/"exports").iterdir())
    with pytest.raises(PredictorError,match="estimate"):
        export_model_boxes(flights[0]["result"],ModelBoxRequest(),store,lambda _:None)
    assert set((store.settings.data_dir/"exports").iterdir())==before


def test_model_box_same_dataset_plan_merges(store,utc):
    store.save("first",make_normalized())
    flights=[{"index":i,"status":"completed","result":predict(request(utc).model_copy(update={"dataset":"first","launch_datetime":utc+timedelta(minutes=i*30)}),store.get("first"))} for i in range(2)]
    exported=export_model_boxes({"type":"series","flights":flights},ModelBoxRequest(selection="plan"),store,lambda _:None)
    assert len(exported["boxes"])==1


def test_geodesic_longitude_margin_and_dateline():
    points=[{"latitude":80,"longitude":179.9},{"latitude":80.1,"longitude":-179.9}]
    box=bounds(points)
    assert box["east_unwrapped"]-box["west_unwrapped"]<10
    assert box["east_unwrapped"]-180.1>2.5
    assert box["south"]==pytest.approx(79.5)


@pytest.mark.parametrize("reorder",[False,True])
def test_native_era_export_matches_column_reader(tmp_path,utc,reorder):
    path=tmp_path/"era5/2024/era5_2024d015.nc"
    make_file(path,date="2024-01-15",times=(12,18),lat=[55,53,51],lon=[354,356,358],humidity=True,surface_z=True,reverse_levels=True,reorder_dims=reorder)
    weather=ERA5Weather(Archive(tmp_path/"era5",16),utc,ColumnCache(16),21600)
    fields,surface=era_block(weather,0,np.array([0,1]),np.array([0,1]))
    column=weather.raw_column(0,0,1)
    for key in fields:
        assert np.allclose(fields[key][:,0,1],column[key],equal_nan=True)
    assert surface["terrain"][0,1]==pytest.approx(column["terrain"])


def test_native_wrf_export_matches_column_reader(tmp_path,utc):
    path=tmp_path/"wrfout_d02_test";make_wrf_file(path)
    info=scan_wrf(tmp_path)[0][0]
    weather=WRFWeather(info,ColumnCache(16),21600)
    fields,surface=wrf_block(weather,0,np.array([0,1]),np.array([1,2]))
    column=weather.raw_column(0,1,2)
    for key in fields:
        assert np.allclose(fields[key][:,1,1],column[key],equal_nan=True)
    assert surface["surface_u"][1,1]==pytest.approx(column["surface"]["u"])


def test_cleanup_dependencies_cache_and_activity(store,utc):
    store.save("test",make_normalized())
    result=predict(request(utc).model_copy(update={"dataset":"test"}),store.get("test"))
    result_path=store.settings.data_dir/"results"/("a"*32+".json");result_path.write_text(json.dumps(result))
    with activity(store.settings),pytest.raises(PredictorError,match="Storage is in use"):
        cleanup(store.settings,store,CleanupRequest(target="dataset",name="test",allow_orphan=True))
    with pytest.raises(PredictorError,match="depend"):
        cleanup(store.settings,store,CleanupRequest(target="dataset",name="test"))
    cache=store.settings.data_dir/"cache"; (cache/"abc.grib").write_bytes(b"data");(cache/"abc.request.json").write_text("{}");(cache/"abc.grib.lock").touch()
    assert cleanup(store.settings,store,CleanupRequest(target="cache"))["removed_files"]==2
    assert result_path.exists() and store.exists("test") and (cache/"abc.grib.lock").exists()
    cleanup(store.settings,store,CleanupRequest(target="dataset",name="test",allow_orphan=True))
    assert result_path.exists() and not store.exists("test")


def test_model_box_background_api_and_storage_paths(store,utc):
    store.save("test",make_normalized())
    app=create_app(store.settings)
    with TestClient(app) as client:
        data=client.get("/api/storage").json()
        assert data["data_dir"]==str(store.settings.data_dir)
        body=request(utc).model_copy(update={"dataset":"test"}).model_dump(mode="json")
        job=client.post("/api/predictions",json=body).json()["id"]
        def complete(ident):
            for _ in range(200):
                record=client.get("/api/jobs/"+ident).json()
                if record["status"] in ("completed","failed"):return record
                time.sleep(.01)
            pytest.fail("Job did not finish")
        assert complete(job)["status"]=="completed"
        export=client.post(f"/api/predictions/{job}/model-box",json={}).json()["id"]
        record=complete(export)
        assert record["status"]=="completed",record
        response=client.get(f"/api/exports/{export}/download")
        assert response.status_code==200 and response.content[:3]==b"\x89HD"
        assert client.post("/api/storage/cleanup",json={"target":"dataset","name":"test"}).status_code==422
        assert client.post("/api/storage/cleanup",json={"target":"results"}).status_code==200
        assert client.get("/api/jobs/"+job).status_code==404
        assert client.get("/api/jobs/"+export).status_code==404


@pytest.mark.parametrize("kind",["era5","wrf"])
def test_native_full_model_box_export(store,utc,kind):
    if kind=="era5":
        path=store.settings.era5_root/"2024/era5_2024d015.nc"
        make_file(path,date="2024-01-15",times=(12,18),lat=[55,53,51],lon=[354,356,358],humidity=True,surface_z=True)
        ident="era5"
    else:
        store.settings.wrf_root.mkdir()
        make_wrf_file(store.settings.wrf_root/"wrfout_d02_test")
    store.native.scan()
    if kind=="wrf":ident=store.native.wrf[0]["id"]
    req=request(utc).model_copy(update={"dataset":ident,"burst_altitude":2000})
    flight=predict(req,store.get(ident,utc))
    exported=export_model_boxes(flight,ModelBoxRequest(),store,lambda _:None)
    with Dataset(exported["path"]) as nc:
        assert len(nc.dimensions["level"])==(137 if kind=="era5" else 3)
        assert len(nc.dimensions["time"])==2
        assert "surface_pressure" in nc.variables
        assert ("hybrid_a" in nc.variables)==(kind=="era5")
        assert np.isfinite(nc["u"][:]).all()
        if kind=="era5":assert json.loads(nc.getncattr("provenance_json"))["files"]


def test_sonde_bridge_exports_same_science_and_metadata(store,utc,tmp_path):
    from balloon_predictor.sonde import write_sonde
    source=make_normalized();source["terrain"][:]=500;source["height"][:]=source.height.values+500
    store.save("coast",source)
    req=request(utc,ground_altitude=0).model_copy(update={"dataset":"coast"})
    flight=predict(req,store.get("coast"))
    path=tmp_path/"sonde.nc";write_sonde(path,flight,store.get("coast"))
    with Dataset(path) as nc:
        sample=flight_weather(store.get("coast"),req).sample(53,-4,1,utc)
        assert nc["temp"][0]==pytest.approx(sample.temperature)
        assert nc["pressure"][0]==pytest.approx(sample.pressure/100)
        assert float(nc.getncattr("heightOfStationGroundAboveMeanSeaLevel"))==0
        assert json.loads(nc.getncattr("below_model_approximation_json"))["maximum_depth_m"]>500


def test_cleanup_rejects_other_process_lease(store):
    import os, subprocess, sys
    code="""
import sys
from pathlib import Path
from balloon_predictor.settings import Settings
from balloon_predictor.storage import activity
settings=Settings(Path(sys.argv[1]))
with activity(settings):
 print('held',flush=True)
 sys.stdin.readline()
"""
    proc=subprocess.Popen([sys.executable,"-c",code,str(store.settings.data_dir)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True,env=os.environ.copy())
    try:
        assert proc.stdout.readline().strip()=="held"
        with pytest.raises(PredictorError,match="Storage is in use"):
            cleanup(store.settings,store,CleanupRequest(target="cache"))
    finally:
        proc.communicate("done\n",timeout=10)
    assert proc.returncode==0
    assert cleanup(store.settings,store,CleanupRequest(target="cache"))["removed_files"]==0


def test_queued_jobs_hold_storage_lease(store):
    from balloon_predictor.api import Jobs
    import threading
    store.settings.workers=1
    jobs=Jobs(store.settings,store)
    blocker=threading.Event();started=threading.Event()
    def task(progress):
        started.set();blocker.wait(5);return {"ok":True}
    try:
        running=jobs.submit("weather",task)
        assert started.wait(2)
        queued=jobs.submit("weather",lambda _: {"ok":True})
        assert jobs.get(queued["id"])["status"]=="queued"
        with pytest.raises(PredictorError,match="Storage is in use"):
            cleanup(store.settings,store,CleanupRequest(target="cache"))
    finally:
        blocker.set();jobs.executor.shutdown(wait=True)
    assert not list((store.settings.data_dir/"locks").glob("*.lease"))
