from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
import json
import logging
from logging.handlers import RotatingFileHandler
import os
import traceback
from pathlib import Path
import threading
import uuid

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask

from . import __version__
from .errors import MissingTerrainError, PredictorError
from .exports import arrival_csv_export, csv_export, geojson, kml_export
from .models import ArrivalPlanRequest, BatchPredictionRequest, DownloadRequest, PredictionRequest, ModelBoxRequest, CleanupRequest, TerrainDownloadRequest, SondeExportRequest
from .storage import activity, inventory, cleanup, StorageLeaseMiddleware
from .context import flight_weather
from .provenance import validate_manifest
from .coordination import plan_arrivals
from .predictor import predict, predict_batch
from .settings import ROOT, Settings, configured_data, save_data_location
from .store import WeatherStore, atomic_json
from .weather.download import download_weather

LOGGER = logging.getLogger(__name__)


class StorageLocation(BaseModel):
    path: str = Field(min_length=1, max_length=4096)


class Jobs:
    def __init__(self, settings, store):
        self.settings, self.store = settings, store
        self.executor = ThreadPoolExecutor(max_workers=settings.workers)
        self.records = {}
        self.lock = threading.RLock()

    def submit(self, kind, task):
        with self.lock:
            if sum(r["status"] in ("queued", "running") for r in self.records.values()) >= 16:
                raise HTTPException(429, "Job queue is full. Wait for a running job to finish.")
            while len(self.records) >= 100:
                completed = next((key for key, value in self.records.items() if value["status"] in ("completed", "failed")), None)
                if completed is None:
                    break
                del self.records[completed]
            lease = activity(self.settings)
            lease.__enter__()
            job_id = uuid.uuid4().hex
            self.records[job_id] = {"id": job_id, "kind": kind, "status": "queued", "message": "Queued"}
        def update(message):
            with self.lock:
                self.records[job_id].update(message=message, stage=message)
        def run():
            with self.lock:
                self.records[job_id]["status"] = "running"
            try:
                result = task(update)
                if kind == "prediction":
                    result["id"] = job_id
                    atomic_json(self.settings.data_dir / "results" / (job_id + ".json"), result)
                if kind == "export":
                    atomic_json(self.settings.data_dir / "exports" / (job_id + ".json"), result)
                outcome = dict(status="completed", message="Complete", result=result)
            except Exception as exc:
                self.log_failure(job_id, kind, exc)
                log = f"Server log: {self.settings.log_path} (job {job_id})."
                message = str(exc) if isinstance(exc, PredictorError) else f"Unexpected processing error during {kind}. {log}"
                if isinstance(exc, PredictorError) and "server log" in message:
                    message += " " + log
                outcome = dict(status="failed", message=message, error=message)
                if isinstance(exc, MissingTerrainError):
                    outcome["error_details"] = exc.details()
            with self.lock:
                # A terminal job may be cleaned up immediately.
                lease.__exit__(None, None, None)
                self.records[job_id].update(outcome)
        try:
            future = self.executor.submit(run)
            future.add_done_callback(lambda f: lease.__exit__(None, None, None) if f.cancelled() else None)
        except Exception:
            lease.__exit__(None, None, None)
            raise
        return {"id": job_id, "status": "queued"}

    def log_failure(self, job_id, kind, error):
        # Log stage, exception type and stack locations without response bodies,
        # exception messages or frame locals (which may include credentials).
        errors = [error] + ([error.__cause__] if error.__cause__ else [])
        trace = " | ".join(f"{type(exc).__name__}: " + " → ".join(
            f"{Path(frame.filename).name}:{frame.lineno} in {frame.name}"
            for frame in traceback.extract_tb(exc.__traceback__)) for exc in errors)
        with self.lock:
            stage = self.records[job_id].get("stage", "starting")
            line = f"Job {job_id} ({kind}, {stage}) failed: {trace}"
            try:
                handler = RotatingFileHandler(self.settings.log_path, maxBytes=2_000_000,
                                              backupCount=2, encoding="utf-8")
                handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
                try:
                    handler.handle(logging.LogRecord(__name__, logging.ERROR, "", 0, line, (), None))
                finally:
                    handler.close()
            except OSError:
                LOGGER.error("Could not write the job log at %s", self.settings.log_path)
            LOGGER.error("%s", line)

    def get(self, job_id):
        with self.lock:
            if job_id in self.records:
                return dict(self.records[job_id])
        if len(job_id) == 32 and all(c in "0123456789abcdef" for c in job_id):
            path = self.settings.data_dir / "results" / (job_id + ".json")
            if path.exists():
                return {"id": job_id, "kind": "prediction", "status": "completed",
                        "message": "Complete", "result": json.loads(path.read_text(encoding="utf-8"))}
            path = self.settings.data_dir / "exports" / (job_id + ".json")
            if path.exists():
                return {"id": job_id, "kind": "export", "status": "completed", "message": "Complete",
                        "result": json.loads(path.read_text(encoding="utf-8"))}
        raise HTTPException(404, "Job not found.")


