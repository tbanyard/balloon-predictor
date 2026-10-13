import argparse
import glob
import json
from pathlib import Path
import sys

import numpy as np
import yaml

from .errors import PredictorError
from .exports import csv_export, geojson, kml_export
from .models import BatchPredictionRequest, DownloadRequest, PredictionRequest
from .predictor import predict, predict_batch
from .settings import Settings
from .store import NETCDF_LOCK, WeatherStore, atomic_json
from .weather.download import download_weather
from .weather.readers import read_pressure_files
from .weather.wrf import read_wrf


def expand_files(patterns, base=None):
    paths = []
    for pattern in patterns:
        if base and not Path(pattern).expanduser().is_absolute():
            pattern = str(Path(base) / pattern)
        matches = sorted(glob.glob(str(Path(pattern).expanduser())))
        if not matches:
            raise PredictorError(f"No files match: {pattern}")
        paths.extend(Path(match).resolve() for match in matches)
    return sorted(set(paths))


def np_time(value):
    if not value:
        return None
    from datetime import datetime, timezone
    stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if stamp.tzinfo is None:
        raise PredictorError("Times require Z or a UTC offset.")
    return np.datetime64(stamp.astimezone(timezone.utc).replace(tzinfo=None), "ns")


def import_files(store, name, kind, files, area=None, start=None, end=None, label=None, overwrite=False):
    attrs = {"label": label or name, "source": kind}
    with NETCDF_LOCK:
        reader = read_wrf if kind == "wrf" else read_pressure_files
        ds = reader(files, area=area, start=np_time(start), end=np_time(end), attrs=attrs)
        try:
            return store.save(name, ds, overwrite)
        finally:
            ds.close()


def build_parser():
    parser = argparse.ArgumentParser(description="Balloon Predictor — local ERA5 / ERA5T / IFS / WRF")
    parser.add_argument("--config", help="Configuration YAML (default config.yml beside the project)")
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve", help="Start the local map and API")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--reload", action="store_true")
    serve.add_argument("--config",dest="serve_config",help="Personal configuration YAML")
    serve.add_argument("--era5-root",help="Local ERA5 viewer archive directory")
    serve.add_argument("--wrf-root",help="Native wrfout directory")
    serve.add_argument("--cache-mb",type=int,help="Decoded native-column cache budget")
    sub.add_parser("list", help="List locally available weather")
    inspect = sub.add_parser("inspect", help="Show coverage and provenance")
    inspect.add_argument("dataset")
    download = sub.add_parser("download", help="Download weather using a YAML or JSON request")
    download.add_argument("request_file")
    download.add_argument("--overwrite", action="store_true")
    for command, kind in (("import-wrf", "wrf"), ("import-era5", "era5"), ("import-ifs", "ifs")):
        imp = sub.add_parser(command, help=f"Import local {kind} weather files")
        imp.add_argument("--name", required=True)
        imp.add_argument("--label")
        imp.add_argument("--files", nargs="+", required=True, help="Paths or quoted glob patterns")
        imp.add_argument("--area", nargs=4, type=float, metavar=("NORTH", "WEST", "SOUTH", "EAST"))
        imp.add_argument("--start", help="Inclusive UTC start; retain bracketing timestamps")
        imp.add_argument("--end", help="Inclusive UTC end; retain bracketing timestamps")
        imp.add_argument("--overwrite", action="store_true")
        imp.set_defaults(kind=kind)
    config_import = sub.add_parser("import-config", help="Import enabled definitions in config.yml")
    config_import.add_argument("--overwrite", action="store_true")
    run = sub.add_parser("predict", help="Calculate and export a flight without the browser")
    run.add_argument("request_file")
    run.add_argument("--output", default="data/results/flight", help="Filename prefix for JSON/CSV/GeoJSON/KML")
    series=sub.add_parser("predict-series",help="Calculate a repeated launch series from YAML/JSON")
    series.add_argument("request_file")
    series.add_argument("--output",default="data/results/series",help="Filename prefix for JSON/CSV/GeoJSON/KML")
    return parser


