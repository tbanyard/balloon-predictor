"""Browser sign-in using the local SSH server and existing Unix identity broker."""
import asyncio
from collections import deque
from pathlib import Path
import re
import secrets
import time
import logging

from .login import ssh_command

LOG = logging.getLogger(__name__)
USERNAME = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.@-]*\$?")


class LoginError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


class Attempt:
    def __init__(self, username, password):
        self.id = secrets.token_urlsafe(32)
        self.username = username
        self._password = password
        self.started = time.monotonic()
        self.status = "connecting"
        self.message = "Checking your server account…"
        self.challenge = self.reply = self.task = self.result = None

    def password(self):
        answer, self._password = self._password, None
        return answer

    def public(self):
        return {"status": self.status, "message": self.message, "challenge": self.challenge}

    async def ask(self, text, prompts):
        if len(prompts) > 8:
            raise LoginError("The server returned too many sign-in questions. Ask its operator for help.")
        self.reply = asyncio.get_running_loop().create_future()
        self.challenge = {"id": secrets.token_urlsafe(18), "text": str(text)[:1000],
                          "prompts": [[str(label)[:200], bool(echo)] for label, echo in prompts]}
        self.status, self.message = "challenge", "Complete the server sign-in below."
        try:
            return await self.reply
        finally:
            self.reply = self.challenge = None
            self.status, self.message = "connecting", "Completing sign-in…"

    def answer(self, challenge, answers):
        if (not self.challenge or challenge != self.challenge["id"] or self.reply is None or self.reply.done()
                or not isinstance(answers, list) or len(answers) != len(self.challenge["prompts"])
                or any(not isinstance(value, str) or len(value) > 4096 for value in answers)):
            raise LoginError("This sign-in question has changed. Please try again.")
        self.reply.set_result(answers)

    def cancel(self):
        self._password = None
        if self.task:
            self.task.cancel()


class BrowserLogins:
    def __init__(self, owner):
        self.owner = owner
        self.attempts, self.rates = {}, {}

    def host_keys(self):
        import asyncssh
        configured = self.owner.settings.web_login_host_key_file
        candidates = [configured] if configured else [
            Path("/etc/ssh/ssh_host_ed25519_key.pub"),
            Path("/etc/ssh/ssh_host_ecdsa_key.pub"),
            Path("/etc/ssh/ssh_host_rsa_key.pub"),
        ]
        keys = []
        for path in candidates:
            try:
                keys.append(asyncssh.read_public_key(path))
            except (OSError, ValueError):
                continue
        if not keys:
            raise LoginError("Browser sign-in is not configured: the service cannot read the local SSH public host key. Ask the server operator to set web_login_host_key_file.", 503)
        return keys, [], []

    def begin(self, username, password, peer, previous=None):
        try:
            import asyncssh
        except ImportError:
            raise LoginError("The sign-in service needs an update. Ask the operator to restart it using the v1.6.4 shared-server launcher.", 503) from None
        if (not isinstance(username, str) or not 1 <= len(username) <= 128 or not USERNAME.fullmatch(username)
                or not isinstance(password, str) or not 1 <= len(password) <= 4096):
            raise LoginError("Enter your server username and password.")
        now = time.monotonic()
        for key, attempt in list(self.attempts.items()):
            if now - attempt.started > 600:
                self.cancel(key)
        self.rates = {key: stamps for key, stamps in self.rates.items() if stamps and now - stamps[-1] < 60}
        for key, limit in (("all", 120), ("peer:" + peer, 20), ("user:" + username, 6)):
            stamps = self.rates.setdefault(key, deque())
            while stamps and now - stamps[0] >= 60:
                stamps.popleft()
            if len(stamps) >= limit:
                raise LoginError("Too many sign-in attempts. Wait a minute and try again.", 429)
        if sum(attempt.status in ("connecting", "challenge") for attempt in self.attempts.values()) >= 8 or len(self.attempts) >= 64:
            raise LoginError("The sign-in service is busy. Please try again shortly.", 429)
        if previous:
            self.cancel(previous)
        for key in ("all", "peer:" + peer, "user:" + username):
            self.rates[key].append(now)
        attempt = Attempt(username, password)
        self.attempts[attempt.id] = attempt
        attempt.task = asyncio.create_task(self.run(attempt))
        return attempt

    async def authenticate(self, attempt):
        import asyncssh
        manager = self

        class Client(asyncssh.SSHClient):
            def password_auth_requested(self):
                return attempt.password()

            def kbdint_auth_requested(self):
                return ""

            async def kbdint_challenge_received(self, name, instructions, lang, prompts):
                if not prompts:
                    return []
                if len(prompts) == 1 and re.search(r"password", prompts[0][0], re.IGNORECASE) and attempt._password is not None:
                    return [attempt.password()]
                return await attempt.ask(instructions or name or "Enter the sign-in code requested by your server.", prompts)

        async with asyncssh.connect(
            "127.0.0.1", port=self.owner.settings.web_login_ssh_port, username=attempt.username,
            known_hosts=self.host_keys(), config=None, client_factory=Client,
            client_keys=None, agent_path=None, preferred_auth=("keyboard-interactive", "password"),
            connect_timeout=20, login_timeout=600, keepalive_interval=25, keepalive_count_max=3,
        ) as connection:
            reply = await connection.run(ssh_command(str(self.owner.socket_path)), check=True, timeout=12)
            import json
            try:
                if len(reply.stdout) > 4096:
                    raise ValueError()
                identity = json.loads(reply.stdout)
                token = identity["token"]
                if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9_-]{43}", token):
                    raise ValueError()
            except (ValueError, KeyError, TypeError):
                raise LoginError("SSH accepted the sign-in, but the personal workspace could not be opened. Ask the operator to check its permissions.", 503) from None
            # A verified Unix peer, rather than the typed username, selects
            # the folder. The broker ticket never goes into a browser URL.
            return manager.owner.consume_ticket(token)

    async def run(self, attempt):
        import asyncssh
        try:
            attempt.result = await asyncio.wait_for(self.authenticate(attempt), timeout=600)
            attempt.status, attempt.message = "authenticated", "Opening your personal workspace…"
        except asyncio.CancelledError:
            attempt.status, attempt.message = "cancelled", "Sign-in cancelled."
            raise
        except asyncssh.PermissionDenied:
            attempt.status, attempt.message = "failed", "The server did not accept that username, password or sign-in code. Please try again."
        except asyncssh.HostKeyNotVerifiable:
            attempt.status, attempt.message = "failed", "The local SSH server's identity could not be verified. Ask the operator to check the SSH public host key."
        except (asyncio.TimeoutError, TimeoutError):
            attempt.status, attempt.message = "failed", "The sign-in session timed out. Please sign in again."
        except LoginError as error:
            attempt.status, attempt.message = "failed", str(error)
        except Exception as error:
            LOG.error("Browser sign-in failed: %s", type(error).__name__)
            attempt.status, attempt.message = "failed", "Could not contact the server's SSH sign-in service. Ask the operator to check its local SSH port and service log."
        finally:
            attempt._password = None

    def cancel(self, identifier):
        attempt = self.attempts.pop(identifier, None)
        if attempt:
            attempt.cancel()
            if attempt.result:
                with self.owner.lock:
                    self.owner.sessions.pop(attempt.result[1], None)

    async def close(self):
        tasks = []
        for attempt in self.attempts.values():
            attempt.cancel()
            if attempt.task:
                tasks.append(attempt.task)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self.attempts.clear()
