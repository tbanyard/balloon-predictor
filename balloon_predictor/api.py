from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
import json
import logging
from pathlib import Path
import threading
import uuid

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from . import __version__
from .errors import PredictorError
from .exports import csv_export, geojson, kml_export
from .models import BatchPredictionRequest, DownloadRequest, PredictionRequest
from .predictor import predict, predict_batch
from .settings import ROOT, Settings
from .store import WeatherStore, atomic_json
from .weather.download import download_weather

LOGGER = logging.getLogger(__name__)


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
            job_id = uuid.uuid4().hex
            self.records[job_id] = {"id": job_id, "kind": kind, "status": "queued", "message": "Queued"}
        def update(message):
            with self.lock:
                self.records[job_id]["message"] = message
        def run():
            with self.lock:
                self.records[job_id]["status"] = "running"
            try:
                result = task(update)
                if kind == "prediction":
                    result["id"] = job_id
                    atomic_json(self.settings.data_dir / "results" / (job_id + ".json"), result)
                with self.lock:
                    self.records[job_id].update(status="completed", message="Complete", result=result)
            except PredictorError as exc:
                with self.lock:
                    self.records[job_id].update(status="failed", message=str(exc), error=str(exc))
                # Do not log remote error bodies or credentials.
                LOGGER.error("Job %s failed: %s (cause type: %s)", job_id, str(exc),
                             type(exc.__cause__).__name__ if exc.__cause__ else "none")
            except Exception as exc:
                LOGGER.error("Job %s failed with exception type %s", job_id, type(exc).__name__)
                with self.lock:
                    self.records[job_id].update(status="failed", message="Unexpected processing error. Check input files and the server log.",
                                                error="Unexpected processing error. Check input files and the server log.")
        self.executor.submit(run)
        return {"id": job_id, "status": "queued"}

    def get(self, job_id):
        with self.lock:
            if job_id in self.records:
                return dict(self.records[job_id])
        if len(job_id) == 32 and all(c in "0123456789abcdef" for c in job_id):
            path = self.settings.data_dir / "results" / (job_id + ".json")
            if path.exists():
                return {"id": job_id, "kind": "prediction", "status": "completed",
                        "message": "Complete", "result": json.loads(path.read_text(encoding="utf-8"))}
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

    @app.exception_handler(PredictorError)
    async def known_error(request: Request, exc: PredictorError):
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    @app.get("/api/health")
    def health():
        return {"status": "ok", "version": __version__, "datasets": len(store.list()), "time_reference": "UTC"}

    @app.get("/api/weather")
    def weather_list():
        return {"datasets": store.list(), "defaults":{"dataset":"era5", "era5_root":str(settings.era5_root),
                    "wrf_root":str(settings.wrf_root),"cache_mb":settings.cache_mb},
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
        return result

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
        weather=store.get(request.dataset,request.launch_datetime)
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
                return store.save(body.name, ds)
            finally:
                ds.close()
        return jobs.submit("weather", task)

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
                   "kml": (kml_export, "application/vnd.google-earth.kml+xml", "kml"),
                   "json": (lambda value: value, "application/json", "json")}
        if format not in formats:
            raise HTTPException(404, "Use csv, geojson, kml or json.")
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
