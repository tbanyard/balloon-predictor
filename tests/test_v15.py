from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import subprocess
import sys
from threading import Thread
import time
from urllib.request import Request, urlopen

import pytest
from fastapi.testclient import TestClient

from balloon_predictor.api import create_app
from balloon_predictor.settings import Settings, user_data_dir
from desktop.connection import Chooser, direct_url
from desktop.launcher import DesktopOrigin


def wait(get, check=bool):
    deadline = time.monotonic() + 12
    while time.monotonic() < deadline:
        value = get()
        if check(value):
            return value
        time.sleep(.04)
    raise AssertionError(f"Timed out: {value!r}")


def test_default_storage_and_override(monkeypatch, tmp_path):
    monkeypatch.delenv("BP_DATA_DIR", raising=False)
    config = tmp_path / "config.yml"
    config.write_text("workers: 1\n")
    assert Settings.load(config).data_dir == user_data_dir().resolve()
    config.write_text("data_dir: another drive/data\n")
    assert Settings.load(config).data_dir == tmp_path / "another drive/data"
    monkeypatch.setenv("BP_DATA_DIR", str(tmp_path / "override"))
    assert Settings.load(config).data_dir == tmp_path / "override"


def test_local_gui_storage_preference(monkeypatch, tmp_path):
    monkeypatch.delenv("BP_DATA_DIR", raising=False)
    config = tmp_path / "config.yml"
    config.write_text("data_dir: old\nworkers: 1\n")
    app = create_app(Settings.load(config))
    chosen = tmp_path / "New Data Folder"
    url = "http://127.0.0.1:48761"
    endpoint = "/api/storage/location"
    with TestClient(DesktopOrigin(app, origin=url), base_url=url) as client:
        assert client.post(endpoint, headers={"Origin":url}, json={"path":str(chosen)}).status_code==403
        app.state.edit_storage_location = True
        assert client.post(endpoint, json={"path":str(chosen)}).status_code==403
        assert client.post(endpoint, headers={"Origin":"https://different.example"}, json={"path":str(chosen)}).status_code==403
        assert client.post(endpoint, headers={"Origin":url}, json={"path":"relative"}).status_code==422
        assert client.post(endpoint, headers={"Origin":url}, json={"path":str(chosen)}).json()["restart_required"]
        info = client.get("/api/storage").json()
        assert Path(info["data_dir"]) == tmp_path / "old" and Path(info["pending_data_dir"]) == chosen
    settings = Settings.load(config)
    assert chosen.is_dir() and settings.data_dir == chosen and settings.workers == 1


def test_era5_error_points_to_redacted_job_log(monkeypatch, tmp_path):
    def conversion_failed(body, settings, progress):
        progress("Converting ERA5 GRIB")
        raise RuntimeError("private provider token")

    monkeypatch.setattr("balloon_predictor.api.download_weather", conversion_failed)
    settings = Settings(tmp_path)
    with TestClient(create_app(settings)) as client:
        response = client.post("/api/weather/download", json={"name":"era5-example","source":"era5",
            "start":"2024-01-15T12:00:00Z","end":"2024-01-15T13:00:00Z","area":[56,-9,49,1]})
        assert response.status_code == 202, response.text
        job = response.json()["id"]
        state = wait(lambda: client.get("/api/jobs/" + job).json(), lambda x: x["status"]=="failed")
        assert str(settings.log_path) in state["error"] and job in state["error"]
    log = settings.log_path.read_text()
    assert job in log and "RuntimeError" in log and "Converting ERA5 GRIB" in log
    assert "private provider token" not in log


def test_direct_url_validation():
    assert direct_url("https://example.org:443/", False) == "https://example.org"
    for value in ("http://example.org","https://user:secret@example.org","https://example.org/path"):
        with pytest.raises(ValueError):
            direct_url(value, False)


def test_chooser_real_ssh_password_and_changed_host(tmp_path):
    pytest.importorskip("asyncssh")
    class Health(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"status":"ok","time_reference":"UTC"}')
        def log_message(self, *_):
            pass

    def request(url, action, data=None):
        origin = url[:url.index("/", len("http://"))]
        req = Request(url + action) if data is None else Request(url + action, data=json.dumps(data).encode(),
            headers={"Origin":origin,"Content-Type":"application/json"})
        with urlopen(req, timeout=8) as response:
            return json.load(response)

    ready = tmp_path / "ready.json"
    with ThreadingHTTPServer(("127.0.0.1", 0), Health) as http:
        Thread(target=http.serve_forever, daemon=True).start()
        proc = subprocess.Popen([sys.executable,"scripts/ssh-test-fixture.py",str(ready),str(http.server_port)])
        try:
            info = wait(lambda: json.loads(ready.read_text()) if ready.is_file() else None)
            with Chooser(tmp_path / "chooser", port=0) as chooser:
                url = chooser.url
                opts = {"mode":"ssh","host":"127.0.0.1","username":"fixture","ssh_port":info["port"],"web_port":http.server_port}
                request(url,"connect",opts)
                trust = wait(lambda: request(url,"state")["challenge"])
                assert trust["kind"] == "trust" and info["fingerprint"] in trust["text"]
                request(url,"answer",{"id":trust["id"],"values":[]})
                password = wait(lambda:request(url,"state")["challenge"],lambda x:x and x["kind"]=="credentials")
                request(url,"answer",{"id":password["id"],"values":["fixture-password"]})
                done = wait(lambda:request(url,"state"),lambda x:not x["busy"])
                assert done["target"], done["error"]
                assert request(done["target"]+"/","api/health")["status"]=="ok"
                assert "fixture-password" not in chooser.preferences.read_text()
                chooser.pins[f"127.0.0.1:{info['port']}"] = "SHA256:wrong"
                request(url,"connect",opts)
                done = wait(lambda:request(url,"state"),lambda x:not x["busy"])
                assert "changed" in done["error"] and done["challenge"] is None
        finally:
            proc.terminate()
            proc.wait(8)
            http.shutdown()
