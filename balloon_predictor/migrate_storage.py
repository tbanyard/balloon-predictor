"""Copy the current operator's old data, then write a shared-server profile."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import sys
import uuid

import yaml

from .disk import SHARED_DATA_ROOT, check_writable, os_account, personal_data_dir
from .errors import PredictorError
from .settings import configured_data


def digest(path):
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def rebase(value, source, target):
    if isinstance(value, dict):
        return {key: rebase(item, source, target) for key, item in value.items()}
    if isinstance(value, list):
        return [rebase(item, source, target) for item in value]
    if isinstance(value, str) and (value == str(source) or value.startswith(str(source) + "/")):
        return str(target) + value[len(str(source)):]
    return value


def copy_data(source, target, progress=print):
    if source == target or not source.is_dir():
        target.mkdir(parents=True, exist_ok=True, mode=0o700)
        return {"source": str(source), "files": 0, "bytes": 0}
    if source.is_relative_to(target) or target.is_relative_to(source):
        raise PredictorError("The old and new data folders must not contain each other.")
    marker = target / ".migration.json"
    if marker.is_file() and json.loads(marker.read_text()).get("source") == str(source):
        progress("This source was already copied. Keeping the existing workspace.")
        return json.loads(marker.read_text())
    if target.exists() and any(target.iterdir()):
        raise PredictorError(f"The destination already contains data: {target}. Nothing was overwritten. Use --fresh to keep those files and start without copying.")
    files = []
    for path in source.rglob("*"):
        relative = path.relative_to(source)
        if "locks" in relative.parts or path.name.endswith((".part", ".tmp", ".lock", ".lease")):
            continue
        if path.is_symlink():
            raise PredictorError(f"Migration stopped at symbolic link: {path}. Move or copy its contents explicitly before migrating.")
        if path.is_file():
            files.append(path)
    total = sum(path.stat().st_size for path in files)
    if shutil.disk_usage(target.parent).free < total + 32 * 1024**2:
        raise PredictorError(f"Not enough free space to copy {total / 1024**3:.2f} GiB into {target.parent}.")
    stage = target.parent / (".migration-" + uuid.uuid4().hex)
    stage.mkdir(mode=0o700)
    try:
        for index, path in enumerate(files, 1):
            relative = path.relative_to(source)
            output = stage / relative
            output.parent.mkdir(parents=True, exist_ok=True)
            before = path.stat()
            shutil.copy2(path, output)
            # Verify even large GRIB/NetCDF/GeoTIFF files before switching paths.
            if digest(path) != digest(output) or (path.stat().st_size, path.stat().st_mtime_ns) != (before.st_size, before.st_mtime_ns):
                raise PredictorError(f"Source changed or copying failed: {path.name}. Stop the old server before retrying.")
            if path.suffix == ".json":
                try:
                    data = json.loads(output.read_text())
                except (ValueError, UnicodeError):
                    pass
                else:
                    changed = rebase(data, source, target)
                    if changed != data:
                        output.write_text(json.dumps(changed, indent=2, allow_nan=False), encoding="utf-8")
            if index == len(files) or index % 25 == 0:
                progress(f"Verified {index}/{len(files)} files.")
        report = {"source": str(source), "destination": str(target), "files": len(files), "bytes": total}
        (stage / ".migration.json").write_text(json.dumps(report, indent=2))
        if target.exists():
            target.rmdir()  # Only an empty target can reach this point.
        stage.rename(target)
        return report
    finally:
        if stage.exists():
            shutil.rmtree(stage)


def prepare(shared_root, source, old_config, port=8011, *, fresh=False, progress=print):
    root = Path(shared_root).expanduser().resolve()
    if root.is_symlink():
        raise PredictorError("The shared storage root must not be a symbolic link.")
    root.mkdir(parents=True, exist_ok=True)
    # The sticky bit preserves world-write access while protecting each
    # account/service directory from being renamed by another Unix user.
    if os.name == "posix" and root.stat().st_uid == os.geteuid():
        root.chmod(stat.S_IMODE(root.stat().st_mode) | stat.S_ISVTX)
    target = personal_data_dir(root)
    profile = root / f".server-{port}"
    if profile.is_symlink() or (profile.exists() and profile.stat().st_uid != os.geteuid()):
        raise PredictorError(f"The server profile is not owned by this Unix account: {profile}")
    profile.mkdir(mode=0o755, exist_ok=True)
    check_writable(root)
    source = Path(source).expanduser().resolve()
    report = copy_data(source, target, progress) if not fresh else {"files": 0, "bytes": 0}
    target.mkdir(mode=0o700, exist_ok=True)
    config = configured_data(old_config)
    for key in ("era5_root", "wrf_root", "terrain_dir"):
        value = config.get(key)
        if value:
            absolute = (old_config.parent / Path(value).expanduser()).resolve()
            config[key] = rebase(str(absolute), source, target)
    for definition in config.get("datasets", []):
        definition["files"] = [rebase(str(old_config.parent / item) if not Path(item).is_absolute() else item, source, target)
                               for item in definition.get("files", [])]
    config.update(data_dir=str(target), shared_server=True, shared_data_root=str(root))
    config.setdefault("cache_mb", 128)
    config_path = profile / "config.yml"
    if config_path.exists():
        # A repeat must retain the operator's later edits.
        existing = configured_data(config_path)
        if existing.get("shared_server") is not True or Path(existing.get("shared_data_root", "")).resolve() != root:
            raise PredictorError(f"A different server configuration already exists: {config_path}")
    else:
        descriptor = os.open(config_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            yaml.safe_dump(config, output, sort_keys=False)
    progress(f"Personal data: {target}\nServer config: {config_path}\nOld files retained: {source}")
    return config_path, report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--shared-root", type=Path, default=SHARED_DATA_ROOT)
    parser.add_argument("--source", type=Path, help="Old data folder; defaults to the old config's data_dir or ~/.local/share/balloon-predictor.")
    parser.add_argument("--config", type=Path, default=Path(__file__).resolve().parents[1] / "config.yml")
    parser.add_argument("--port", type=int, default=8011)
    parser.add_argument("--fresh", action="store_true", help="Keep the destination; do not copy the old data.")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")
    try:
        config_path = args.config.expanduser().resolve()
        config = configured_data(config_path)
        old_data = args.source or os.environ.get("BP_DATA_DIR") or config.get("data_dir")
        source = (config_path.parent / Path(old_data).expanduser()).resolve() if old_data else Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share") / "balloon-predictor"
        prepare(args.shared_root, source, config_path, args.port, fresh=args.fresh)
    except (OSError, PredictorError) as error:
        print(f"Stopped: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
