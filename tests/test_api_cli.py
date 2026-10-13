import json
import time
from xml.etree.ElementTree import fromstring

from fastapi.testclient import TestClient
import pytest

from balloon_predictor.api import create_app
from balloon_predictor.cli import main
from balloon_predictor.exports import segments
from balloon_predictor.settings import Settings
from balloon_predictor.store import WeatherStore
from conftest import make_normalized


def test_async_api_all_exports_and_persistence(tmp_path):
    settings=Settings(tmp_path)
    app=create_app(settings)
    with TestClient(app) as client:
        assert client.get("/").status_code==200
        assert client.get("/api/health").json()["status"]=="ok"
        assert client.get("/api/weather").json()["datasets"][0]["id"]=="demo"
        response=client.post("/api/predictions",json={"launch_datetime":"2024-01-15T12:00:00Z","burst_altitude":1000})
        assert response.status_code==202
        job_id=response.json()["id"]
        for _ in range(100):
            job=client.get("/api/jobs/"+job_id).json()
            if job["status"] in ("completed","failed"):break
            time.sleep(.01)
        assert job["status"]=="completed",job
        for format in ("csv","geojson","kml","json"):
            response=client.get(f"/api/predictions/{job_id}/export/{format}")
            assert response.status_code==200
            assert "attachment" in response.headers["content-disposition"]
            if format=="csv":assert response.text.startswith("member,stage,datetime_utc")
            if format=="geojson":assert response.json()["type"]=="FeatureCollection"
            if format=="kml":assert fromstring(response.text).tag.endswith("kml")
        assert (tmp_path/"results"/(job_id+".json")).exists()
    with TestClient(create_app(settings)) as client:
        assert client.get("/api/jobs/"+job_id).json()["status"]=="completed"


def test_tawhiri_style_and_validation(tmp_path):
    with TestClient(create_app(Settings(tmp_path))) as client:
        response=client.get("/api/v1/",params={"launch_datetime":"2024-01-15T12:00:00Z",
                            "launch_latitude":52,"launch_longitude":355.875,"burst_altitude":1000})
        assert response.status_code==200
        assert response.json()["prediction"][0]["stage"]=="ascent"
        assert client.post("/predict",json={"launch_datetime":"bad"}).status_code==422
        assert client.post("/predict",json={"launch_datetime":"2024-01-15T12:00:00Z","dataset":"../secret"}).status_code==422
        assert client.get("/assets/../.cdsapirc").status_code==404


def test_weather_store_roundtrip_and_duplicate_guard(tmp_path,utc):
    store=WeatherStore(Settings(tmp_path))
    info=store.save("test-weather",make_normalized())
    assert info["terrain_available"]
    assert store.get("test-weather").sample(52,-5,5000,utc).u==pytest.approx(7,abs=1e-5)
    from balloon_predictor.errors import PredictorError
    with pytest.raises(PredictorError,match="already exists"):store.save("test-weather",make_normalized())


def test_cli_demo_export(tmp_path):
    config=tmp_path/"config.yml";config.write_text("data_dir: data\n")
    request=tmp_path/"flight.json";request.write_text(json.dumps({"launch_datetime":"2024-01-15T12:00:00Z","burst_altitude":1000}))
    output=tmp_path/"output"/"flight"
    assert main(["--config",str(config),"predict",str(request),"--output",str(output)])==0
    for suffix in ("json","csv","geojson","kml"):assert output.with_suffix("."+suffix).exists()


def test_antimeridian_export_splits():
    points=[{"longitude":179,"latitude":52,"altitude":100},{"longitude":-179,"latitude":53,"altitude":200}]
    lines=segments(points)
    assert len(lines)==2
    assert lines[0][-1]==[180,52.5,150]
    assert lines[1][0]==[-180,52.5,150]
