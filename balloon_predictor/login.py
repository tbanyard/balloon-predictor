"""Obtain a personal browser session from an already authenticated Unix login."""
import argparse
import base64
import json
from pathlib import Path
import shlex
import socket
import sys

# Executed through SSH by desktop/mobile clients. Only the path is supplied
# by the remote health check, encoded as data, never interpolated as code.
PEER_CLIENT = (
    "import base64,socket,sys;"
    "s=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM);s.settimeout(8);"
    "s.connect(base64.b64decode(sys.argv[1]).decode());"
    "print(s.makefile('rb').readline(4096).decode().strip())"
)


def ssh_command(path):
    if not isinstance(path, str) or not path.startswith("/") or len(path.encode()) > 103 or "\x00" in path:
        raise ValueError("The server returned an invalid personal-workspace sign-in address.")
    return "python3 -c " + shlex.quote(PEER_CLIENT) + " " + shlex.quote(base64.b64encode(path.encode()).decode())


def local_login(path):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(8)
        client.connect(str(path))
        result = json.loads(client.makefile("rb").readline(4096))
    if "error" in result:
        raise ValueError(result["error"])
    return result


def main():
    parser = argparse.ArgumentParser(description="Print a personal sign-in link after logging into the server with SSH.")
    parser.add_argument("--port", type=int, default=8005)
    parser.add_argument("--shared-root", type=Path, default=Path("/data1/tim/BP_Data"))
    args = parser.parse_args()
    try:
        value = local_login(args.shared_root / f".server-{args.port}" / "identity.sock")
        print(f"Personal workspace: {value['username']}\nOpen through your VPN or SSH tunnel:")
        print(f"http://127.0.0.1:{args.port}/signin#{value['token']}")
        print("This sign-in link expires in 60 seconds. Replace the host/port with your forwarded address if needed.")
    except (OSError, ValueError) as error:
        print("Could not obtain a personal sign-in link. Check that the shared server is running on that port.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
