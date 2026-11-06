"""NOAA GFS 0.25-degree pressure/surface subsets, cached by explicit run."""
from datetime import datetime, timedelta, timezone
import json
import time
from urllib.parse import urlencode

from ..errors import PredictorError
from .download import cached_retrieve, check_download_size, check_forecast_coverage, forecast_steps
from .readers import read_grib

FILTER = "https://nomads.ncep.noaa.gov/cgi-bin/filter_gfs_0p25.pl"
ROOT = "https://nomads.ncep.noaa.gov/pub/data/nccf/com/gfs/prod"
# Actual GFS pgrb2.0p25 inventory, not ERA5's different pressure-level grid.
LEVELS = [1000, 975, 950, 925, 900, 850, 800, 750, 700, 650, 600, 550, 500,
          450, 400, 350, 300, 250, 200, 150, 100, 70, 50, 40, 30, 20, 15,
          10, 7, 5, 3, 2, 1, .7, .4, .2, .1, .07, .04, .02, .01]


def subset_url(cycle, step, area, surface=True):
    north, west, south, east = area
    params = {"file": f"gfs.t{cycle:%H}z.pgrb2.0p25.f{step:03d}",
              "dir": f"/gfs.{cycle:%Y%m%d}/{cycle:%H}/atmos",
              "var_HGT": "on", "var_TMP": "on", "var_UGRD": "on", "var_VGRD": "on",
              "var_SPFH": "on", "var_VVEL": "on", "subregion": "",
              "leftlon": west, "rightlon": east, "toplat": north, "bottomlat": south,
              **{f"lev_{level}_mb": "on" for level in LEVELS}}
    if surface:
        params.update(lev_surface="on", lev_2_m_above_ground="on", lev_10_m_above_ground="on", var_PRES="on")
    return FILTER + "?" + urlencode(params)


def latest_cycle(request, progress):
    import requests
    now = datetime.now(timezone.utc)
    newest = now.replace(hour=now.hour // 6 * 6, minute=0, second=0, microsecond=0)
    for offset in range(12):
        cycle = newest - timedelta(hours=offset * 6)
        try:
            steps = forecast_steps(cycle, request.start, request.end, "gfs")
        except PredictorError:
            continue
        progress(f"Checking GFS {cycle:%Y-%m-%d %H} UTC availability")
        available = True
        for step in sorted({steps[0], steps[-1]}):
            url = f"{ROOT}/gfs.{cycle:%Y%m%d}/{cycle:%H}/atmos/gfs.t{cycle:%H}z.pgrb2.0p25.f{step:03d}.idx"
            try:
                with requests.get(url, stream=True, timeout=(10, 25)) as response:
                    if response.status_code != 200:
                        available = False
                        break
            except requests.RequestException:
                available = False
                break
        if available:
            return cycle
    raise PredictorError("No available recent GFS run brackets this window. Choose a recent/future flight, specify an available cycle, or use local historical data.")


def download_gfs(request, settings, progress):
    try:
        import requests
    except ImportError as exc:
        raise PredictorError("Install forecast support: python -m pip install -e '.[weather]'.") from exc
    cycle = request.cycle or latest_cycle(request, progress)
    steps = forecast_steps(cycle, request.start, request.end, "gfs")
    check_download_size(request, settings, len(steps), len(LEVELS))
    files = []
    for step in steps:
        url = subset_url(cycle, step, request.area, request.surface)
        progress(f"Retrieving GFS {cycle:%Y-%m-%d %H} UTC · +{step} h · regional atmospheric columns")
        def retrieve(target, url=url):
            for attempt in range(3):
                try:
                    with requests.get(url, stream=True, timeout=(15, 180)) as response:
                        response.raise_for_status()
                        with target.open("wb") as output:
                            for chunk in response.iter_content(1024 * 1024):
                                output.write(chunk)
                    with target.open("rb") as check:
                        if check.read(4) != b"GRIB":
                            raise PredictorError("GFS returned no GRIB data. Check run availability, area and forecast hours.")
                    time.sleep(1)  # Be considerate to the public NOMADS subset service.
                    return
                except (requests.RequestException, PredictorError) as exc:
                    target.unlink(missing_ok=True)
                    if attempt == 2:
                        raise PredictorError("GFS subset download failed. Retry later or choose an earlier available cycle; NOMADS retains recent runs only.") from exc
                    time.sleep(2 ** (attempt + 1))
        files.append(cached_retrieve(settings.data_dir / "cache", {"provider": "gfs", "url": url}, retrieve, request.refresh))
    progress("Decoding GFS winds, temperature, humidity, vertical motion and heights")
    ds = read_grib(files, area=request.area, expected_cycle=cycle,
                   attrs={"source": "gfs", "label": f"{request.name} · GFS {cycle:%d %b %H}Z"})
    check_forecast_coverage(ds, request)
    provenance = json.loads(ds.attrs["provenance_json"])
    provenance.update(provider="NOAA GFS via NOMADS", cycle=cycle.isoformat(),
                      forecast_steps_hours=steps, horizontal_grid_degrees=.25,
                      attribution="NOAA / NCEP GFS", downloaded_utc=datetime.now(timezone.utc).isoformat())
    ds.attrs["provenance_json"] = json.dumps(provenance)
    return ds
