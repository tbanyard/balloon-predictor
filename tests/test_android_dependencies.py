"""Cold/cache builds, registry limits and checksum failures without network traffic."""
from datetime import datetime, timedelta, timezone
from email.message import Message
from email.utils import format_datetime
import hashlib
from io import BytesIO
import http.client
from urllib.error import HTTPError, URLError

import pytest

from android import build

DATA = b'verified dependency fixture'
HASH = hashlib.sha256(DATA).hexdigest()
URL = 'example/package/1.0/package-1.0.jar'


def folders(tmp_path):
    work, cache = tmp_path/'work', tmp_path/'cache'
    work.mkdir()
    cache.mkdir()
    return work, cache


def test_cached_dependency_never_contacts_a_registry(tmp_path, monkeypatch):
    work, cache = folders(tmp_path)
    (cache/(HASH+'.jar')).write_bytes(DATA)
    def offline(*args, **kwargs):
        pytest.fail('A valid cached dependency must not make a network request.')
    monkeypatch.setattr(build, 'urlopen', offline)
    assert build.download_jar('fixture', URL, HASH, work, cache).read_bytes() == DATA


def test_cold_build_uses_the_mirror_then_reuses_verified_bytes(tmp_path, monkeypatch):
    work, cache = folders(tmp_path)
    calls = []
    def download(request, timeout):
        calls.append(request)
        assert timeout == 60
        assert request.get_header('User-agent').startswith('BalloonPredictor/1.6.4 build ')
        return BytesIO(DATA)
    monkeypatch.setattr(build, 'urlopen', download)
    assert build.download_jar('fixture', URL, HASH, work, cache).read_bytes() == DATA
    assert calls[0].host == 'maven-central.storage-download.googleapis.com'
    second = tmp_path/'second'
    second.mkdir()
    assert build.download_jar('fixture', URL, HASH, second, cache).read_bytes() == DATA
    assert len(calls) == 1
    assert list(cache.iterdir()) == [cache/(HASH+'.jar')]


def test_corrupt_cache_is_not_used(tmp_path, monkeypatch):
    work, cache = folders(tmp_path)
    cached = cache/(HASH+'.jar')
    cached.write_bytes(b'corrupt')
    monkeypatch.setattr(build, 'urlopen', lambda *a, **kw: BytesIO(DATA))
    assert build.download_jar('fixture', URL, HASH, work, cache).read_bytes() == DATA
    assert cached.read_bytes() == DATA


def test_rate_limit_honours_retry_after_and_uses_a_backup(tmp_path, monkeypatch):
    work, cache = folders(tmp_path)
    calls, sleeps = [], []
    def limited(request, **kwargs):
        calls.append(request.host)
        if len(calls) == 1:
            headers = Message()
            headers['Retry-After'] = '3'
            raise HTTPError(request.full_url, 429, 'Too many requests', headers, BytesIO())
        return BytesIO(DATA)
    monkeypatch.setattr(build, 'urlopen', limited)
    monkeypatch.setattr(build.time, 'sleep', sleeps.append)
    assert build.download_jar('fixture', URL, HASH, work, cache).read_bytes() == DATA
    assert sleeps == [3]
    assert calls == ['maven-central.storage-download.googleapis.com', 'repo.maven.apache.org']


@pytest.mark.parametrize('kind', ['http503', 'network'])
def test_transient_failures_have_a_bounded_retry_budget(tmp_path, monkeypatch, kind):
    work, cache = folders(tmp_path)
    calls, sleeps = [], []
    def fail(request, **kwargs):
        calls.append(request.full_url)
        if kind == 'http503':
            raise HTTPError(request.full_url, 503, 'Unavailable', {}, BytesIO())
        raise URLError('temporary network failure')
    monkeypatch.setattr(build, 'urlopen', fail)
    monkeypatch.setattr(build.time, 'sleep', sleeps.append)
    with pytest.raises(RuntimeError, match='Rerun the Android workflow later'):
        build.download_jar('fixture', URL, HASH, work, cache)
    assert len(calls) == 3 and sleeps == [20, 40]
    assert not list(cache.iterdir()) and not list(work.iterdir())


def test_a_new_release_missing_from_the_mirror_can_use_central(tmp_path, monkeypatch):
    work, cache = folders(tmp_path)
    def mirror_lag(request, **kwargs):
        if 'storage-download' in request.host:
            raise HTTPError(request.full_url, 404, 'Not mirrored yet', {}, BytesIO())
        return BytesIO(DATA)
    monkeypatch.setattr(build, 'urlopen', mirror_lag)
    monkeypatch.setattr(build.time, 'sleep', lambda _: None)
    assert build.download_jar('fixture', URL, HASH, work, cache).read_bytes() == DATA


def test_a_bad_checksum_is_never_retried_or_cached(tmp_path, monkeypatch):
    work, cache = folders(tmp_path)
    calls = []
    monkeypatch.setattr(build, 'urlopen', lambda *a, **kw: calls.append(a) or BytesIO(b'wrong data'))
    with pytest.raises(RuntimeError, match='Invalid checksum'):
        build.download_jar('fixture', URL, HASH, work, cache)
    assert len(calls) == 1 and not list(cache.iterdir()) and not list(work.iterdir())


def test_long_retry_after_is_respected_without_early_retries(tmp_path, monkeypatch):
    work, cache = folders(tmp_path)
    calls, sleeps = [], []
    def blocked(request, **kwargs):
        calls.append(request.full_url)
        raise HTTPError(request.full_url, 429, 'Blocked', {'Retry-After': '120'}, BytesIO())
    monkeypatch.setattr(build, 'urlopen', blocked)
    monkeypatch.setattr(build.time, 'sleep', sleeps.append)
    with pytest.raises(RuntimeError, match='longer wait'):
        build.download_jar('fixture', URL, HASH, work, cache)
    assert len(calls) == 1 and sleeps == []


def test_interrupted_download_is_cleaned_before_retry(tmp_path, monkeypatch):
    work, cache = folders(tmp_path)
    class Interrupted(BytesIO):
        def read(self, size=-1):
            if self.tell():
                raise http.client.IncompleteRead(b'partial')
            return super().read(size)
    calls = []
    def download(*args, **kwargs):
        calls.append(1)
        return Interrupted(b'partial') if len(calls) == 1 else BytesIO(DATA)
    monkeypatch.setattr(build, 'urlopen', download)
    monkeypatch.setattr(build.time, 'sleep', lambda _: None)
    assert build.download_jar('fixture', URL, HASH, work, cache).read_bytes() == DATA
    assert not list(work.glob('*.part')) and (cache/(HASH+'.jar')).read_bytes() == DATA


def test_retry_after_accepts_http_dates_and_invalid_headers():
    future = format_datetime(datetime.now(timezone.utc)+timedelta(seconds=30))
    assert 25 < build.retry_delay({'Retry-After': future}, 0) <= 30
    past = format_datetime(datetime.now(timezone.utc)-timedelta(seconds=30))
    assert build.retry_delay({'Retry-After': past}, 0) == 1
    assert build.retry_delay({'Retry-After': 'invalid'}, 1) == 40


def test_authorisation_error_is_not_treated_as_a_transient_failure(tmp_path, monkeypatch):
    work, cache = folders(tmp_path)
    def denied(request, **kwargs):
        raise HTTPError(request.full_url, 403, 'Forbidden', {}, BytesIO())
    monkeypatch.setattr(build, 'urlopen', denied)
    with pytest.raises(HTTPError) as error:
        build.download_jar('fixture', URL, HASH, work, cache)
    assert error.value.code == 403 and not list(work.iterdir())
