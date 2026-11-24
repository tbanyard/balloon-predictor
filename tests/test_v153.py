import asyncio
import errno
from pathlib import Path
import time
from tempfile import TemporaryDirectory

import asyncssh
from fastapi.testclient import TestClient
import pytest

from balloon_predictor.disk import os_account
from balloon_predictor.multiuser import SharedServer
from balloon_predictor.settings import Settings
from scripts.web_login_fixture import LoginSSH, PASSWORD, CODE

ORIGIN = {"Origin": "http://localhost"}


@pytest.fixture
def web_server(tmp_path):
    app = SharedServer(Settings(tmp_path / "owner", shared_data_root=tmp_path / "users", default_dataset="demo"), 8005)
    app.socket_path = Path("/browser-auth-fixture/identity.sock")
    app.start = lambda: None
    with LoginSSH(app, tmp_path), TestClient(app, base_url="http://localhost") as client:
        yield app, client


def terminal(client, target=("authenticated", "failed", "challenge")):
    for _ in range(250):
        value = client.get("/api/login").json()
        if value["status"] in target:
            return value
        time.sleep(.02)
    raise AssertionError("Sign-in did not reach the expected state")


def signin(client, username="alice", password=PASSWORD):
    response = client.post("/api/login", json={"username": username, "password": password}, headers=ORIGIN)
    assert response.status_code == 202, response.text
    assert password not in response.text
    return response


def test_login_page_replaces_instruction_page(web_server):
    _, client = web_server
    health = client.get("/api/health").json()
    assert health["version"] == "1.5.3" and health["browser_login"] is True
    page = client.get("/")
    assert page.status_code == 200 and 'id="signin-form"' in page.text and 'type="password"' in page.text
    assert "python -m" not in page.text and "60 seconds" not in page.text
    for asset in ("session-login.css", "session-login.js", "favicon.svg"):
        assert client.get("/assets/" + asset).status_code == 200
    assert "style-src 'self'" in page.headers["content-security-policy"]
    assert client.get("/api/weather").json()["login_required"]


def test_password_signin_and_logout_use_same_workspace(web_server, caplog):
    app, client = web_server
    response = signin(client)
    identifier = client.cookies.get(app.login_cookie)
    attempt = app.web_logins.attempts[identifier]
    result = terminal(client)
    assert result["status"] == "authenticated" and result["username"] == "alice"
    assert attempt._password is None and PASSWORD not in caplog.text
    assert client.get("/api/storage").json()["data_dir"] == str(app.root / "alice")
    assert client.cookies.get(app.cookie) and "HttpOnly" in response.headers["set-cookie"]
    assert client.post("/api/logout", headers={"Origin": "https://unrelated.invalid"}, json={}).status_code == 403
    assert client.get("/api/storage").status_code == 200
    assert client.post("/api/logout", headers=ORIGIN, json={}).status_code == 200
    assert client.get("/api/storage").status_code == 401 and not client.cookies.get(app.cookie)
    assert 'id="signin-form"' in client.get("/").text


def test_https_login_cookies_are_secure(web_server):
    app, client = web_server
    client.base_url = "https://localhost"
    response = client.post("/api/login", json={"username": "alice", "password": PASSWORD},
                           headers={"Origin": "https://localhost"})
    assert response.status_code == 202
    cookie = response.headers["set-cookie"]
    assert "Secure" in cookie and "HttpOnly" in cookie and "SameSite=strict" in cookie
    assert terminal(client)["status"] == "authenticated"
    assert client.get("/api/storage").json()["workspace_user"] == "alice"
    assert client.cookies.get(app.cookie)


@pytest.mark.parametrize("browser_login", [True, False])
def test_desktop_web_mode_recognises_browser_login(monkeypatch, tmp_path, browser_login):
    from desktop.connection import Chooser
    monkeypatch.setattr("desktop.connection.verify_server", lambda url:
                        {"requires_ssh_identity": True, "browser_login": browser_login})
    chooser = Chooser(tmp_path / "chooser")
    chooser.connect_worker({"mode": "web", "address": "http://server.example:8005", "allow_http": True})
    if browser_login:
        assert chooser.state["target"] == "http://server.example:8005" and chooser.state["error"] is None
    else:
        assert chooser.state["target"] is None and "SSH tunnel" in chooser.state["error"]


