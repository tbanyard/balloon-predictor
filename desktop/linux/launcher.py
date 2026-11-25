"""GTK shell; run the separate engine with the bundled Python."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
from urllib.parse import urlsplit
import webbrowser
import sys

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("WebKit2", "4.1")
from gi.repository import Gio, GLib, Gtk, WebKit2

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from balloon_predictor.disk import SHARED_DATA_ROOT, personal_data_dir


def origin(url):
    try:
        u = urlsplit(url)
        if u.username is None and u.scheme in ("https", "http") and u.hostname:
            return u.scheme, u.hostname, u.port or (443 if u.scheme == "https" else 80)
    except (ValueError, TypeError):
        pass
    return None


class App:
    def __init__(self, root, temp, smoke):
        self.root, self.temp, self.smoke = root, temp, smoke
        self.info = None
        self.passed = self.downloading = False
        self.generation = 0
        self.flight_url = None
        self.roundtrip = 0
        self.probing = False
        self.deadline = time.monotonic() + 100
        self.window = Gtk.Window(title="BalloonPredictor")
        self.window.set_default_size(1280, 850)
        self.window.set_size_request(760, 570)
        self.window.set_icon_name("balloon-predictor")
        self.window.connect("destroy", lambda *_: Gtk.main_quit())
        bar = Gtk.HeaderBar(title="BalloonPredictor", show_close_button=True)
        button = Gtk.Button(label="Server")
        button.connect("clicked", self.show_chooser)
        bar.pack_end(button)
        self.window.set_titlebar(bar)
        data = root / "webview"
        manager = WebKit2.WebsiteDataManager(base_data_directory=str(data / "data"), base_cache_directory=str(data / "cache"))
        self.context = WebKit2.WebContext.new_with_website_data_manager(manager)
        self.context.connect("download-started", self.on_download)
        self.web = WebKit2.WebView.new_with_context(self.context)
        self.chooser = WebKit2.WebView.new_with_context(self.context)
        self.views = Gtk.Stack()
        self.views.add_named(self.web, "flight")
        self.views.add_named(self.chooser, "chooser")
        from balloon_predictor import __version__
        for view in (self.web, self.chooser):
            view.get_settings().set_user_agent_with_application_details(
                "BalloonPredictor", __version__ + " (+https://github.com/tbanyard/balloon-predictor)")
            view.connect("decide-policy", self.navigate)
            view.connect("load-failed", self.load_failed)
        self.window.add(self.views)
        self.window.show_all()
        self.web.load_html("<!doctype html><p>Starting BalloonPredictor…</p>", None)
        self.log = (root / "BalloonPredictor.log").open("ab")
        cmd = [str(ROOT / "runtime/bin/python3.13"), "-I", "-B", "-u", str(ROOT / "desktop/host.py"),
               "--data-dir", str(root), "--ready-file", str(temp / "ready.json")]
        if smoke:
            cmd.append("--smoke-test")
        self.engine = subprocess.Popen(cmd, cwd=root, stdin=subprocess.PIPE, stdout=self.log, stderr=subprocess.STDOUT)
        GLib.timeout_add(200, self.poll)

    def shutdown(self):
        self.engine.stdin.close()
        try:
            self.engine.wait(10)
        except subprocess.TimeoutExpired:
            self.engine.terminate()
            try:
                self.engine.wait(5)
            except subprocess.TimeoutExpired:
                self.engine.kill()
                self.engine.wait()
        self.log.close()

    def show_chooser(self, *_):
        if self.info:
            self.chooser.load_uri(self.info["url"] + "?splash=done&native=1")
            self.views.set_visible_child_name("chooser")

    def present_flight(self):
        try:
            presentation = json.loads((self.temp / "target.json").read_text())
            generation = presentation["generation"]
            if generation <= self.generation:
                return
            self.generation = generation
            if not (presentation["reuse"] and self.flight_url == presentation["url"]):
                self.web.load_uri(presentation["navigation"])
            self.flight_url = presentation["url"]
            self.views.set_visible_child_name("flight")
        except (OSError, ValueError, KeyError):
            pass  # The engine writes this file atomically when a connection succeeds.

    def selected(self):
        try:
            return json.loads((self.temp / "target.json").read_text())["url"]
        except (OSError, ValueError, KeyError):
            return None

    def allowed(self, url):
        target = self.selected()
        return (target and url.startswith(f"blob:{target}/")) or (origin(url) is not None and origin(url) in {
            origin(target), origin(self.info["url"]) if self.info else None,
        })

    def navigate(self, view, decision, kind):
        if kind == WebKit2.PolicyDecisionType.RESPONSE:
            url = decision.get_request().get_uri()
            if self.info and not self.allowed(url):
                decision.ignore()
            else:
                headers = decision.get_response().get_http_headers()
                disposition = headers.get_one("Content-Disposition") if headers else None
                if (disposition or "").lower().startswith("attachment") or not decision.is_mime_type_supported():
                    decision.download()
                else:
                    decision.use()
            return True
        action = decision.get_navigation_action()
        url = action.get_request().get_uri()
        if self.allowed(url) and kind != WebKit2.PolicyDecisionType.NEW_WINDOW_ACTION or not self.info and url == "about:blank":
            decision.use()
        else:
            decision.ignore()
            if self.allowed(url) and url.startswith("blob:"):
                view.download_uri(url)
            elif action.get_navigation_type() == WebKit2.NavigationType.LINK_CLICKED and (url.startswith("https://") or self.allowed(url)):
                webbrowser.open(url)
        return True

    def load_failed(self, _, event, url, error):
        if self.allowed(url):
            self.error("Could not open the flight page. Check the server or VPN.\n" + error.message)
        return True

    def on_download(self, _, download):
        if not self.allowed(download.get_request().get_uri()):
            download.cancel()
            return
        download.connect("decide-destination", self.destination)
        if self.smoke:
            download.connect("finished", self.saved)

    def destination(self, download, filename):
        if self.smoke:
            target = self.root / "Downloaded model box.nc"
        else:
            dialog = Gtk.FileChooserDialog(title="Save BalloonPredictor export", transient_for=self.window,
                                           action=Gtk.FileChooserAction.SAVE)
            dialog.add_buttons("Cancel", Gtk.ResponseType.CANCEL, "Save", Gtk.ResponseType.ACCEPT)
            dialog.set_do_overwrite_confirmation(True)
            dialog.set_current_name(Path(filename).name or "BalloonPredictor export")
            result, chosen = dialog.run(), dialog.get_filename()
            dialog.destroy()
            if result != Gtk.ResponseType.ACCEPT or not chosen:
                download.cancel()
                return True
            target = Path(chosen)
            download.set_allow_overwrite(True)
        download.set_destination(Gio.File.new_for_path(str(target)).get_uri())
        return True

    def saved(self, *_):
        with (self.root / "Downloaded model box.nc").open("rb") as source:
            self.passed = source.read(8) == b"\x89HDF\r\n\x1a\n"
        if self.passed:
            (self.root / "smoke-result.json").write_text(json.dumps({"engine": "GRIB, GeoTIFF and both NetCDF exports passed",
                "window": "Local chooser, prediction and retained flight after Server/Return in GTK WebKit passed", "download": "NetCDF download passed"}) + "\n")
        Gtk.main_quit()

    def poll(self):
        if self.engine.poll() is not None or (not self.info or self.smoke) and time.monotonic() > self.deadline:
            self.error(f"BalloonPredictor did not finish its startup or test.\nLog: {self.root / 'BalloonPredictor.log'}")
            Gtk.main_quit()
            return False
        ready = self.temp / "ready.json"
        if not self.info and ready.is_file():
            self.info = json.loads(ready.read_text())
            self.show_chooser()
        if self.info:
            self.present_flight()
        if self.info and self.smoke and not self.downloading:
            self.check_window()
        return True

    def check_window(self):
        if self.probing:
            return
        self.probing = True
        view = self.chooser if self.views.get_visible_child_name() == "chooser" else self.web
        script = """(() => {
            const form=document.querySelector('#connection');
            if(form){
                if(window.__bpReturnTest){document.querySelector('#resume')?.click();return false;}
                if(form.hidden)document.querySelector('#choose-local')?.click();
                if(!window.__bpLocal&&form.dataset.ready==='true'&&!document.querySelector('#connect').disabled){
                    const mode=document.querySelector('#mode');mode.value='local';
                    mode.dispatchEvent(new Event('change'));form.requestSubmit();window.__bpLocal=true;
                }
                return false;
            }
            const button=document.querySelector('#predict-button');
            if(!window.L||button?.disabled!==false||document.querySelector('#dataset')?.value!=='demo')return false;
            if(!window.__bpFlight){
                window.__bpFlight=true;document.querySelector('#flight_mode').value='ascent';
                document.querySelector('#burst_altitude').value='1000';document.querySelector('#ascent_rate').value='5';button.click();
            }
            return document.querySelector('#launch_datetime-date')?.type==='text' && document.querySelector('#launch_datetime-time')?.type==='text' && navigator.userAgent.includes('BalloonPredictor/') && document.querySelector('#flight-status').textContent.startsWith('Flight complete');
        })()"""
        if self.roundtrip:
            script = script.replace("return navigator.userAgent", "return window.__bpRetainedFlight===true && navigator.userAgent")
        def finished(view, result, _):
            self.probing = False
            try:
                ready = view.run_javascript_finish(result).get_js_value().to_boolean()
            except GLib.Error:
                return
            if ready and self.roundtrip == 0:
                self.roundtrip = 1
                self.web.run_javascript("window.__bpRetainedFlight=true", None, None, None)
                self.show_chooser()
                self.chooser.connect("load-changed", self.resume_test)
                return
            if ready and view is self.web and self.views.get_visible_child_name() == "flight" and not self.downloading:
                if self.roundtrip != 2:
                    return
                self.downloading = True
                view.download_uri(self.selected() + self.info["download"])
        view.run_javascript(script, None, finished, None)

    def resume_test(self, view, event):
        if event == WebKit2.LoadEvent.FINISHED and self.roundtrip == 1:
            view.run_javascript("window.__bpReturnTest=true", None, None, None)
            self.roundtrip = 2

    def error(self, text):
        if self.smoke:
            print(text, flush=True)
            return
        dialog = Gtk.MessageDialog(transient_for=self.window, modal=True, message_type=Gtk.MessageType.ERROR,
                                   buttons=Gtk.ButtonsType.CLOSE, text=text)
        dialog.run()
        dialog.destroy()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--smoke-test", type=Path)
    args = parser.parse_args()
    shared = os.environ.get("BP_SHARED_DATA_ROOT")
    default = personal_data_dir(shared or SHARED_DATA_ROOT) if shared or SHARED_DATA_ROOT.is_dir() else Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share") / "balloon-predictor"
    root = (args.smoke_test or default).resolve()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.TemporaryDirectory(prefix=".balloon-session-", dir=root) as temp:
        app = App(root, Path(temp), args.smoke_test is not None)
        try:
            Gtk.main()
        finally:
            app.shutdown()
        return 0 if args.smoke_test is None or app.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
