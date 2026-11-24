"""Loopback-only SSH server for password authentication and forwarding tests."""
import asyncio
import json
from pathlib import Path
import sys
import shlex
import asyncssh
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class Fixture(asyncssh.SSHServer):
    def begin_auth(self, username):
        return True

    def password_auth_supported(self):
        return True

    def validate_password(self, username, password):
        return username == "fixture" and password == "fixture-password"

    def connection_requested(self, dest_host, dest_port, orig_host, orig_port):
        return dest_host == "127.0.0.1" and dest_port == int(sys.argv[2])


async def main():
    async def process(command):
        try:
            from balloon_predictor.login import ssh_command
            allowed = ssh_command(sys.argv[4]) if len(sys.argv) > 4 else None
            if allowed is None or shlex.split(command.command) != shlex.split(allowed):
                command.exit(1)
                return
            arguments = shlex.split(command.command)
            child = await asyncio.create_subprocess_exec(sys.executable, *arguments[1:],
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            stdout, _ = await asyncio.wait_for(child.communicate(), 12)
            command.stdout.write(stdout.decode())
            command.exit(child.returncode)
        except Exception:
            command.exit(1)
    key = asyncssh.generate_private_key("ssh-ed25519")
    async with asyncssh.listen("127.0.0.1", int(sys.argv[3]) if len(sys.argv)>3 else 0,
                              server_factory=Fixture, server_host_keys=[key], process_factory=process) as server:
        path = Path(sys.argv[1])
        temp = path.with_suffix(".tmp")
        temp.write_text(json.dumps({"port": server.get_port(), "fingerprint": key.get_fingerprint("sha256")}))
        temp.replace(path)
        await asyncio.Future()


if __name__ == "__main__":
    asyncio.run(main())
