"""One Linux service, with workspaces authenticated by SSH/Unix credentials."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from http.cookies import SimpleCookie, CookieError
import json
import logging
import os
from pathlib import Path
import re
import secrets
import stat
import socket
import socketserver
import struct
import threading
import time

from filelock import FileLock
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse

from . import __version__
from .disk import personal_data_dir, storage_failure
from .errors import PredictorError
from .settings import ROOT
from .web_login import BrowserLogins, LoginError


class SharedServer:
    def __init__(self, settings, port):
        if not hasattr(socket, "SO_PEERCRED") or not hasattr(socket, "AF_UNIX"):
            raise PredictorError("Shared SSH workspaces require a Linux server. Use local mode on this device.")
        self.settings, self.port = settings, port
        self.root = settings.shared_data_root
        self.socket_path = self.root / f".server-{port}" / "identity.sock"
        self.apps, self.tickets, self.sessions = {}, {}, {}
        self.lock = threading.RLock()
        self.executor = ThreadPoolExecutor(max_workers=settings.workers)
        self.broker = self.broker_lock = None
        self.cookie = f"bp_session_{port}"
        self.login_cookie = f"bp_login_{port}"
        self.web_logins = BrowserLogins(self)

    def consume_ticket(self, token):
        with self.lock:
            value = self.tickets.pop(token, None)
            now = time.monotonic()
            if not value or value[1] < now:
                raise LoginError("The automatic sign-in could not be completed. Please sign in again.", 401)
            self.sessions = {key: entry for key, entry in self.sessions.items() if entry[1] > now}
            if len(self.sessions) >= 4096:
                raise LoginError("The sign-in service is busy. Please try again shortly.", 429)
            session = secrets.token_urlsafe(32)
            self.sessions[session] = (value[0], now + 12 * 3600)
        return value[0], session

    def cookie_value(self, headers, name):
        try:
            cookie = SimpleCookie()
            cookie.load(headers.get("cookie", ""))
            return cookie[name].value if name in cookie else None
        except CookieError:
            return None

    def set_session(self, response, session, scheme):
        response.set_cookie(self.cookie, session, httponly=True, samesite="strict",
                            secure=scheme == "https", path="/")

    def request_allowed(self, scope, headers):
        return headers.get("origin") == scope["scheme"] + "://" + headers.get("host", "") and headers.get("sec-fetch-site", "same-origin") in ("same-origin", "none")

    async def login_request(self, scope, receive, headers):
        path, method, scheme = scope["path"], scope["method"], scope["scheme"]
        identifier = self.cookie_value(headers, self.login_cookie)
        attempt = self.web_logins.attempts.get(identifier)
        if method == "GET" and path == "/api/login":
            if not attempt:
                return JSONResponse({"status": "idle", "message": "Sign in with your server account."})
            if attempt.status == "authenticated":
                username, session = attempt.result
                self.web_logins.attempts.pop(identifier, None)
                response = JSONResponse({"status": "authenticated", "username": username})
                self.set_session(response, session, scheme)
                response.delete_cookie(self.login_cookie, path="/")
                return response
            return JSONResponse(attempt.public())
        if method != "POST" or not self.request_allowed(scope, headers):
            return JSONResponse({"detail": "Submit sign-in requests from this BalloonPredictor page."}, status_code=403)
        try:
            if headers.get("content-type", "").split(";", 1)[0].strip() != "application/json":
                raise LoginError("Use the sign-in form on this page.", 415)
            length = int(headers.get("content-length", "0"))
            if not 0 < length <= 20000:
                raise LoginError("The sign-in request was too large or incomplete.")
            data = await Request(scope, receive).json()
            if not isinstance(data, dict):
                raise LoginError("Use the sign-in form on this page.")
            if path == "/api/login":
                peer = str((scope.get("client") or ("unknown", 0))[0])
                password = data.pop("password", None)
                attempt = self.web_logins.begin(data.get("username"), password, peer, identifier)
                password = None
                response = JSONResponse(attempt.public(), status_code=202)
                response.set_cookie(self.login_cookie, attempt.id, httponly=True, samesite="strict",
                                    secure=scheme == "https", path="/")
                return response
            if path == "/api/login/answer" and attempt:
                attempt.answer(data.get("id"), data.get("answers"))
                return JSONResponse({"status": "connecting", "message": "Completing sign-in…"})
            if path == "/api/login/cancel":
                self.web_logins.cancel(identifier)
                response = JSONResponse({"status": "idle", "message": "Sign-in cancelled."})
                response.delete_cookie(self.login_cookie, path="/")
                return response
            raise LoginError("This sign-in session ended. Please sign in again.", 401)
        except LoginError as error:
            return JSONResponse({"detail": str(error)}, status_code=error.status)
        except (ValueError, TypeError):
            return JSONResponse({"detail": "Use the sign-in form on this page."}, status_code=400)

    def start(self):
        self.root.mkdir(parents=True, exist_ok=True)
        mode = self.root.stat().st_mode
        if mode & stat.S_IWOTH and not mode & stat.S_ISVTX:
            if self.root.stat().st_uid != os.geteuid():
                raise PredictorError("Ask the BP_Data owner to set chmod 1777 on the shared root before starting this service.")
            self.root.chmod(stat.S_IMODE(mode) | stat.S_ISVTX)
        directory = self.socket_path.parent
        if directory.is_symlink() or (directory.exists() and directory.stat().st_uid != os.geteuid()):
            raise PredictorError("The shared sign-in directory is not owned by this server account.")
        directory.mkdir(parents=True, exist_ok=True, mode=0o755)
        self.broker_lock = FileLock(str(directory / "identity.lock"), timeout=0)
        self.broker_lock.acquire()
        try:
            self.socket_path.unlink(missing_ok=True)
            owner = self

            class Handler(socketserver.StreamRequestHandler):
                def handle(self):
                    import pwd
                    try:
                        _, uid, _ = struct.unpack("iII", self.request.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
                        username = pwd.getpwuid(uid).pw_name
                        personal_data_dir(owner.root, username)
                        with owner.lock:
                            now = time.monotonic()
                            owner.tickets = {key: value for key, value in owner.tickets.items() if value[1] > now}
                            if len(owner.tickets) >= 1024:
                                raise PredictorError("Too many pending sign-ins. Wait a minute and retry.")
                            token = secrets.token_urlsafe(32)
                            owner.tickets[token] = (username, now + 60)
                        result = {"token": token, "username": username}
                    except Exception:
                        result = {"error": "Could not open this Unix account's personal workspace. Ask the server operator to check its folder permissions."}
                    self.wfile.write(json.dumps(result).encode() + b"\n")

            class Broker(socketserver.ThreadingUnixStreamServer):
                daemon_threads = True

            self.broker = Broker(str(self.socket_path), Handler)
            os.chmod(self.socket_path, 0o666)
            threading.Thread(target=self.broker.serve_forever, daemon=True).start()
        except Exception:
            self.broker_lock.release()
            raise

    def close(self):
        self.executor.shutdown(wait=False, cancel_futures=True)
        if self.broker:
            self.broker.shutdown()
            self.broker.server_close()
            self.socket_path.unlink(missing_ok=True)
        if self.broker_lock:
            self.broker_lock.release()

    def workspace(self, username):
        from .api import create_app
        with self.lock:
            if username in self.apps:
                return self.apps[username]
            if len(self.apps) >= 32:
                raise PredictorError("This server has reached its workspace limit. Ask the operator to restart it when active work has finished.")
            target = personal_data_dir(self.root, username)
            target.mkdir(parents=True, exist_ok=True, mode=0o700)
            target.chmod(0o700)
            def relocate(path):
                if path is None:
                    return None
                try:
                    return target / path.relative_to(self.settings.data_dir)
                except ValueError:
                    return path
            selected = replace(self.settings, data_dir=target, shared_server=False,
                               workspace_user=username, era5_root=relocate(self.settings.era5_root),
                               wrf_root=relocate(self.settings.wrf_root), terrain_dir=relocate(self.settings.terrain_dir))
            self.apps[username] = create_app(selected, executor=self.executor)
            return self.apps[username]

    def session_user(self, headers):
        token = self.cookie_value(headers, self.cookie)
        with self.lock:
            value = self.sessions.get(token)
            if value and value[1] > time.monotonic():
                self.sessions[token] = (value[0], time.monotonic() + 12 * 3600)
                return value[0]
            if token:
                self.sessions.pop(token, None)
        return None

    async def __call__(self, scope, receive, send):
        if scope["type"] == "lifespan":
            while True:
                message = await receive()
                if message["type"] == "lifespan.startup":
                    try:
                        self.start()
                    except Exception as error:
                        await send({"type": "lifespan.startup.failed", "message": str(error)})
                        raise
                    await send({"type": "lifespan.startup.complete"})
                elif message["type"] == "lifespan.shutdown":
                    await self.web_logins.close()
                    self.close()
                    await send({"type": "lifespan.shutdown.complete"})
                    return
        if scope["type"] != "http":
            return
        path, method = scope["path"], scope["method"]
        headers = {key.decode().lower(): value.decode() for key, value in scope["headers"]}
        if path == "/api/health" and method == "GET":
            response = JSONResponse({"status": "ok", "version": __version__, "time_reference": "UTC",
                                     "requires_ssh_identity": True, "browser_login": True, "identity_socket": str(self.socket_path)})
        elif path in ("/assets/session-login.js", "/assets/session-login.css", "/assets/favicon.svg") and method == "GET":
            mime = {".js": "text/javascript", ".css": "text/css", ".svg": "image/svg+xml"}[Path(path).suffix]
            response = FileResponse(ROOT / "frontend" / Path(path).name, media_type=mime)
        elif path == "/signin" and method == "GET":
            response = FileResponse(ROOT / "frontend" / "session-login.html")
        elif path in ("/api/login", "/api/login/answer", "/api/login/cancel"):
            response = await self.login_request(scope, receive, headers)
        elif path == "/api/logout" and method == "POST":
            if not self.request_allowed(scope, headers):
                response = JSONResponse({"detail": "Sign out from your own BalloonPredictor page."}, status_code=403)
            else:
                with self.lock:
                    self.sessions.pop(self.cookie_value(headers, self.cookie), None)
                self.web_logins.cancel(self.cookie_value(headers, self.login_cookie))
                response = JSONResponse({"status": "signed_out"})
                response.delete_cookie(self.cookie, path="/")
                response.delete_cookie(self.login_cookie, path="/")
        elif path == "/api/session" and method == "POST":
            if not self.request_allowed(scope, headers):
                response = JSONResponse({"detail": "Open the sign-in link on this BalloonPredictor server."}, status_code=403)
            else:
                request = Request(scope, receive)
                try:
                    length = int(headers.get("content-length", "0"))
                    if not 0 < length < 256:
                        raise ValueError()
                    token = (await request.json())["token"]
                    if not isinstance(token, str):
                        raise ValueError()
                    username, session = self.consume_ticket(token)
                    response = JSONResponse({"username": username})
                    self.set_session(response, session, scope["scheme"])
                except (ValueError, KeyError, TypeError, LoginError):
                    response = JSONResponse({"detail": "That sign-in link expired. Sign in with your username and password below."}, status_code=401)
        else:
            username = self.session_user(headers)
            if username:
                # Session cookies select a workspace; request data never does.
                if method not in ("GET", "HEAD", "OPTIONS") and not self.request_allowed(scope, headers):
                    response = JSONResponse({"detail": "Submit this request from your own BalloonPredictor workspace."}, status_code=403)
                else:
                    try:
                        app = await asyncio.to_thread(self.workspace, username)
                    except (PredictorError, OSError) as error:
                        target = self.root / username
                        detail = storage_failure(error, target)
                        logging.getLogger(__name__).error("Cannot open workspace %s: %s errno=%s", username, type(error).__name__, detail["errno"] if detail else None)
                        response = JSONResponse({"detail": detail["message"] if detail else f"Cannot open your personal data folder: {target}. Ask the server operator to check its space, ownership and permissions.",
                                                 "error_details": detail}, status_code=507 if detail else 503)
                    else:
                        async def private_send(message):
                            if message["type"] == "http.response.start":
                                extra = [(b"x-frame-options", b"DENY"), (b"referrer-policy", b"no-referrer")]
                                if not path.startswith("/assets/"):
                                    extra.append((b"cache-control", b"no-store"))
                                keys = {key for key, _ in extra}
                                message = {**message, "headers": [(key, value) for key, value in message.get("headers", []) if key.lower() not in keys] + extra}
                            await send(message)
                        await app(scope, receive, private_send)
                        return
            elif path.startswith("/api/"):
                response = JSONResponse({"detail": "Your sign-in session ended. Please sign in again.", "login_required": True}, status_code=401)
            else:
                response = FileResponse(ROOT / "frontend" / "session-login.html")
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
        await response(scope, receive, send)
