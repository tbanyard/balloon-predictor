"""Loopback SSH fixture used by Python and browser regression tests only."""
import asyncio
from concurrent.futures import Future
import json
from pathlib import Path
import secrets
import shlex
import sys
import threading
import time

import asyncssh

from balloon_predictor.login import ssh_command

PASSWORD = "fixture-password"
CODE = "246810"


class LoginSSH:
    def __init__(self, owner, directory, *, real_broker=False):
        self.owner, self.directory, self.real_broker = owner, Path(directory), real_broker
        self.ready = Future()
        self.thread = None
        self.loop = self.stop_event = None
        self.host_key = asyncssh.generate_private_key("ssh-ed25519")
        self.key_file = self.directory / "ssh-host.pub"
        self.directory.mkdir(parents=True, exist_ok=True)
        self.key_file.write_bytes(self.host_key.export_public_key())
        self.password_checks = 0

    def __enter__(self):
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()
        self.port = self.ready.result(10)
        self.owner.settings.web_login_ssh_port = self.port
        self.owner.settings.web_login_host_key_file = self.key_file
        return self

    def __exit__(self, *_):
        if self.loop and not self.loop.is_closed():
            self.loop.call_soon_threadsafe(self.stop_event.set)
        if self.thread:
            self.thread.join(10)

    def run(self):
        try:
            asyncio.run(self.serve())
        except Exception as error:
            if not self.ready.done():
                self.ready.set_exception(error)

    async def serve(self):
        self.loop = asyncio.get_running_loop()
        self.stop_event = asyncio.Event()
        fixture = self

        class Server(asyncssh.SSHServer):
            def begin_auth(self, username):
                self.user, self.stage = username, 0
                return True

            def password_auth_supported(self):
                return self.user != "otp"

            def kbdint_auth_supported(self):
                return self.user == "otp"

            def validate_password(self, username, password):
                fixture.password_checks += 1
                return username in ("alice", "bob", "alias") and password == PASSWORD

            def get_kbdint_challenge(self, username, lang, methods):
                return "Server sign-in", "", "", [("Password:", False)]

            def validate_kbdint_response(self, username, answers):
                if self.stage == 0 and answers == [PASSWORD]:
                    self.stage = 1
                    return "Server sign-in", "Enter your verification code.", "", [("Verification code:", False)]
                return self.stage == 1 and answers == [CODE]

        async def process(command):
            if shlex.split(command.command or "") != shlex.split(ssh_command(str(fixture.owner.socket_path))):
                command.exit(1)
                return
            if fixture.real_broker:
                args = shlex.split(command.command)
                child = await asyncio.create_subprocess_exec(sys.executable, *args[1:],
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                stdout, _ = await child.communicate()
                command.stdout.write(stdout.decode())
                command.exit(child.returncode)
                return
            # Stub only the final Unix socket boundary in sandboxes which
            # prohibit Unix binding. SSH/password/OTP and HTTP are real.
            typed = command.get_extra_info("username")
            canonical = "alice" if typed == "alias" else typed
            token = secrets.token_urlsafe(32)
            with fixture.owner.lock:
                fixture.owner.tickets[token] = (canonical, time.monotonic() + 60)
            command.stdout.write(json.dumps({"token": token, "username": canonical}) + "\n")
            command.exit(0)

        async with asyncssh.listen("127.0.0.1", 0, server_factory=Server,
                                   server_host_keys=[self.host_key], process_factory=process) as server:
            self.ready.set_result(server.get_port())
            await self.stop_event.wait()
