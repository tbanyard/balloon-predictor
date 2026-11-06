"""Cheap source receipts detect replacement before re-sampling a saved flight."""
from pathlib import Path
from .errors import PredictorError


def receipt(path):
    path = Path(path)
    stat = path.stat()
    return {"path": str(path.resolve()), "size": stat.st_size, "mtime_ns": stat.st_mtime_ns}


def record_file(weather, path):
    entry = receipt(path)
    receipts = getattr(weather, "source_receipts", None)
    if receipts is None:
        weather.source_receipts = receipts = {}
    previous = receipts.setdefault(entry["path"], entry)
    if previous != entry:
        raise PredictorError(f"Source changed during processing: {Path(path).name}. Rescan and run again.")
    return entry


def snapshot(weather):
    paths = []
    if hasattr(weather, "source_path"):
        paths.append(Path(weather.source_path))
    elif hasattr(weather, "archive"):
        used = weather.info.get("provenance", {}).get("files", [])
        paths.extend(weather.archive.root / p for p in used)
        topo = weather.archive.root / "era5_topo.nc"
        if topo.exists():
            paths.append(topo)
    elif hasattr(weather, "records"):
        paths.extend(Path(r[1]) for r in weather.records)
    catalog = getattr(weather, "terrain_catalog", None)
    if catalog:
        paths.extend(catalog.root / p for p in weather.info.get("physical_ground", {}).get("tiles", []))
    recorded = getattr(weather, "source_receipts", {})
    return [recorded.get(str(p.resolve()), receipt(p)) for p in sorted(set(paths))]


def validate_manifest(result):
    if "input_manifest" not in result:
        raise PredictorError("This saved prediction predates source verification. Re-run it with v1.3 before exporting sampled data.")
    for entry in result["input_manifest"]:
        path = Path(entry["path"])
        if not path.is_file() or path.stat().st_size != entry["size"] or path.stat().st_mtime_ns != entry["mtime_ns"]:
            raise PredictorError(f"Source file changed or was removed: {path.name}. Re-run the prediction before exporting model data.")
