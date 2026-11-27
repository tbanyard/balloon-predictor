import sys
import threading
from types import ModuleType, SimpleNamespace

import pytest

from desktop import launcher
from desktop.connection import Chooser, read_json


@pytest.mark.parametrize('reuse,expected_navigations', [(True, 1), (False, 2)])
def test_windows_connection_retains_the_flight_view(monkeypatch, tmp_path, reuse, expected_navigations):
    """Exercise actual shell handoff/menu logic with native WebView2 replaced."""
    windows, events = [], []
    webview = ModuleType('webview')
    webview.settings = {}
    webview.guilib = ModuleType('webview.guilib')
    webview.guilib.initialize = lambda name: SimpleNamespace(renderer=name)
    webview.menu = ModuleType('webview.menu')
    webview.menu.Menu = lambda name, actions: SimpleNamespace(name=name, actions=actions)
    webview.menu.MenuAction = lambda name, action: SimpleNamespace(name=name, action=action)
    system = ModuleType('System')
    system.Action = lambda callback: callback
    monkeypatch.setitem(sys.modules, 'System', system)
    for module in (webview, webview.guilib, webview.menu):
        monkeypatch.setitem(sys.modules, module.__name__, module)
    url = 'http://127.0.0.1:49302/private/'
    flight = 'http://127.0.0.1:49303'
    chooser = SimpleNamespace(url=url, lock=threading.RLock(), presentation={
        'generation': 1, 'url': flight, 'navigation': flight+'/signin?ticket=example', 'reuse': False})

    class Event:
        def __init__(self):
            self.handlers, self.calls = [], 0
        def __iadd__(self, handler):
            self.handlers.append(handler)
            return self
        def is_set(self):
            return True
        def wait(self, seconds):
            self.calls += 1
            if self is windows[0].events.closed:
                if self.calls == 2:
                    windows[0].options['menu'][0].actions[0].action()
                    chooser.presentation = {'generation': 2, 'url': flight, 'navigation': flight, 'reuse': reuse}
                return self.calls > 2
            # Reproduce Edge's initial empty page: navigating to the same
            # about:blank document need not emit NavigationCompleted/loaded.
            if self is windows[0].events.loaded:
                return windows[0].url != 'about:blank'
            return True

    def create(title, url, **options):
        window = SimpleNamespace(
            title=title, url=url, options=options,
            events=SimpleNamespace(loaded=Event(), closing=Event(), closed=Event()),
            show=lambda: events.append((title, 'show')),
            hide=lambda: events.append((title, 'hide')),
            destroy=lambda: events.append((title, 'destroy')),
            load_url=lambda url: events.append((title, 'navigate', url)),
            evaluate_js=lambda script: events.append((title, 'evaluate', script)) or True,
            native=SimpleNamespace(Invoke=lambda callback: callback(), webview=SimpleNamespace(
                CoreWebView2=SimpleNamespace(Settings=SimpleNamespace(UserAgent='Actual Edge engine')))),
        )
        windows.append(window)
        return window

    webview.create_window = create
    webview.start = lambda start_window, **options: start_window()
    monkeypatch.setattr(launcher, 'time', SimpleNamespace(sleep=lambda seconds: events.append(('sleep', seconds))))
    launcher.open_window(chooser, tmp_path)
    main, connection, splash = windows
    assert main.url == url+'splash.html' and connection.url == url+'?splash=done&native=1'
    assert splash.options['transparent'] and ('sleep', 2) in events
    navigations = [e for e in events if e[:2] == (main.title, 'navigate')]
    assert len(navigations) == expected_navigations
    assert navigations[0][2] == flight+'/signin?ticket=example'
    assert not any(e == (main.title, 'navigate', connection.url) for e in events)
    assert (main.title, 'hide') in events and (connection.title, 'hide') in events
    assert not any(e[:2] == (connection.title, 'navigate') for e in events), 'Reopening the chooser must not navigate its retained page.'
    for view in (main, connection):
        assert view.native.webview.CoreWebView2.Settings.UserAgent.startswith('Actual Edge engine BalloonPredictor/1.6.6 ')


def test_resume_publishes_reuse_without_reopening_the_connection(tmp_path):
    target = tmp_path/'target.json'
    chooser = Chooser(tmp_path, target_file=target)
    chooser.activate('http://127.0.0.1:8011', navigation='http://127.0.0.1:8011/signin?ticket=example')
    first = read_json(target)
    assert first['generation'] == 1 and first['reuse'] is False
    assert first['navigation'].endswith('ticket=example')
    chooser.resume({})
    resumed = read_json(target)
    assert resumed == {**first, 'generation': 2, 'navigation': first['url'], 'reuse': True}
    assert chooser.state['target'] == first['url']
    # A deliberate new connection, even to the same URL, does not reuse the old user page.
    chooser.activate(first['url'])
    assert read_json(target)['reuse'] is False
    assert read_json(target)['generation'] == 3


def test_gtk_server_screen_preserves_webview_and_resume_does_not_load_again(monkeypatch, tmp_path):
    import runpy
    from pathlib import Path
    gi = ModuleType('gi')
    gi.require_version = lambda *args: None
    repository = ModuleType('gi.repository')
    for name in ('Gio', 'GLib', 'Gtk', 'WebKit2'):
        setattr(repository, name, SimpleNamespace())
    monkeypatch.setitem(sys.modules, 'gi', gi)
    monkeypatch.setitem(sys.modules, 'gi.repository', repository)
    App = runpy.run_path(str(Path(launcher.__file__).parent/'linux/launcher.py'))['App']
    app = App.__new__(App)
    loads, chooser_loads, shown = [], [], []
    app.info = {'url': 'http://127.0.0.1:40000/private/'}
    app.temp, app.generation, app.flight_url = tmp_path, 0, None
    app.web = SimpleNamespace(load_uri=loads.append)
    resets=[]
    app.smoke=False
    app.chooser = SimpleNamespace(load_uri=chooser_loads.append,get_uri=lambda:chooser_loads[-1] if chooser_loads else None,
                                 run_javascript=lambda script,cancel,callback,data:resets.append((script,callback)))
    app.views = SimpleNamespace(set_visible_child_name=shown.append)
    chooser = Chooser(tmp_path, target_file=tmp_path/'target.json')
    chooser.activate('http://127.0.0.1:8011')
    app.present_flight()
    app.show_chooser()
    assert len(loads) == 1 and chooser_loads[-1].endswith('?splash=done&native=1')
    chooser.resume({})
    app.present_flight()
    app.present_flight()  # Polling the consumed generation must be harmless.
    assert len(loads) == 1 and shown[-1] == 'flight'
    app.show_chooser()
    assert len(chooser_loads)==1 and 'showChoices' in resets[-1][0]
    assert shown[-1]=='flight', 'Old chooser fields were revealed before reset completed.'
    resets[-1][1]()
    assert shown[-1]=='chooser'
    chooser.activate('http://127.0.0.1:8011')
    app.present_flight()
    assert len(loads) == 2, 'An explicit new connection must navigate, even to the same URL.'