def create_app(settings=None):
    settings = settings or Settings.load()
    store = WeatherStore(settings)
    jobs = Jobs(settings, store)

    @asynccontextmanager
    async def lifespan(app):
        yield
        jobs.executor.shutdown(wait=False, cancel_futures=True)

    app = FastAPI(title="Balloon Predictor", version=__version__, lifespan=lifespan)
    app.state.store, app.state.jobs = store, jobs
    app.state.edit_storage_location = False
    app.add_middleware(StorageLeaseMiddleware, settings=settings)

    @app.exception_handler(PredictorError)
    async def known_error(request: Request, exc: PredictorError):
        body = {"detail": str(exc)}
        if isinstance(exc, MissingTerrainError):
            body["error_details"] = exc.details()
        return JSONResponse(status_code=422, content=body)

    @app.get("/api/health")
    def health():
        return {"status": "ok", "version": __version__, "datasets": len(store.list()), "time_reference": "UTC"}

    @app.get("/api/weather")
    def weather_list():
        return {"datasets": store.list(), "defaults":{"dataset":settings.default_dataset, "era5_root":str(settings.era5_root),
                    "wrf_root":str(settings.wrf_root),"cache_mb":settings.cache_mb, "data_dir":str(settings.data_dir), "terrain_dir":str(store.terrain.root)},
                "messages":store.native.messages}

    @app.post("/api/weather/rescan", status_code=202)
    def weather_rescan():
        def task(progress):
            progress("Indexing local ERA5 dates and WRF file headers…")
            store.native.scan()
            return {"datasets":store.list(),"messages":store.native.messages}
        return jobs.submit("weather",task)

    @app.get("/api/weather/{name}/times")
    def weather_times(name: str, date: str):
        from datetime import datetime
        from .weather.io import NETCDF_LOCK
        from .weather.era5_native.data import DataError, iso
        try:
            datetime.strptime(date,"%Y-%m-%d")
            with NETCDF_LOCK:
                if name == "era5":
                    return store.native.archive.day(date)
                info = next((i for i in store.native.wrf if i["id"]==name),None)
                if info:
                    from datetime import timezone
                    return {"date":date,"times":[{"datetime":iso(datetime.fromtimestamp(r[0],timezone.utc))}
                            for r in info["records"] if datetime.fromtimestamp(r[0],timezone.utc).date().isoformat()==date]}
                weather=store.get(name)
                return {"date":date,"times":[{"datetime":iso(datetime.fromtimestamp(float(t),timezone.utc))}
                        for t in getattr(weather,"times",[]) if datetime.fromtimestamp(float(t),timezone.utc).date().isoformat()==date]}
        except (ValueError,DataError) as exc:
            raise PredictorError(str(exc)) from exc

    @app.get("/api/weather/{name}/terrain")
    def terrain(name: str, latitude: float = Query(ge=-85, le=85), longitude: float = Query(ge=-180, le=360), when: str | None = None):
        from datetime import datetime
        try:
            stamp=datetime.fromisoformat(when.replace("Z","+00:00")) if when else None
            if stamp and stamp.tzinfo is None:
                raise ValueError("Terrain timestamp requires a timezone.")
        except ValueError as exc:
            raise PredictorError(str(exc)) from exc
        return {"altitude": store.get(name,stamp).terrain(latitude, (longitude + 180) % 360 - 180), "reference": "AMSL"}

    @app.post("/api/predictions", status_code=202)
    def prediction_job(body: PredictionRequest):
        return jobs.submit("prediction", lambda progress: predict(body, store.get(body.dataset,body.launch_datetime), progress))

    @app.post("/api/predictions/series", status_code=202)
    def prediction_series(body: BatchPredictionRequest):
        return jobs.submit("prediction",lambda progress:predict_batch(body,store,progress))

    @app.post("/api/predictions/arrival", status_code=202)
    def prediction_arrival(body: ArrivalPlanRequest):
        return jobs.submit("prediction", lambda progress: plan_arrivals(body, store, progress))

    @app.post("/predict")
    def synchronous_prediction(body: PredictionRequest):
        """Convenient synchronous endpoint for scripts; UI uses jobs."""
        return predict(body, store.get(body.dataset,body.launch_datetime))

    @app.get("/api/v1/")
    def tawhiri_style(launch_datetime: str, launch_latitude: float, launch_longitude: float,
                      launch_altitude: float = 100, ascent_rate: float = 5, burst_altitude: float = 25000,
                      descent_rate: float = 5, dataset: str = "demo", profile: str = "standard_profile"):
        from pydantic import ValidationError
        if profile != "standard_profile":
            raise HTTPException(422, "Only standard_profile is supported by this compatibility endpoint.")
        try:
            body = PredictionRequest(dataset=dataset, launch_datetime=launch_datetime, launch_latitude=launch_latitude,
                                     launch_longitude=launch_longitude, launch_altitude=launch_altitude,
                                     ascent_rate=ascent_rate, burst_altitude=burst_altitude, descent_rate=descent_rate)
        except ValidationError as exc:
            raise HTTPException(422, str(exc)) from exc
        return predict(body, store.get(body.dataset,body.launch_datetime))

    def selected_result(job_id, flight_index):
        job=jobs.get(job_id)
        if job["status"]!="completed" or job["kind"]!="prediction":
            raise HTTPException(409,"Prediction is not complete.")
        result=job["result"]
        if result.get("type")=="series":
            entry=next((e for e in result["flights"] if e["index"]==flight_index),None)
            if entry is None or entry["status"]!="completed":
                raise HTTPException(404,"This launch has no completed prediction.")
            result=entry["result"]
        elif flight_index != 0:
            raise HTTPException(404, "This prediction contains only flight index 0.")
        return result

    @app.get("/api/predictions/{job_id}/sonde.nc")
    def sonde_export(job_id: str, flight_index: int = Query(default=0, ge=0), scope: str = "ascent"):
        from .sonde import write_sonde
        result = selected_result(job_id, flight_index)
        validate_manifest(result)
        body = PredictionRequest.model_validate(result["request"])
        weather = flight_weather(store.get(body.dataset, body.launch_datetime), body)
        path = settings.data_dir / "results" / (uuid.uuid4().hex + ".sonde.nc")
        try:
            write_sonde(path, result, weather, scope)
        except Exception:
            path.unlink(missing_ok=True)
            raise
        return FileResponse(path, media_type="application/x-netcdf",
            filename=f"model-sonde-{body.launch_datetime:%Y%m%dT%H%M%SZ}-{job_id[:8]}-{flight_index}.nc",
            background=BackgroundTask(path.unlink, missing_ok=True))

    @app.get("/api/predictions/{job_id}/sounding")
    def sounding(job_id: str, flight_index: int = Query(default=0,ge=0), mode: str = "ascent"):
        result=selected_result(job_id,flight_index)
        if mode not in ("ascent","launch"):
            raise HTTPException(422,"Sounding mode must be ascent or launch.")
        profile=result.get("sounding")
        if not profile:
            raise HTTPException(422,"Sounding is unavailable for this prediction.")
        if mode=="ascent":
            return profile
        from .profiles import observation
        from .models import PredictionRequest
        request=PredictionRequest.model_validate(result["request"])
        validate_manifest(result)
        weather=flight_weather(store.get(request.dataset,request.launch_datetime), request)
        launch=result["summary"]["launch"]
        rows=[observation(weather,{**launch,"altitude":r["altitude"]}) for r in profile["rows"]]
        return {**profile,"mode":"launch","rows":rows,
                "humidity_available":any(r["dewpoint_c"] is not None for r in rows),
                "description":"Fixed column at the launch location and launch UTC time, up to target/burst altitude."}

    @app.get("/api/predictions/{job_id}/sounding/export/csv")
    def sounding_export(job_id: str, flight_index: int = Query(default=0,ge=0), mode: str = "ascent"):
        import csv
        from io import StringIO
        profile=sounding(job_id,flight_index,mode)
        output=StringIO(newline="")
        keys=["datetime","latitude","longitude","altitude","pressure_hpa","temperature_c","dewpoint_c","u","v","specific_humidity"]
        writer=csv.DictWriter(output,fieldnames=keys,extrasaction="ignore")
        writer.writeheader(); writer.writerows(profile["rows"])
        return Response(output.getvalue(),media_type="text/csv",headers={"Content-Disposition":'attachment; filename="sounding.csv"'})

    @app.post("/api/weather/download", status_code=202)
    def weather_download(body: DownloadRequest):
        if store.exists(body.name):
            raise PredictorError("Dataset name already exists. Choose another name.")
        def task(progress):
            ds = download_weather(body, settings, progress)
            try:
                progress("Saving the processed weather dataset")
                return store.save(body.name, ds)
            finally:
                ds.close()
        return jobs.submit("weather", task)

    @app.get("/api/storage")
    def storage_info():
        info = inventory(settings, store)
        editable = app.state.edit_storage_location and bool(settings.config_path) and "BP_DATA_DIR" not in os.environ
        pending = None
        if editable:
            selected = configured_data(settings.config_path).get("data_dir")
            if selected:
                saved = (settings.config_path.parent / Path(selected).expanduser()).resolve()
                if saved != settings.data_dir:
                    pending = str(saved)
        return {**info, "config_path": str(settings.config_path or ""), "log_path": str(settings.log_path),
                "editable": editable, "pending_data_dir": pending}

    @app.post("/api/storage/location")
    def storage_location(body: StorageLocation):
        if not app.state.edit_storage_location or not settings.config_path or "BP_DATA_DIR" in os.environ:
            raise HTTPException(403, "The server operator can change the location in config.yml or BP_DATA_DIR.")
        with jobs.lock:
            target = save_data_location(settings, body.path.strip())
        return {"data_dir": str(target), "restart_required": target != settings.data_dir}

    @app.post("/api/storage/cleanup")
    def storage_cleanup(body: CleanupRequest):
        with jobs.lock:
            if any(r["status"] in ("queued", "running") for r in jobs.records.values()):
                raise HTTPException(409, "Wait for queued/running predictions, downloads and exports before cleanup.")
            result = cleanup(settings, store, body)
            if body.target == "results":
                jobs.records = {k:v for k,v in jobs.records.items() if v["kind"] not in ("prediction", "export")}
        return result

    @app.get("/api/terrain")
    def physical_terrain():
        return {"path": str(store.terrain.root), "tiles": store.terrain.list(),
                "note": "GeoTIFF elevations must be orthometric metres. GLO-30 is an EGM2008 DSM, including vegetation/buildings. Use a local bare-earth DTM where available."}

    @app.get("/api/terrain/sample")
    def sample_physical_terrain(latitude: float = Query(ge=-85,le=85), longitude: float = Query(ge=-180,le=360), tile: str = "auto"):
        height, used = store.terrain.sample(latitude,longitude,tile)
        return {"altitude": height, "tile": used, "reference": "orthometric metres (tile datum)"}

    @app.post("/api/terrain/download", status_code=202)
    def download_terrain(body: TerrainDownloadRequest):
        def task(progress):
            result = store.terrain.download(body.latitude, body.longitude, progress)
            if body.verify_pixel:
                store.terrain.sample(body.latitude, body.longitude)
            return result
        return jobs.submit("terrain", task)

    @app.post("/api/predictions/{job_id}/sonde-export", status_code=202)
    def prepare_sonde_export(job_id: str, body: SondeExportRequest):
        """Prepare a persistent sonde download with progress, like a model box."""
        from .sonde import write_sonde
        result = selected_result(job_id, body.flight_index)
        def task(progress):
            validate_manifest(result)
            request = PredictionRequest.model_validate(result["request"])
            progress("Preparing simulated sonde along the selected flight…")
            weather = flight_weather(store.get(request.dataset, request.launch_datetime), request)
            path = settings.data_dir / "exports" / (uuid.uuid4().hex + ".sonde.nc")
            try:
                write_sonde(path, result, weather, body.scope, progress=progress)
                validate_manifest(result)
                return {"path": str(path), "filename": f"model-sonde-{request.launch_datetime:%Y%m%dT%H%M%SZ}-{job_id[:8]}-{body.flight_index}-{body.scope}.nc",
                        "bytes": path.stat().st_size, "type": "sonde", "scope": body.scope,
                        "flight_index": body.flight_index, "warnings": []}
            except Exception:
                path.unlink(missing_ok=True)
                raise
        return jobs.submit("export", task)

    @app.post("/api/predictions/{job_id}/model-box", status_code=202)
    def model_box(job_id: str, body: ModelBoxRequest):
        from .modelbox import export_model_boxes
        record = jobs.get(job_id)
        if record["status"] != "completed" or record["kind"] != "prediction":
            raise HTTPException(409, "Prediction is not complete.")
        return jobs.submit("export", lambda progress: export_model_boxes(record["result"],body,store,progress))

    @app.post("/api/predictions/{job_id}/offline-pack", status_code=202)
    def offline_pack(job_id: str, flight_index: int = Query(default=0, ge=0)):
        from .offline import export_forecast_pack
        result = selected_result(job_id, flight_index)
        return jobs.submit("export", lambda progress: export_forecast_pack(result, store, progress))

    @app.get("/api/exports/{job_id}/download")
    def model_box_download(job_id: str):
        record = jobs.get(job_id)
        if record["status"] != "completed" or record["kind"] != "export":
            raise HTTPException(409, "Export is not complete.")
        result = record["result"]
        path = Path(result["path"])
        if not path.is_file() or not path.resolve().is_relative_to((settings.data_dir / "exports").resolve()):
            raise HTTPException(404, "Export was cleared. Create it again from the prediction.")
        content_type = {".zip": "application/zip", ".bpforecast": "application/octet-stream"}.get(path.suffix, "application/x-netcdf")
        return FileResponse(path, filename=result["filename"], media_type=content_type)

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: str):
        return jobs.get(job_id)

    @app.get("/api/predictions/{job_id}/export/{format}")
    def export(job_id: str, format: str):
        job = jobs.get(job_id)
        if job["status"] != "completed" or job["kind"] != "prediction":
            raise HTTPException(409, "Prediction is not complete.")
        result = job["result"]
        formats = {"csv": (csv_export, "text/csv", "csv"), "geojson": (geojson, "application/geo+json", "geojson"),
                   "schedule": (arrival_csv_export, "text/csv", "csv"),
                   "kml": (kml_export, "application/vnd.google-earth.kml+xml", "kml"),
                   "json": (lambda value: value, "application/json", "json")}
        if format not in formats:
            raise HTTPException(404, "Use csv, geojson, kml, json or schedule (coordinated plans).")
        writer, media, extension = formats[format]
        data = writer(result)
        if isinstance(data, dict):
            data = json.dumps(data, allow_nan=False)
        return Response(data, media_type=media, headers={"Content-Disposition": f'attachment; filename="prediction-{job_id[:8]}.{extension}"'})

    frontend = ROOT / "frontend"
    app.mount("/assets", StaticFiles(directory=frontend), name="assets")

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(frontend / "index.html")

    return app


app = create_app()
