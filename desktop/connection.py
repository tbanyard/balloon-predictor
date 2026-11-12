"""Private local connection screen and authenticated SSH forwarding."""
import asyncio
from concurrent.futures import Future, TimeoutError
from contextlib import ExitStack
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
from pathlib import Path
import re
import secrets
import threading
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, build_opener

from desktop.launcher import PORT, serve

UI = Path(__file__).with_name("connect-ui")
LOG = logging.getLogger("balloon-predictor.connection")


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def verify_server(url):
    try:
        with build_opener(ProxyHandler({}), NoRedirect()).open(url + "/api/health", timeout=8) as response:
            info = json.loads(response.read(32768))
        if info.get("status") == "ok" and info.get("time_reference") == "UTC":
            return
    except (OSError, ValueError, AttributeError):
        pass
    raise ValueError("BalloonPredictor did not answer. Check the VPN, server address and web port.")


def direct_url(value, allow_http):
    parsed = urlsplit(value)
    if (parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username is not None
            or parsed.path not in ("", "/") or parsed.query or parsed.fragment
            or any(ch.isspace() or ch in "\\\\%@" for ch in parsed.netloc)):
        raise ValueError("Enter a server address such as https://server.example.org (no path).")
    port = parsed.port
    if port == 0:
        raise ValueError("The web port must be between 1 and 65535.")
    if parsed.scheme == "http" and not allow_http:
        raise ValueError("Use HTTPS, or explicitly allow HTTP on your trusted network or VPN.")
    host = parsed.hostname.encode("idna").decode("ascii")
    if ":" in host:
        host = f"[{host}]"
    return f"{parsed.scheme}://{host}" + (f":{port}" if port and port != (443 if parsed.scheme == "https" else 80) else "")


def read_json(path):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def write_json(path, value):
    temp = path.with_suffix(".new")
    temp.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    temp.replace(path)


