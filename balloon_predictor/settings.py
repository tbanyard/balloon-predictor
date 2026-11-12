import os
import sys
from tempfile import NamedTemporaryFile
from dataclasses import dataclass
from pathlib import Path

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]


def user_data_dir():
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData/Local") / "BalloonPredictor/data"
    if sys.platform == "darwin":
        return Path.home() / "Library/Application Support/BalloonPredictor/data"
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share") / "balloon-predictor"


def configured_data(path):
    return (yaml.safe_load(path.read_text(encoding="utf-8")) or {}) if path and path.is_file() else {}


def save_data_location(settings, value):
    from .errors import PredictorError

    temporary = None
    try:
        target = Path(value).expanduser()
        if not target.is_absolute():
            raise PredictorError("Enter an absolute folder path, or a path starting with ~/.")
        target = target.resolve()
        target.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(dir=target, prefix=".balloon-write-check-"):
            pass
        config = configured_data(settings.config_path)
        config["data_dir"] = str(target)
        with NamedTemporaryFile(mode="w", encoding="utf-8", dir=settings.config_path.parent,
                                prefix=".balloon-config-", delete=False) as output:
            temporary = Path(output.name)
            yaml.safe_dump(config, output, sort_keys=False, allow_unicode=True)
        temporary.replace(settings.config_path)
        return target
    except (OSError, ValueError, RuntimeError, yaml.YAMLError) as error:
        raise PredictorError("Could not save that location. Check the folder path and write permissions.") from error
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)


@dataclass
class Settings:
    data_dir: Path
    workers: int = 2
    max_weather_mb: int = 2048
    config_path: Path | None = None
    era5_root: Path | None = None
    wrf_root: Path | None = None
    cache_mb: int = 256
    max_time_gap_hours: float = 6

    terrain_dir: Path | None = None
    max_export_mb: int = 4096
    default_dataset: str = "era5"

    def __post_init__(self):
        if self.era5_root is None:
            legacy = Path("/data3/ERA5")
            self.era5_root = legacy if legacy.is_dir() else self.data_dir / "era5"
        if self.wrf_root is None:
            self.wrf_root = self.data_dir / "wrf"

    @property
    def log_path(self):
        return self.data_dir / "logs" / "balloon-predictor.log"

    @classmethod
    def load(cls, path=None):
        load_dotenv(ROOT / ".env", override=False)
        config_path = Path(path or os.environ.get("BP_CONFIG", ROOT / "config.yml")).expanduser().resolve()
        config = configured_data(config_path)
        data_dir = Path(os.environ.get("BP_DATA_DIR", config.get("data_dir", user_data_dir()))).expanduser()
        if not data_dir.is_absolute():
            data_dir = config_path.parent / data_dir
        def root(key, env, default=None):
            raw = os.environ.get(env, config.get(key, default))
            if raw is None:
                return None
            value = Path(raw).expanduser()
            return (value if value.is_absolute() else config_path.parent / value).resolve()
        return cls(data_dir.resolve(), int(config.get("workers", 2)),
                   int(config.get("max_weather_mb", 2048)), config_path,
                   root("era5_root", "BP_ERA5_ROOT"),
                   root("wrf_root", "BP_WRF_ROOT"),
                   max(16, int(os.environ.get("BP_CACHE_MB",config.get("cache_mb", 256)))),
                   max(0.1, float(config.get("max_time_gap_hours", 6))),
                   root("terrain_dir", "BP_TERRAIN_DIR", str(data_dir / "terrain")),
                   int(config.get("max_export_mb", 4096)),
                   str(config.get("default_dataset", "era5")))

    def prepare(self):
        for name in ("raw", "cache", "weather", "results", "exports", "locks", "terrain", "logs"):
            (self.data_dir / name).mkdir(parents=True, exist_ok=True)

    def definitions(self):
        if not self.config_path or not self.config_path.exists():
            return []
        with self.config_path.open(encoding="utf-8") as f:
            config = yaml.safe_load(f) or {}
        return config.get("datasets", [])
