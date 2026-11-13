import json
import shutil
import subprocess
import time

from fastapi.testclient import TestClient
import numpy as np
import pytest

from balloon_predictor.api import create_app
from balloon_predictor.models import PredictionRequest
from balloon_predictor.settings import Settings
from balloon_predictor.weather.download import cds_status
from conftest import make_normalized


def complete(client, job):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        state = client.get("/api/jobs/" + job).json()
        if state["status"] in ("failed","completed"):
            return state
        time.sleep(.025)
    pytest.fail("Job timed out")


def test_descent_dem_download_uses_the_missing_sample(tmp_path, monkeypatch):
    import rasterio
    from rasterio.transform import from_origin
    from balloon_predictor.terrain import TerrainCatalog, glo30_tile

    app = create_app(Settings(tmp_path / "data"))
    downloads = []
    def add_tile(self, lat, lon, progress):
        name = glo30_tile(lat,lon) + ".tif"
        downloads.append((lat,lon))
        # This small fixture covers the demo descent, not its launch site.
        with rasterio.open(self.root / name, "w", driver="GTiff", width=40, height=40, count=1,
                           dtype="float32", crs="EPSG:4326", transform=from_origin(lon-.2,lat+.2,.01,.01)) as dst:
            dst.write(np.full((1,40,40),20,dtype="float32"))
        return {"tile":name}
    monkeypatch.setattr(TerrainCatalog,"download",add_tile)

    req = PredictionRequest(launch_datetime="2024-01-15T12:00Z",landing_mode="dem",burst_altitude=1000,time_step=40)
    body = req.model_dump(mode="json")
    with TestClient(app) as client:
        bad = complete(client,client.post("/api/predictions",json=body).json()["id"])
        assert bad["status"]=="failed"
        detail = bad["error_details"]
        assert detail["phase"]=="descent" and detail["downloadable"]
        assert (detail["latitude"],detail["longitude"])!=(req.launch_latitude,req.launch_longitude)
        response = client.post("/api/terrain/download",json={"latitude":detail["latitude"],"longitude":detail["longitude"],"verify_pixel":True})
        assert complete(client,response.json()["id"])["status"]=="completed"
        assert downloads==[(detail["latitude"],detail["longitude"])]
        result = complete(client,client.post("/api/predictions",json=body).json()["id"])
        assert result["status"]=="completed", result
        assert result["result"]["summary"]["landing"]["altitude"]==pytest.approx(20)


def test_downloaded_dem_with_no_valid_pixel_is_not_offered_again(tmp_path, monkeypatch):
    from balloon_predictor.terrain import TerrainCatalog, glo30_tile
    app = create_app(Settings(tmp_path/"data"))
    def missing(self,lat,lon,progress):
        from balloon_predictor.errors import MissingTerrainError
        raise MissingTerrainError(lat,lon,glo30_tile(lat,lon)+".tif",False,no_public_tile=True)
    monkeypatch.setattr(TerrainCatalog,"download",missing)
    with TestClient(app) as client:
        r=client.post("/api/terrain/download",json={"latitude":0,"longitude":12,"verify_pixel":True})
        state=complete(client,r.json()["id"])
    assert not state["error_details"]["downloadable"]
    assert state["error_details"]["tile"]==glo30_tile(0,12)+".tif"


@pytest.mark.parametrize("raw,want",[
    ("status has been updated to accepted","waiting in their queue"),
    ("status has been updated to running","preparing the data"),
    ("status has been updated to successful","downloading to this server"),
    ("Request is queued","Queued at CDS"),
    ("key=some-token URL=https://example.com",None),
])
def test_cds_stage_reports_only_known_states(raw,want):
    got = cds_status(raw)
    assert want in got if want else got is None


def test_pack_replays_server_forecast_and_rejects_missing_coverage(tmp_path):
    if not shutil.which("node"):
        pytest.skip("Node is required to cross-check the Android solver")
    app = create_app(Settings(tmp_path/"data"))
    weather=make_normalized()
    weather.attrs.update(source="gfs",label="Synthetic GFS fixture")
    app.state.store.save("offline-fixture",weather)
    req=PredictionRequest(dataset="offline-fixture",launch_datetime="2024-01-15T13:00:00Z",
                          burst_altitude=1500,time_step=20)
    with TestClient(app) as client:
        job=complete(client,client.post("/api/predictions",json=req.model_dump(mode="json")).json()["id"])
        assert job["status"]=="completed",job
        r=client.post("/api/predictions/"+job["id"]+"/offline-pack")
        pack=complete(client,r.json()["id"])
        assert pack["status"]=="completed",pack
        saved=client.get("/api/exports/"+pack["id"]+"/download")
        assert saved.content.startswith(b"BPF1") and ".bpforecast" in saved.headers["content-disposition"]
    path=tmp_path/"fixture.bpforecast"
    path.write_bytes(saved.content)
    script="""
const fs=require('node:fs'),assert=require('node:assert/strict');
const {decode,fly}=require('./android/offline/engine.js');
const pack=decode(Uint8Array.from(fs.readFileSync(process.argv[1])).buffer);
const q=pack.info.setup,result=fly(pack,q);
assert.throws(()=>fly(pack,{...q,launch_datetime:'2030-01-01T12:00Z'}),/outside the saved forecast/);
assert.throws(()=>fly(pack,{...q,launch_latitude:12}),/outside the saved forecast/);
assert.throws(()=>fly(pack,{...q,landing_mode:'dem'}),/server mode/);
console.log(JSON.stringify(result));
"""
    replay=json.loads(subprocess.check_output(["node","-e",script,str(path)],text=True,timeout=20))
    for key in ("burst","landing"):
        expected=job["result"]["summary"][key]
        for coordinate in ("latitude","longitude","altitude"):
            assert replay[key][coordinate]==pytest.approx(expected[coordinate],abs=2e-5)
        assert replay[key]["elapsed_seconds"]==pytest.approx(expected["elapsed_seconds"],abs=.02)
