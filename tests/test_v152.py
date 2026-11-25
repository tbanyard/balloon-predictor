from contextlib import contextmanager
import errno
import json
import os
from pathlib import Path
import time
from tempfile import TemporaryDirectory

from fastapi.testclient import TestClient
import pytest

from balloon_predictor.api import create_app
from balloon_predictor.disk import os_account, personal_data_dir, storage_failure
from balloon_predictor.errors import PredictorError
from balloon_predictor.login import local_login, ssh_command
from balloon_predictor.migrate_storage import prepare
from balloon_predictor.multiuser import SharedServer
from balloon_predictor.settings import Settings, user_data_dir


def wait(client, job):
    for _ in range(200):
        result = client.get("/api/jobs/" + job["id"]).json()
        if result["status"] in ("completed", "failed"):
            return result
        time.sleep(.02)
    raise AssertionError("The job did not finish")


def test_shared_default_uses_real_unix_account(monkeypatch, tmp_path):
    monkeypatch.setenv("BP_SHARED_DATA_ROOT", str(tmp_path))
    monkeypatch.setenv("USER", "somebody-else")
    assert user_data_dir() == tmp_path / os_account()
    monkeypatch.delenv("BP_DATA_DIR", raising=False)
    config = tmp_path / "config.yml"
    config.write_text("workers: 1\n")
    assert Settings.load(config).data_dir == tmp_path / os_account()
    config.write_text("data_dir: explicit\n")
    assert Settings.load(config).data_dir == tmp_path / "explicit"


def test_reject_symlink_under_world_writable_parent(tmp_path):
    real = tmp_path / "other"
    real.mkdir()
    (tmp_path / os_account()).symlink_to(real, target_is_directory=True)
    with pytest.raises(PredictorError, match="symbolic link"):
        personal_data_dir(tmp_path)
    for user in ("../other", ".", "..", "/tmp/other", ""):
        with pytest.raises(PredictorError):
            personal_data_dir(tmp_path, user)


@pytest.mark.parametrize("number", [errno.ENOSPC, getattr(errno, "EDQUOT", 122), errno.EACCES, errno.EROFS])
def test_storage_diagnostics_follow_cause_without_provider_secrets(tmp_path, number):
    cause = OSError(number, "private-provider-token")
    wrapped = PredictorError("Weather download failed")
    wrapped.__cause__ = cause
    detail = storage_failure(wrapped, tmp_path)
    assert detail["errno"] == number and str(tmp_path) in detail["message"]
    assert "private-provider-token" not in json.dumps(detail)


def test_permission_error_identifies_external_archive(tmp_path):
    detail = storage_failure(PermissionError(errno.EACCES, "denied", "/external/ERA5/day.nc"), tmp_path)
    assert detail["affected_path"] == "/external/ERA5/day.nc"
    assert "/external/ERA5/day.nc" in detail["message"] and detail["data_dir"] == str(tmp_path)


def test_failed_result_write_reports_quota_and_finishes_job(monkeypatch, tmp_path):
    app = create_app(Settings(tmp_path))
    def fail(*_):
        raise OSError(122, "do-not-log-this")
    monkeypatch.setattr("balloon_predictor.api.atomic_json", fail)
    with TestClient(app) as client:
        job = client.post("/api/predictions", json={"dataset": "demo", "launch_datetime": "2024-01-15T12:00:00Z",
                          "flight_mode": "ascent", "burst_altitude": 1000, "ascent_rate": 5}).json()
        result = wait(client, job)
        assert result["status"] == "failed" and result["error_details"]["errno"] == 122
        assert "quota" in result["error"] and str(tmp_path) in result["error"]
    log = (tmp_path / "logs/balloon-predictor.log").read_text()
    assert "errno=122" in log and "do-not-log-this" not in log


def test_storage_failure_does_not_hide_health_or_recovery(monkeypatch, tmp_path):
    app = create_app(Settings(tmp_path))
    @contextmanager
    def failed_lease(_):
        raise OSError(errno.ENOSPC, "disk full")
        yield
    monkeypatch.setattr("balloon_predictor.storage.activity", failed_lease)
    def failed_probe(_):
        raise OSError(122, "quota")
    monkeypatch.setattr("balloon_predictor.disk.check_writable", failed_probe)
    with TestClient(app) as client:
        response = client.post("/api/weather/rescan")
        assert response.status_code == 507 and response.json()["error_details"]["errno"] == errno.ENOSPC
        assert client.get("/api/health").status_code == 200
        status = client.get("/api/storage/health").json()
        assert not status["ok"] and status["errno"] == 122
        assert client.get("/api/storage").status_code == 200


def test_migration_copies_and_rebases_input_receipts(tmp_path):
    old, root = tmp_path / "old", tmp_path / "BP_Data"
    (old / "weather").mkdir(parents=True)
    (old / "results").mkdir()
    source = old / "weather/forecast.nc"
    source.write_bytes(b"example weather")
    result = {"input_manifest": [{"path": str(source), "size": source.stat().st_size,
                                 "mtime_ns": source.stat().st_mtime_ns}]}
    (old / "results/test.json").write_text(json.dumps(result))
    config = tmp_path / "config.yml"
    config.write_text("era5_root: /external/ERA5\nworkers: 3\n")
    profile, report = prepare(root, old, config, progress=lambda _: None)
    dest = root / os_account()
    moved = json.loads((dest / "results/test.json").read_text())["input_manifest"][0]
    assert moved["path"] == str(dest / "weather/forecast.nc")
    assert moved["mtime_ns"] == (dest / "weather/forecast.nc").stat().st_mtime_ns
    assert source.exists() and report["files"] == 2
    assert profile.read_text().count("shared_server: true") == 1
    assert "/external/ERA5" in profile.read_text()
    assert dest.stat().st_mode & 0o777 == 0o700
    again, _ = prepare(root, old, config, progress=lambda _: None)
    assert again == profile


