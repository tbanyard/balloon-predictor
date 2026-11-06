import os
from dataclasses import dataclass
from pathlib import Path

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]


@dataclass
class Settings:
    data_dir: Path
    workers: int = 2
    max_weather_mb: int = 2048
    config_path: Path | None = None
    era5_root: Path = Path("/data3/ERA5")
    wrf_root: Path = Path("/data1/tim/SkySightWRF")
    cache_mb: int = 256
    max_time_gap_hours: float = 6

    terrain_dir: Path | None = None
    max_export_mb: int = 4096

    @classmethod
    def load(cls, path=None):
        load_dotenv(ROOT / ".env", override=False)
        config_path = Path(path or os.environ.get("BP_CONFIG", ROOT / "config.yml")).resolve()
        config = {}
        if config_path.exists():
            with config_path.open(encoding="utf-8") as f:
                config = yaml.safe_load(f) or {}
        data_dir = Path(os.environ.get("BP_DATA_DIR", config.get("data_dir", "data"))).expanduser()
        if not data_dir.is_absolute():
            data_dir = config_path.parent / data_dir
        def root(key, env, default):
            value = Path(os.environ.get(env, config.get(key, default))).expanduser()
            return (value if value.is_absolute() else config_path.parent / value).resolve()
        return cls(data_dir.resolve(), int(config.get("workers", 2)),
                   int(config.get("max_weather_mb", 2048)), config_path,
                   root("era5_root", "BP_ERA5_ROOT", "/data3/ERA5"),
                   root("wrf_root", "BP_WRF_ROOT", "/data1/tim/SkySightWRF"),
                   max(16, int(os.environ.get("BP_CACHE_MB",config.get("cache_mb", 256)))),
                   max(0.1, float(config.get("max_time_gap_hours", 6))),
                   root("terrain_dir", "BP_TERRAIN_DIR", str(data_dir / "terrain")),
                   int(config.get("max_export_mb", 4096)))

    def prepare(self):
        for name in ("raw", "cache", "weather", "results", "exports", "locks", "terrain"):
            (self.data_dir / name).mkdir(parents=True, exist_ok=True)

    def definitions(self):
        if not self.config_path or not self.config_path.exists():
            return []
        with self.config_path.open(encoding="utf-8") as f:
            config = yaml.safe_load(f) or {}
        return config.get("datasets", [])
