"""The installed app runs the existing BalloonPredictor UI on loopback."""
import argparse
from contextlib import contextmanager, nullcontext
import faulthandler
import json
import logging
import os
from pathlib import Path
import socket
import sys
from threading import Thread
import time
from urllib.request import ProxyHandler, build_opener

HOST = "127.0.0.1"
# Keep the same origin between launches so WebView2 keeps saved setups/layout.
PORT = 48761
ROOT = Path(__file__).resolve().parent
LOG = logging.getLogger("balloon-predictor.desktop")
OPENER = build_opener(ProxyHandler({}))


@contextmanager
def single_instance():
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    create = kernel.CreateMutexW
    create.argtypes = (ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR)
    create.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    handle = create(None, False, r"Local\BalloonPredictor.Desktop")
    error = ctypes.get_last_error()
    if not handle:
        raise ctypes.WinError(error)
    try:
        if error == 183:  # ERROR_ALREADY_EXISTS
            raise RuntimeError("BalloonPredictor is already running. Switch to its open window.")
        yield
    finally:
        kernel.CloseHandle(handle)


def configure(root):
    root.mkdir(parents=True, exist_ok=True)
    config = root / "config.yml"
    if not config.exists():
        folder = "." if sys.platform.startswith("linux") else "data"
        config.write_text(
            "# Paths are relative to this file.\n"
            f"data_dir: {folder}\nera5_root: era5\nwrf_root: wrf\ndefault_dataset: demo\n",
            encoding="utf-8",
        )
    # The desktop install uses its own config even if the user also runs the
    # server edition with a different archive or data directory.
    for key in ("BP_DATA_DIR", "BP_ERA5_ROOT", "BP_WRF_ROOT", "BP_TERRAIN_DIR", "BP_CACHE_MB"):
        os.environ.pop(key, None)
    os.environ["BP_CONFIG"] = str(config)
    return config


class DesktopOrigin:
    """Keep websites from submitting jobs or cleanup requests to the local app."""

    def __init__(self, app, *, origin):
        self.app = app
        self.origin = origin.encode("ascii")
        self.host = self.origin.split(b"//", 1)[1]

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            from starlette.responses import JSONResponse

            headers = dict(scope["headers"])
            if headers.get(b"host") != self.host:
                return await JSONResponse({"detail": "Invalid local host."}, 400)(scope, receive, send)
            if scope["method"] not in {"GET", "HEAD", "OPTIONS"} and (
                headers.get(b"origin", self.origin) != self.origin
                or (scope["path"] == "/api/storage/location" and headers.get(b"origin") != self.origin)
                or headers.get(b"sec-fetch-site", b"same-origin") not in {b"same-origin", b"none"}
            ):
                return await JSONResponse({"detail": "Open BalloonPredictor to make this request."}, 403)(
                    scope, receive, send
                )
        await self.app(scope, receive, send)


@contextmanager
def serve(root, port):
    import uvicorn

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        if sys.platform == "win32":
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        else:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            listener.bind((HOST, port))
        except OSError as error:
            raise RuntimeError(
                f"BalloonPredictor could not open its local port {port}. "
                "Close any other BalloonPredictor window and try again."
            ) from error

        config = configure(root)
        from balloon_predictor.api import create_app
        from balloon_predictor.settings import Settings

        app = create_app(Settings.load(config))
        app.state.edit_storage_location = True

        port = listener.getsockname()[1]
        url = f"http://{HOST}:{port}"
        server = uvicorn.Server(
            uvicorn.Config(
                DesktopOrigin(app, origin=url), host=HOST, port=port, loop="asyncio",
                http="h11", ws="none", proxy_headers=False, log_config=None,
                access_log=False, timeout_graceful_shutdown=5, lifespan="on",
            )
        )
        worker = Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
        worker.start()
        try:
            deadline = time.monotonic() + 30
            while not server.started:
                if not worker.is_alive() or time.monotonic() >= deadline:
                    raise RuntimeError("BalloonPredictor's local server did not start.")
                time.sleep(0.05)
            with OPENER.open(url + "/api/health", timeout=5) as response:
                if json.load(response)["status"] != "ok":
                    raise RuntimeError("BalloonPredictor's local server did not become ready.")
            LOG.info("BalloonPredictor is ready at %s", url)
            yield url, app.state.store
        finally:
            server.should_exit = True
            worker.join(10)
            if worker.is_alive():
                LOG.warning("Local server did not stop before the shutdown timeout.")


