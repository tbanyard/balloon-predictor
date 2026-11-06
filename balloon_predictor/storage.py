"""Shared process leases prevent cleanup while model data or exports are in use."""
from contextlib import contextmanager
import json
from pathlib import Path
import re
import uuid
from filelock import FileLock, Timeout
from .errors import PredictorError


@contextmanager
def activity(settings):
    directory = settings.data_dir / "locks"
    path = directory / (uuid.uuid4().hex + ".lease")
    gate = FileLock(str(directory / "storage.lock"), timeout=30)
    with gate:
        lease = FileLock(str(path), timeout=0, thread_local=False)
        lease.acquire()
    try:
        yield
    finally:
        with gate:
            lease.release()
            path.unlink(missing_ok=True)


def result_dependencies(settings):
    dependencies = {}
    for path in (settings.data_dir / "results").glob("*.json"):
        if not re.fullmatch(r"[0-9a-f]{32}\.json", path.name):
            continue
        try:
            value = json.loads(path.read_text())
            results = [e["result"] for e in value.get("flights", []) if e.get("status") == "completed"] if value.get("type") == "series" else [value]
            for name in {r["request"]["dataset"] for r in results}:
                dependencies.setdefault(name, set()).add(path.stem)
        except (ValueError, KeyError, OSError):
            continue
    return dependencies


def inventory(settings, store):
    dependencies = result_dependencies(settings)
    def folder(path):
        files = [p for p in path.rglob("*") if p.is_file() and not p.is_symlink()]
        return {"path": str(path), "bytes": sum(p.stat().st_size for p in files), "files": len(files)}
    data = {name: folder(settings.data_dir / name) for name in ("weather", "cache", "results", "exports")}
    data["data_dir"] = str(settings.data_dir)
    data["terrain"] = folder(store.terrain.root)
    data["datasets"] = [{"id": p.stem, "bytes": p.stat().st_size, "path": str(p),
                          "dependent_predictions": len(dependencies.get(p.stem, []))}
                         for p in sorted((settings.data_dir / "weather").glob("*.nc"))]
    data["notes"] = ["Raw remote cache is shared by downloads. Clearing it keeps saved weather datasets and predictions.",
                     "Removing a weather dataset can prevent later sampled exports. Native ERA5/WRF archives and terrain are never removed here.",
                     "Stop v1.2 or older processes before cleanup; they do not participate in v1.3 storage leases."]
    return data


def cleanup(settings, store, body):
    directory = settings.data_dir / "locks"
    with FileLock(str(directory / "storage.lock"), timeout=30):
        for path in directory.glob("*.lease"):
            lock = FileLock(str(path), timeout=0)
            try:
                lock.acquire()
            except Timeout:
                raise PredictorError("Storage is in use by a prediction, download or export. Wait for active work to finish.")
            else:
                lock.release()
                path.unlink(missing_ok=True)
        if body.target == "dataset":
            from .store import checked_name
            name = checked_name(body.name or "")
            count = len(result_dependencies(settings).get(name, []))
            if count and not body.allow_orphan:
                raise PredictorError(f"{count} saved prediction(s) depend on this dataset. Confirm removal with allow_orphan to continue.")
            paths = [settings.data_dir / "weather" / (name + ext) for ext in (".nc", ".json")]
        elif body.target == "cache":
            paths = [p for p in (settings.data_dir / "cache").iterdir()
                     if p.is_file() and (p.name.endswith((".grib", ".request.json", ".part")))]
        else:
            paths = [p for folder in ("results", "exports") for p in (settings.data_dir / folder).rglob("*")
                     if p.is_file() and not p.is_symlink()]
        count, size = 0, 0
        for path in paths:
            if path.is_file() and not path.is_symlink():
                size += path.stat().st_size
                path.unlink()
                count += 1
        with store.lock:
            store.cache.clear()
        return {"removed_files": count, "removed_bytes": size}


class StorageLeaseMiddleware:
    def __init__(self, app, settings):
        self.app, self.settings = app, settings

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and (scope.get("path", "").startswith("/api/") or scope.get("path") == "/predict") and scope.get("path") != "/api/storage/cleanup":
            with activity(self.settings):
                await self.app(scope, receive, send)
        else:
            await self.app(scope, receive, send)
