import sys
from types import ModuleType, SimpleNamespace

from desktop import launcher


def test_windows_splash_hands_off_to_the_main_window(monkeypatch, tmp_path):
    """Exercise window and menu creation without importing native Windows libraries."""
    windows = []
    events = []
    webview = ModuleType("webview")
    webview.settings = {}
    webview.guilib = ModuleType("webview.guilib")
    webview.guilib.initialize = lambda name: SimpleNamespace(renderer=name)
    webview.menu = ModuleType("webview.menu")
    webview.menu.Menu = lambda name, actions: SimpleNamespace(name=name, actions=actions)
    webview.menu.MenuAction = lambda name, action: SimpleNamespace(name=name, action=action)
    for module in (webview, webview.guilib, webview.menu):
        monkeypatch.setitem(sys.modules, module.__name__, module)

    def create(title, url, **options):
        def wait(seconds):
            events.append((title, "loaded"))
            return True

        window = SimpleNamespace(
            title=title,
            url=url,
            options=options,
            events=SimpleNamespace(loaded=SimpleNamespace(wait=wait)),
            show=lambda: events.append((title, "show")),
            destroy=lambda: events.append((title, "destroy")),
            load_url=lambda url: events.append((title, url)),
        )
        windows.append(window)
        return window

    webview.create_window = create
    webview.start = lambda start_window, **options: start_window()
    monkeypatch.setattr(launcher, "time", SimpleNamespace(sleep=lambda seconds: events.append(("sleep", seconds))))
    url = "http://127.0.0.1:49302/private/"
    launcher.open_window(url, tmp_path)

    main, splash = windows
    assert main.options["hidden"] and splash.options["transparent"]
    assert main.url == url + "?splash=done" and splash.url == url + "splash.html"
    assert events == [(splash.title, "loaded"), ("sleep", 2), (main.title, "show"), (splash.title, "destroy")]
    main.options["menu"][0].actions[0].action()
    assert events[-1] == (main.title, main.url)