def open_window(url, root, verify=False):
    import webview

    # The Inno installer provides this runtime if needed. Fail explicitly if
    # the renderer fell back to the legacy Internet Explorer engine.
    from webview.guilib import initialize
    renderer = initialize("edgechromium")
    if renderer.renderer != "edgechromium":
        raise RuntimeError("Microsoft Edge WebView2 Runtime is missing. Re-run BalloonPredictor Setup.")
    webview.settings["ALLOW_DOWNLOADS"] = True
    webview.settings["ALLOW_FILE_URLS"] = False
    webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = True
    splash = webview.create_window(
        "Starting BalloonPredictor", url + "splash.html", width=330, height=300,
        frameless=True, transparent=True, easy_drag=False, resizable=False, on_top=True,
    )
    chooser_url = url + "?splash=done"
    window = webview.create_window(
        "BalloonPredictor", chooser_url, width=1280, height=850, min_size=(760, 570), hidden=True,
        menu=[Menu("Connection", [MenuAction("Choose server…", lambda: window.load_url(chooser_url))])],
    )
    from webview.menu import Menu, MenuAction

    checked = []

    def verify_frontend():
        try:
            if not window.events.loaded.wait(30):
                raise RuntimeError("The Windows window did not finish loading.")
            deadline = time.monotonic() + 50
            while time.monotonic() < deadline:
                try:
                    window.evaluate_js(
                        "if (document.querySelector('#connect') && !window.__bpLocal) {"
                        "window.__bpLocal = true; document.querySelector('#connect').click(); }"
                    )
                except webview.errors.JavascriptException:
                    time.sleep(.2)
                    continue
                if window.evaluate_js(
                    "Boolean(window.L && document.querySelector('#dataset')?.value === 'demo' && "
                    "document.querySelector('#predict-button')?.disabled === false)"
                ):
                    checked.append(True)
                    LOG.info("Windows WebView2 rendered the UI with Demo weather selected.")
                    return
                time.sleep(0.2)
            raise RuntimeError("The Windows window did not initialise the flight controls.")
        except Exception:
            LOG.exception("Windows window check failed")
        finally:
            window.destroy()

    def start_window():
        # The two seconds start when the splash document is ready.
        splash.events.loaded.wait(15)
        time.sleep(2)
        try:
            window.show()
        finally:
            splash.destroy()
        if verify:
            verify_frontend()

    webview.start(
        start_window, gui="edgechromium",
        private_mode=False, storage_path=str(root / "webview"),
        icon=str(ROOT / "windows" / "BalloonPredictor.ico"),
    )
    if verify and not checked:
        raise RuntimeError("The Windows window check did not pass.")


def notify_failure(message):
    if sys.platform == "win32":
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, message, "BalloonPredictor", 0x10)
    else:
        print(message, file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(description="Start the BalloonPredictor desktop app.")
    parser.add_argument("--smoke-test", type=Path, help="Run installed-app checks in a fresh test folder.")
    parser.add_argument("--check-window", action="store_true", help="Also render the Windows window during the check.")
    args = parser.parse_args()
    if args.check_window and args.smoke_test is None:
        parser.error("--check-window requires --smoke-test")
    if args.smoke_test is None and sys.platform != "win32":
        parser.error("The desktop window is supported on Windows.")
    root = (args.smoke_test or Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local")) / "BalloonPredictor").resolve()
    log_path = root / "BalloonPredictor.log"
    try:
        root.mkdir(parents=True, exist_ok=True)
        if log_path.exists() and log_path.stat().st_size > 2_000_000:
            log_path.replace(root / "BalloonPredictor.previous.log")
        with log_path.open("a", encoding="utf-8", buffering=1) as output:
            if sys.stdout is None:
                sys.stdout = output
            if sys.stderr is None:
                sys.stderr = output
            logging.basicConfig(
                level=logging.INFO, stream=output,
                format="%(asctime)s %(levelname)s %(name)s: %(message)s",
            )
            faulthandler.enable(output)
            try:
                from desktop.connection import Chooser

                with (nullcontext() if args.smoke_test else single_instance()):
                    if args.smoke_test:
                        from desktop.smoke import check_runtime

                        with serve(root, 0) as (url, store):
                            check_runtime(url, store)
                    if args.check_window or args.smoke_test is None:
                        with Chooser(root, port=0 if args.smoke_test else PORT) as chooser:
                            open_window(chooser.url, root, verify=args.check_window)
                if args.smoke_test:
                    (root / "smoke-test-passed.txt").write_text(
                        "Installed runtime, local API, GRIB, terrain and both NetCDF exports passed.\n"
                        + ("Windows WebView2 window passed.\n" if args.check_window else ""),
                        encoding="utf-8",
                    )
            except Exception:
                LOG.exception("BalloonPredictor stopped")
                raise
            finally:
                faulthandler.disable()
        return 0
    except Exception as error:
        if not args.smoke_test:
            notify_failure(f"BalloonPredictor could not start.\n\n{error}\n\nLog: {log_path}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