def test_migration_refuses_overwrites(tmp_path):
    old, root = tmp_path / "old", tmp_path / "BP_Data"
    old.mkdir()
    root.mkdir()
    target = personal_data_dir(root)
    target.mkdir()
    (target / "valuable").write_text("keep")
    with pytest.raises(PredictorError, match="already contains data"):
        prepare(root, old, tmp_path / "config.yml", progress=lambda _: None)
    assert (target / "valuable").read_text() == "keep"


@pytest.mark.skipif(not hasattr(__import__("socket"), "SO_PEERCRED"), reason="Linux peer credentials required")
def test_shared_server_verifies_peer_and_isolates_workspaces(tmp_path):
    with TemporaryDirectory(prefix="s-", dir=Path(__file__).resolve().parents[2]) as short:
        assert_shared_workspaces(Path(short))


def assert_shared_workspaces(tmp_path):
    settings = Settings(tmp_path / "owner", shared_server=True, shared_data_root=tmp_path / "BP_Data", workers=2)
    shared = SharedServer(settings, 8008)
    # Exercise the HTTP boundary independently of the Unix listener. The
    # listener itself is tested below on hosts which permit Unix sockets.
    shared.start = lambda: None
    with TestClient(shared, base_url="http://localhost") as first:
        assert first.get("/api/weather").status_code == 401
        identity = {"username": os_account(), "token": "verified-first-user"}
        shared.tickets[identity["token"]] = (identity["username"], time.monotonic()+60)
        headers = {"Origin": "http://localhost"}
        login = first.post("/api/session", json={"token": identity["token"]}, headers=headers)
        assert login.status_code == 200 and "HttpOnly" in login.headers["set-cookie"]
        assert first.post("/api/session", json={"token": identity["token"]}, headers=headers).status_code == 401
        one = first.get("/api/storage").json()
        assert one["workspace_user"] == os_account()
        assert Path(one["data_dir"]) == settings.shared_data_root / os_account()
        assert first.post("/api/weather/rescan").status_code == 403
        rescan = first.post("/api/weather/rescan", json={}, headers=headers)
        assert rescan.status_code == 202 and wait(first, rescan.json())["status"] == "completed"
        first_job = first.post("/api/predictions", headers=headers, json={
            "dataset": "demo", "launch_datetime": "2024-01-15T12:00:00Z",
            "flight_mode": "ascent", "burst_altitude": 1000, "ascent_rate": 5}).json()
        assert wait(first, first_job)["status"] == "completed"
        # Supply a second verified identity at the broker boundary. HTTP
        # callers have no endpoint that can mint or select this identity.
        with shared.lock:
            shared.tickets["test-second-ticket"] = ("workmate", time.monotonic()+60)
        second = TestClient(shared, base_url="http://localhost")
        try:
            assert second.post("/api/session", json={"token": "test-second-ticket"}, headers={"Origin": "https://unrelated.invalid"}).status_code == 403
            assert second.post("/api/session", json={"token": "test-second-ticket"}, headers=headers).status_code == 200
            two = second.get("/api/storage").json()
            assert two["workspace_user"] == "workmate" and one["data_dir"] != two["data_dir"]
            assert second.get("/api/jobs/" + first_job["id"]).status_code == 404
            assert first.get("/api/storage", headers={"X-BP-User": "workmate"}).json()["workspace_user"] == os_account()
        finally:
            second.close()


def test_identity_broker_uses_kernel_credentials():
    import socket
    if not hasattr(socket, "SO_PEERCRED"):
        pytest.skip("Linux peer credentials required")
    with TemporaryDirectory(prefix="p-", dir=Path(__file__).resolve().parents[2]) as short:
        root = Path(short)
        shared = SharedServer(Settings(root / "owner", shared_data_root=root / "users"), 8008)
        try:
            shared.start()
        except PermissionError:
            shared.close()
            if os.environ.get("BP_REQUIRE_UNIX_BROKER_TEST") == "1":
                raise
            pytest.skip("This execution sandbox does not permit Unix socket binding; required by Linux CI")
        try:
            result = local_login(shared.socket_path)
            assert result["username"] == os_account()
            assert result["token"] in shared.tickets
        finally:
            shared.close()


def test_site_mount_defaults_to_shared_server_but_respects_local_config(monkeypatch, tmp_path):
    monkeypatch.delenv("BP_DATA_DIR", raising=False)
    monkeypatch.delenv("BP_SHARED_DATA_ROOT", raising=False)
    monkeypatch.setattr("balloon_predictor.settings.SHARED_DATA_ROOT", tmp_path)
    config = tmp_path / "config.yml"
    config.write_text("workers: 2\n")
    settings = Settings.load(config)
    assert settings.data_dir == tmp_path / os_account() and settings.shared_server
    config.write_text("data_dir: explicit\n")
    assert not Settings.load(config).shared_server
    config.write_text("shared_server: false\n")
    assert not Settings.load(config).shared_server


def test_ssh_command_treats_socket_path_as_data():
    import shlex
    import base64
    path = "/some path/$(not-a-command)/identity.sock"
    command = shlex.split(ssh_command(path))
    assert command[:2] == ["python3", "-c"] and base64.b64decode(command[3]).decode() == path
    for path in (None, "relative", "/bad\x00path", "/" + "a"*105):
        with pytest.raises(ValueError):
            ssh_command(path)
