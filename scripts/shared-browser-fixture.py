"""HTTP integration fixture; production SSH peer credentials have separate tests."""
import json
from pathlib import Path
import secrets
import socket
import sys
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import uvicorn
from balloon_predictor.multiuser import SharedServer
from balloon_predictor.settings import Settings
from scripts.web_login_fixture import LoginSSH, PASSWORD, CODE

root, ready = Path(sys.argv[1]), Path(sys.argv[2])
listener = socket.socket()
listener.bind(("127.0.0.1", 0))
port = listener.getsockname()[1]
app = SharedServer(Settings(root / "owner", shared_data_root=root / "users", default_dataset="demo"), port)
app.socket_path = Path("/browser-auth-fixture/identity.sock")
first, second = secrets.token_urlsafe(32), secrets.token_urlsafe(32)

def start():
    app.tickets[first] = ("alice", time.monotonic()+60)
    app.tickets[second] = ("bob", time.monotonic()+60)

app.start = start
server = uvicorn.Server(uvicorn.Config(app, access_log=False, log_level="warning"))
threading.Thread(target=lambda: (sys.stdin.buffer.read(), setattr(server, "should_exit", True)), daemon=True).start()
with LoginSSH(app, root):
    ready.write_text(json.dumps({"url": f"http://127.0.0.1:{port}", "first": first, "second": second,
                                 "password": PASSWORD, "code": CODE}))
    server.run(sockets=[listener])