def load_request(path):
    with Path(path).open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def main(argv=None):
    args = build_parser().parse_args(argv)
    settings = Settings.load(getattr(args,"serve_config",None) or args.config)
    if args.command=="serve":
        import os
        for key,env in (("era5_root","BP_ERA5_ROOT"),("wrf_root","BP_WRF_ROOT")):
            value=getattr(args,key)
            if value:
                value=Path(value).expanduser().resolve()
                setattr(settings,key,value)
                os.environ[env]=str(value)
        if args.cache_mb:
            settings.cache_mb=max(16,args.cache_mb)
            os.environ["BP_CACHE_MB"]=str(settings.cache_mb)
    store = WeatherStore(settings)
    try:
        if args.command == "serve":
            import os
            import uvicorn
            if settings.config_path:
                os.environ["BP_CONFIG"] = str(settings.config_path)
            # A single process keeps weather caches/jobs consistent.
            uvicorn.run("balloon_predictor.api:app", host=args.host, port=args.port, reload=args.reload)
        elif args.command == "list":
            for info in store.list():
                print(f"{info['id']:24s} {info['source']:7s} {info['start'] or 'any time'} → {info['end'] or 'any time'}")
        elif args.command == "inspect":
            print(json.dumps(store.get(args.dataset).info, indent=2))
        elif args.command == "download":
            body = DownloadRequest.model_validate(load_request(args.request_file))
            if store.exists(body.name) and not args.overwrite:
                raise PredictorError("Dataset already exists. Choose another name or pass --overwrite.")
            ds = download_weather(body, settings, lambda message: print(message, flush=True))
            try:
                print(json.dumps(store.save(body.name, ds, args.overwrite), indent=2))
            finally:
                ds.close()
        elif args.command.startswith("import-") and args.command != "import-config":
            files = expand_files(args.files)
            print(json.dumps(import_files(store, args.name, args.kind, files, args.area, args.start, args.end,
                                          args.label, args.overwrite), indent=2))
        elif args.command == "import-config":
            imported = 0
            for definition in settings.definitions():
                if not definition.get("enabled", True):
                    continue
                kind = definition["source"]
                if kind not in ("wrf", "era5", "era5t", "ifs"):
                    raise PredictorError("Configured imports must use wrf, era5, era5t or ifs.")
                files = expand_files(definition["files"], settings.config_path.parent)
                print(json.dumps(import_files(store, definition["name"], kind, files, definition.get("area"),
                                              definition.get("start"), definition.get("end"), definition.get("label"),
                                              args.overwrite), indent=2))
                imported += 1
            if not imported:
                print("No enabled dataset definitions. Edit config.yml first.")
        elif args.command in ("predict","predict-series"):
            progress=lambda message:print(message,flush=True)
            if args.command=="predict-series":
                body=BatchPredictionRequest.model_validate(load_request(args.request_file))
                result=predict_batch(body,store,progress)
            else:
                body = PredictionRequest.model_validate(load_request(args.request_file))
                result = predict(body, store.get(body.dataset,body.launch_datetime), progress)
            path = Path(args.output).expanduser().resolve()
            path.parent.mkdir(parents=True, exist_ok=True)
            atomic_json(Path(str(path) + ".json"), result)
            atomic_json(Path(str(path) + ".geojson"), geojson(result))
            Path(str(path) + ".csv").write_text(csv_export(result), encoding="utf-8", newline="")
            Path(str(path) + ".kml").write_text(kml_export(result), encoding="utf-8")
            print(json.dumps({k:result[k] for k in ("completed","failed","total")} if result.get("type")=="series" else result["summary"], indent=2))
            print(f"Saved {path}.json/.csv/.geojson/.kml")
        return 0
    except (PredictorError, ValueError, KeyError, OSError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