class Chooser:
    def __init__(self, root, port=PORT, target_file=None):
        self.root, self.port, self.target_file = root, port, target_file
        root.mkdir(parents=True, exist_ok=True)
        self.preferences, self.pins_file = root / "connection.json", root / "ssh-hosts.json"
        self.prefs, self.pins = read_json(self.preferences), read_json(self.pins_file)
        self.token = "/" + secrets.token_urlsafe(24) + "/"
        self.lock = threading.RLock()
        self.state = {"busy": False, "target": None, "error": None, "challenge": None}
        self.session = self.local_url = self.forward = self.response = self.worker = None
        self.closed = False
        self.stack = ExitStack()

    def __enter__(self):
        chooser = self
        assets = {"": ("index.html", "text/html"), "screen.css": ("screen.css", "text/css"),
                  "screen.js": ("screen.js", "text/javascript"), "balloon.svg": ("balloon.svg", "image/svg+xml")}

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def reply(self, status, data, mime="application/json"):
                if mime == "application/json":
                    data = json.dumps(data).encode()
                self.send_response(status)
                self.send_header("Content-Type", mime)
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header("Content-Security-Policy", "default-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
                self.end_headers()
                self.wfile.write(data)

            def local(self):
                return self.headers.get("Host") == chooser.origin.split("//", 1)[1] and self.path.startswith(chooser.token)

            def do_GET(self):
                if not self.local():
                    return self.reply(404, {})
                name = self.path[len(chooser.token):]
                if name == "state":
                    with chooser.lock:
                        result = {**chooser.state, "resume": chooser.session, "prefs": chooser.prefs}
                    return self.reply(200, result)
                if name not in assets:
                    return self.reply(404, {})
                if name == "":
                    with chooser.lock:
                        chooser.state["target"] = None
                path, mime = assets[name]
                self.reply(200, (UI / path).read_bytes(), mime)

            def do_POST(self):
                if not self.local() or self.headers.get("Origin") != chooser.origin:
                    return self.reply(403, {"error": "Use the BalloonPredictor connection screen."})
                if self.headers.get_content_type() != "application/json":
                    return self.reply(415, {"error": "Invalid request."})
                try:
                    size = int(self.headers.get("Content-Length", "0"))
                    if not 0 < size <= 16384:
                        raise ValueError("Invalid connection request.")
                    self.connection.settimeout(5)
                    data = json.loads(self.rfile.read(size))
                    action = {"connect": chooser.connect, "answer": chooser.answer,
                              "cancel": chooser.cancel, "resume": chooser.resume}.get(self.path[len(chooser.token):])
                    if action is None or not isinstance(data, dict):
                        return self.reply(404, {})
                    action(data)
                    self.reply(200, {})
                except (ValueError, OSError) as exc:
                    self.reply(400, {"error": str(exc)})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.origin = f"http://127.0.0.1:{self.server.server_port}"
        self.url = self.origin + self.token
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        return self

    def __exit__(self, *_):
        with self.lock:
            self.closed = True
            self.cancel()
        self.server.shutdown()
        self.server.server_close()
        if self.worker:
            self.worker.join(2)
        if self.forward:
            self.forward.stop()
        self.stack.close()

    def activate(self, url):
        if self.target_file:
            write_json(self.target_file, {"url": url})
        self.session = url
        self.state.update(busy=False, target=url, challenge=None)

    def resume(self, _):
        with self.lock:
            if not self.session or self.state["busy"]:
                raise ValueError("No flight session is available.")
            self.activate(self.session)

    def connect(self, options):
        mode = options.get("mode")
        if mode not in {"local", "web", "ssh"}:
            raise ValueError("Choose how to connect.")
        values = {key: str(options.get(key, "")).strip() for key in ("address", "host", "username", "ssh_port", "web_port")}
        if any(len(v)>512 for v in values.values()):
            raise ValueError("A connection field is too long.")
        values.update(mode=mode, allow_http=options.get("allow_http") is True)
        with self.lock:
            if self.closed or self.state["busy"]:
                raise ValueError("A connection is already in progress.")
            self.state.update(busy=True, target=None, error=None, challenge=None)
            self.worker = threading.Thread(target=self.connect_worker, args=(values,), daemon=True)
            self.worker.start()

    def connect_worker(self, values):
        forward = None
        try:
            if values["mode"] == "local":
                if self.local_url is None:
                    with ExitStack() as stack:
                        url, _ = stack.enter_context(serve(self.root, self.port))
                        with self.lock:
                            if self.closed:
                                return
                            self.stack.enter_context(stack.pop_all())
                            self.local_url = url
                url = self.local_url
            elif values["mode"] == "web":
                url = direct_url(values["address"], values["allow_http"])
                verify_server(url)
            else:
                if not re.fullmatch(r"[a-zA-Z0-9._:-]+", values["host"]) or not values["username"]:
                    raise ValueError("Enter the SSH host and your SSH username.")
                try:
                    ssh_port, web_port = int(values["ssh_port"]), int(values["web_port"])
                    if not (1 <= ssh_port <= 65535 and 1 <= web_port <= 65535):
                        raise ValueError()
                except ValueError:
                    raise ValueError("Enter SSH and web ports between 1 and 65535.") from None
                forward = Forward(self, values["host"], ssh_port, values["username"], web_port)
                url = forward.start()
                try:
                    verify_server(url)
                except ValueError:
                    raise ValueError(f"SSH signed in, but BalloonPredictor did not answer on the server's 127.0.0.1:{web_port}.") from None
            with self.lock:
                if self.closed:
                    return
                write_json(self.preferences, values)
                self.prefs = values
                self.activate(url)
                old, self.forward, forward = self.forward, forward, None
            if old:
                old.stop()
        except Exception as error:
            LOG.error("Connection failed: %s", type(error).__name__)
            with self.lock:
                self.state.update(busy=False, challenge=None, error=str(error) if isinstance(error, ValueError) else
                                  "Could not start the connection. See BalloonPredictor.log.")
        finally:
            if forward:
                forward.stop()

    def ask(self, kind, text, prompts=()):
        future = Future()
        with self.lock:
            if self.closed:
                raise ValueError("Connection cancelled.")
            self.response = future
            self.state["challenge"] = {"id": secrets.token_urlsafe(16), "kind": kind, "text": text, "prompts": prompts}
        try:
            result = future.result(timeout=90)
            if result is None:
                raise ValueError("Connection cancelled.")
            return result
        except TimeoutError:
            raise ValueError("The SSH prompt timed out. Try connecting again.") from None
        finally:
            with self.lock:
                self.response = self.state["challenge"] = None

    def answer(self, data):
        with self.lock:
            challenge = self.state["challenge"]
            if not challenge or data.get("id") != challenge["id"] or self.response.done():
                raise ValueError("This SSH prompt has expired.")
            values = data.get("values", [])
            if not isinstance(values, list) or len(values) != len(challenge["prompts"]) or not all(
                isinstance(v, str) and len(v) <= 4096 for v in values
            ):
                raise ValueError("Invalid SSH response.")
            self.response.set_result(values)

    def cancel(self, _=None):
        with self.lock:
            if self.response and not self.response.done():
                self.response.set_result(None)

    def trust(self, endpoint, fingerprint):
        if endpoint in self.pins:
            if self.pins[endpoint] != fingerprint:
                raise ValueError(f"The SSH key for {endpoint} changed. Check with your server operator before changing ssh-hosts.json.")
            return True
        self.ask("trust", f"{endpoint} offers {fingerprint}. Compare it with the fingerprint supplied by the server operator.")
        with self.lock:
            self.pins[endpoint] = fingerprint
            write_json(self.pins_file, self.pins)
        return True


class Forward:
    def __init__(self, chooser, host, ssh_port, username, web_port):
        self.chooser, self.host, self.ssh_port, self.username, self.web_port = chooser, host, ssh_port, username, web_port
        self.ready = Future()
        self.loop = self.task = None
        self.thread = threading.Thread(target=self.run, daemon=True)

    def start(self):
        self.thread.start()
        return self.ready.result(timeout=310)

    def stop(self):
        if self.loop and self.task and not self.loop.is_closed():
            try:
                self.loop.call_soon_threadsafe(self.task.cancel)
            except RuntimeError:
                pass
        self.thread.join(2)

    def run(self):
        try:
            asyncio.run(self.tunnel())
        except asyncio.CancelledError:
            pass
        except Exception as error:
            LOG.error("SSH failed: %s", type(error).__name__)
            if not self.ready.done():
                self.ready.set_exception(error if isinstance(error, ValueError) else
                    ValueError("SSH could not connect or sign in. Check the VPN, username, port and credentials."))

    async def tunnel(self):
        import asyncssh

        self.loop, self.task = asyncio.get_running_loop(), asyncio.current_task()
        endpoint, forward = f"{self.host}:{self.ssh_port}", self
        chooser = self.chooser

        class Client(asyncssh.SSHClient):
            attempts = 0

            def validate_host_public_key(self, host, addr, port, key):
                try:
                    return chooser.trust(endpoint, key.get_fingerprint("sha256"))
                except ValueError as exc:
                    if not forward.ready.done():
                        forward.ready.set_exception(exc)
                    return False

            async def password_auth_requested(self):
                self.attempts += 1
                if self.attempts > 2:
                    return None
                return (await asyncio.to_thread(chooser.ask, "credentials", f"SSH password for {endpoint}",
                                                [("Password", False)]))[0]

            def kbdint_auth_requested(self):
                return ""

            async def kbdint_challenge_received(self, name, instructions, lang, prompts):
                return await asyncio.to_thread(chooser.ask, "credentials", instructions or name or f"SSH sign-in for {endpoint}", prompts)

        async with asyncssh.connect(self.host, port=self.ssh_port, username=self.username, config=None,
                                    known_hosts=([], [], []), client_factory=Client, client_keys=None, agent_path=None,
                                    preferred_auth=("keyboard-interactive", "password"), connect_timeout=300,
                                    login_timeout=300, keepalive_interval=25, keepalive_count_max=3) as connection:
            listener = await connection.forward_local_port("127.0.0.1", 0, "127.0.0.1", self.web_port)
            self.ready.set_result(f"http://127.0.0.1:{listener.get_port()}")
            try:
                await connection.wait_closed()
            finally:
                listener.close()
                await listener.wait_closed()
