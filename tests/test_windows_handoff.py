"""Run the actual Windows check with delayed native page-ready events.

The earlier shell tests did not run --check-window. This fixture models
pywebview's cleared readiness during a navigation and the hidden chooser after
Return, so an out-of-order evaluate_js call fails just as it does in WebView2.
"""
from types import SimpleNamespace

import pytest

from desktop import launcher


class Clock:
    def __init__(self):
        self.now = 0
    def monotonic(self):
        return self.now
    def sleep(self, seconds):
        self.now += seconds


class NativePages:
    def __init__(self, clock, delay=.4, *, return_enabled=True, retain_result=True):
        self.clock, self.delay = clock, delay
        self.return_enabled, self.retain_result = return_enabled, retain_result
        self.current = {'url': None, 'generation': 0, 'closing': False}
        self.connect_at = self.loaded_at = self.completed_at = self.return_at = None
        self.chooser_at = None
        self.snapshot = False
        self.predict_clicks = self.resume_clicks = self.chooser_shows = 0
        self.evaluations = []
        self.window = SimpleNamespace(events=SimpleNamespace(loaded=SimpleNamespace(is_set=self.flight_loaded)),
                                      evaluate_js=self.evaluate_flight)
        self.connection = SimpleNamespace(events=SimpleNamespace(loaded=SimpleNamespace(is_set=self.chooser_loaded)),
                                          evaluate_js=self.evaluate_connection)

    def flight_loaded(self):
        return self.loaded_at is not None and self.clock.now >= self.loaded_at

    def chooser_loaded(self):
        # After Return is consumed, the chooser can be hidden/unready. No more
        # JavaScript may be sent to it during flight verification.
        return self.current['generation'] < 2

    def present(self):
        if self.connect_at is not None and self.current['generation'] == 0 and self.clock.now >= self.connect_at:
            self.current.update(url='http://127.0.0.1:48761', generation=1)
            self.loaded_at = self.clock.now + self.delay
        if self.return_at is not None and self.clock.now >= self.return_at:
            self.current['generation'] = 2
            if not self.retain_result:
                self.snapshot = False

    def show_chooser(self):
        self.chooser_shows += 1
        self.chooser_at = self.clock.now
        # The connection document stays loaded; its controller needs another
        # poll before the Return button becomes enabled.

    def evaluate_connection(self, script):
        if not self.chooser_loaded():
            raise RuntimeError('Main window failed to start: hidden chooser was evaluated after Return')
        self.evaluations.append(('chooser', script))
        if "const form=document.querySelector('#connection')" in script:
            if self.connect_at is None:
                self.connect_at = self.clock.now + self.delay
            return None
        assert "const button=document.querySelector('#resume')" in script
        if not self.return_enabled or self.clock.now < self.chooser_at + self.delay:
            return False
        self.resume_clicks += 1
        self.return_at = self.clock.now + self.delay
        return True

    def evaluate_flight(self, script):
        if not self.flight_loaded():
            raise RuntimeError('Main window failed to start: flight evaluated before its bridge was ready')
        self.evaluations.append(('flight', script))
        if "const button=document.querySelector('#predict-button')" in script:
            if self.completed_at is None:
                self.predict_clicks += 1
                self.completed_at = self.clock.now + self.delay
            return self.clock.now >= self.completed_at
        if script.startswith('window.__bpRetainedFlight='):
            self.snapshot = True
            return None
        assert 'const saved=window.__bpRetainedFlight' in script
        return self.snapshot and self.current['generation'] == 2


@pytest.mark.parametrize('delay', [0, .4, 1.2])
def test_real_windows_check_waits_then_clicks_return_once(monkeypatch, delay):
    clock = Clock()
    pages = NativePages(clock, delay)
    monkeypatch.setattr(launcher, 'time', clock)
    launcher.check_windows_frontend(pages.window, pages.connection, pages.show_chooser,
                                   pages.present, pages.current, timeout=15)
    assert pages.predict_clicks == 1
    assert pages.resume_clicks == 1
    assert pages.chooser_shows == 1
    assert pages.current['generation'] == 2 and pages.snapshot
    # Once Return is sent, the test waits on the controller/main page. It must
    # not repeatedly click Return while native presentation is asynchronous.
    assert len([script for view, script in pages.evaluations
                if view == 'chooser' and "const button=document.querySelector('#resume')" in script]) >= 1


def test_windows_check_does_not_pass_a_disabled_return_button(monkeypatch):
    clock = Clock()
    pages = NativePages(clock, .1, return_enabled=False)
    monkeypatch.setattr(launcher, 'time', clock)
    with pytest.raises(RuntimeError, match='waiting for Return to flight'):
        launcher.check_windows_frontend(pages.window, pages.connection, pages.show_chooser,
                                       pages.present, pages.current, timeout=3)
    assert pages.resume_clicks == 0


def test_windows_check_still_rejects_a_lost_calculated_flight(monkeypatch):
    clock = Clock()
    pages = NativePages(clock, .1, retain_result=False)
    monkeypatch.setattr(launcher, 'time', clock)
    with pytest.raises(RuntimeError, match='lost or replaced the calculated flight'):
        launcher.check_windows_frontend(pages.window, pages.connection, pages.show_chooser,
                                       pages.present, pages.current, timeout=5)
    assert pages.predict_clicks == pages.resume_clicks == 1


def test_windows_check_does_not_suppress_native_page_failures(monkeypatch):
    clock = Clock()
    pages = NativePages(clock)
    def broken(script):
        raise RuntimeError('native bridge failed')
    pages.connection.evaluate_js = broken
    monkeypatch.setattr(launcher, 'time', clock)
    with pytest.raises(RuntimeError, match='native bridge failed'):
        launcher.check_windows_frontend(pages.window, pages.connection, pages.show_chooser,
                                       pages.present, pages.current, timeout=3)
