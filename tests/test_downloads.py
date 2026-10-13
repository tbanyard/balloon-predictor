from datetime import datetime, timedelta, timezone
import shutil
import types
import sys

import pytest

from balloon_predictor.errors import PredictorError
from balloon_predictor.models import DownloadRequest
from balloon_predictor.settings import Settings
from balloon_predictor.weather.download import cached_retrieve, download_weather, forecast_steps, hourly_window
from conftest import make_grib


def test_cds_request_schema_cache_and_release_check(tmp_path,monkeypatch):
    raw=tmp_path/"source.grib";make_grib(raw,version=5)
    calls=[]
    class FakeClient:
        def __init__(self,**kwargs):pass
        def retrieve(self,dataset,selection,target):
            calls.append((dataset,selection));shutil.copyfile(raw,target)
    monkeypatch.setitem(sys.modules,"cdsapi",types.SimpleNamespace(Client=FakeClient))
    settings=Settings(tmp_path/"data")
    request=DownloadRequest(source="era5t",name="recent",start="2024-01-15T12:15:00Z",end="2024-01-15T17:45:00Z",area=[55,-6,51,-2])
    ds=download_weather(request,settings)
    assert len(calls)==2
    assert calls[0][1]["time"]==[f"{hour}:00" for hour in range(12,19)]
    assert calls[0][1]["data_format"]=="grib"
    assert "1" in calls[0][1]["pressure_level"] and "1000" in calls[0][1]["pressure_level"]
    assert calls[0][1]["product_type"]==["reanalysis"]
    download_weather(request,settings)
    assert len(calls)==2 # Reuses cache.
    with pytest.raises(PredictorError,match="preliminary"):
        download_weather(request.model_copy(update={"source":"era5"}),settings)


def test_ifs_cycle_steps_and_mock_download(tmp_path,monkeypatch):
    raw=tmp_path/"source.grib";make_grib(raw)
    calls=[]
    class FakeClient:
        def __init__(self,**kwargs):
            assert kwargs["infer_stream_keyword"] is False
        def latest(self,**kwargs):
            return datetime(2024,1,15,kwargs["time"])
        def retrieve(self,**kwargs):
            calls.append(kwargs);shutil.copyfile(raw,kwargs["target"])
    import ecmwf.opendata
    monkeypatch.setattr(ecmwf.opendata,"Client",FakeClient)
    request=DownloadRequest(source="ifs",name="forecast",start="2024-01-15T12:15:00Z",end="2024-01-15T17:45:00Z",area=[55,-6,51,-2])
    ds=download_weather(request,Settings(tmp_path/"data"))
    assert len(calls)==7 # 0/3/6 h pressure and surface, plus static terrain.
    assert all(call["stream"]=="oper" for call in calls)
    pressure=[c for c in calls if c["levtype"]=="pl"]
    assert all(10 in c["levelist"] and "gh" in c["param"] for c in pressure)
    assert ds.attrs["source"]=="ifs"
    cycle=datetime(2024,1,15,12,tzinfo=timezone.utc)
    assert forecast_steps(cycle,cycle+timedelta(hours=145),cycle+timedelta(hours=155))==[144,150,156]
    with pytest.raises(PredictorError):forecast_steps(cycle,cycle-timedelta(hours=1),cycle)


def test_failed_cache_download_is_not_committed(tmp_path):
    def broken(path):
        path.write_bytes(b"partial");raise RuntimeError("download failed")
    with pytest.raises(RuntimeError):cached_retrieve(tmp_path,{"test":1},broken)
    assert not list(tmp_path.glob("*.grib"))
    assert not list(tmp_path.glob("*.part"))


def test_future_era5_rejected_without_remote_calls(tmp_path):
    start=datetime.now(timezone.utc)+timedelta(days=1)
    request=DownloadRequest(source="era5t",name="future",start=start,end=start+timedelta(hours=6))
    with pytest.raises(PredictorError,match="retrospective"):download_weather(request,Settings(tmp_path))