def test_failed_password_can_retry_without_link(web_server, caplog):
    app, client = web_server
    signin(client, password="wrong-secret")
    assert terminal(client)["status"] == "failed"
    assert not app.sessions and "wrong-secret" not in caplog.text
    signin(client)
    assert terminal(client)["status"] == "authenticated"


def test_broker_identity_selects_folder_instead_of_typed_alias(web_server):
    app, client = web_server
    signin(client, username="alias")
    assert terminal(client)["username"] == "alice"
    assert client.get("/api/storage", headers={"X-BP-User": "bob"}).json()["workspace_user"] == "alice"
    assert not (app.root / "alias").exists()


def test_second_factor_is_bound_to_initiating_browser(web_server):
    app, client = web_server
    signin(client, username="otp")
    state = terminal(client)
    assert state["status"] == "challenge" and state["challenge"]["prompts"] == [["Verification code:", False]]
    question = state["challenge"]["id"]
    other = TestClient(app, base_url="http://localhost")
    try:
        assert other.post("/api/login/answer", headers=ORIGIN, json={"id": question, "answers": [CODE]}).status_code == 401
    finally:
        other.close()
    assert client.post("/api/login/answer", headers={"Origin": "https://unrelated.invalid"}, json={"id": question, "answers": [CODE]}).status_code == 403
    assert client.post("/api/login/answer", headers=ORIGIN, json={"id": "wrong-question", "answers": [CODE]}).status_code == 400
    assert client.post("/api/login/answer", headers=ORIGIN, json={"id": question, "answers": [CODE]}).status_code == 200
    assert terminal(client)["status"] == "authenticated"
    assert client.get("/api/storage").json()["workspace_user"] == "otp"


def test_cancel_clears_password_and_pending_ssh(web_server):
    app, client = web_server
    signin(client, username="otp")
    assert terminal(client)["status"] == "challenge"
    attempt = next(iter(app.web_logins.attempts.values()))
    assert client.post("/api/login/cancel", json={}, headers=ORIGIN).status_code == 200
    assert not app.web_logins.attempts and attempt._password is None
    assert client.get("/api/login").json()["status"] == "idle"
    assert not client.cookies.get(app.cookie)


def test_rate_limit_and_origin_checks(web_server):
    app, client = web_server
    assert client.post("/api/login", json={"username": "alice", "password": PASSWORD}).status_code == 403
    assert client.post("/api/login", headers=ORIGIN, content="password").status_code == 415
    for _ in range(6):
        signin(client, password="wrong")
        assert terminal(client)["status"] == "failed"
    response = client.post("/api/login", headers=ORIGIN, json={"username": "alice", "password": PASSWORD})
    assert response.status_code == 429 and not app.sessions


def test_wrong_ssh_host_key_is_rejected(web_server, tmp_path):
    app, client = web_server
    key = tmp_path / "wrong.pub"
    key.write_bytes(asyncssh.generate_private_key("ssh-ed25519").export_public_key())
    app.settings.web_login_host_key_file = key
    signin(client)
    result = terminal(client)
    assert result["status"] == "failed" and "could not be verified" in result["message"]
    assert not app.sessions


def test_app_ticket_reconnect_works_with_existing_browser_session(web_server):
    app, client = web_server
    signin(client)
    assert terminal(client)["status"] == "authenticated"
    assert 'id="signin-form"' in client.get("/signin").text
    app.tickets["native-ticket"] = ("bob", time.monotonic()+60)
    assert client.post("/api/session", json={"token": "native-ticket"}, headers=ORIGIN).status_code == 200
    assert client.get("/api/storage").json()["workspace_user"] == "bob"


def test_full_browser_ssh_and_kernel_broker_handoff():
    with TemporaryDirectory(prefix="auth-", dir=Path(__file__).resolve().parents[2]) as temp:
        root = Path(temp)
        app = SharedServer(Settings(root / "owner", shared_data_root=root / "users", default_dataset="demo"), 8005)
        try:
            app.start()
        except PermissionError:
            app.close()
            import os
            if os.environ.get("BP_REQUIRE_UNIX_BROKER_TEST") == "1":
                raise
            pytest.skip("Unix socket binding is blocked here; full handoff is mandatory in Linux CI")
        app.start = lambda: None
        with LoginSSH(app, root, real_broker=True), TestClient(app, base_url="http://localhost") as client:
            signin(client)
            result = terminal(client)
            assert result["status"] == "authenticated" and result["username"] == os_account()
            assert client.get("/api/storage").json()["workspace_user"] == os_account()
