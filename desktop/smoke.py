"""Offline integration checks run using only the installed runtime."""
from datetime import datetime, timezone
import json
import logging
import time
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

OPENER = build_opener(ProxyHandler({}))
LOG = logging.getLogger("balloon-predictor.smoke")
CYCLE = datetime(2024, 1, 15, 12, tzinfo=timezone.utc)


def write_grib(path, packing):
    import eccodes as ec
    import numpy as np
    from balloon_predictor.weather.schema import G

    with path.open("wb") as output:
        for step in (0, 6):
            for level, height in ((1000, 100), (500, 5500), (10, 31000)):
                for param, value in ((131, 10), (132, 2), (130, 270), (129, height * G)):
                    gid = ec.codes_grib_new_from_samples("regular_ll_pl_grib2")
                    try:
                        keys = {
                            "Ni": 3, "Nj": 3,
                            "latitudeOfFirstGridPointInDegrees": 55, "latitudeOfLastGridPointInDegrees": 51,
                            "longitudeOfFirstGridPointInDegrees": 354, "longitudeOfLastGridPointInDegrees": 358,
                            "iDirectionIncrementInDegrees": 2, "jDirectionIncrementInDegrees": 2,
                            "dataDate": 20240115, "dataTime": 1200, "forecastTime": step,
                            "level": level, "paramId": param, "packingType": packing,
                        }
                        for key, setting in keys.items():
                            ec.codes_set(gid, key, setting)
                        ec.codes_set_values(gid, value + np.arange(9, dtype=float) / 100)
                        assert ec.codes_get(gid, "packingType") == packing
                        ec.codes_write(gid, output)
                    finally:
                        ec.codes_release(gid)


def check_runtime(url, store):
    import cdsapi  # noqa: F401
    import ecmwf.opendata  # noqa: F401
    from netCDF4 import Dataset
    import numpy as np
    import rasterio
    from rasterio.transform import from_origin

    from balloon_predictor.weather.readers import read_grib

    def fetch(path, body=None, headers=None):
        headers = dict(headers or {})
        data = None if body is None else json.dumps(body).encode()
        if data is not None:
            headers.update({"Content-Type": "application/json", "Origin": headers.get("Origin", url)})
        with OPENER.open(Request(url + path, data=data, headers=headers), timeout=15) as response:
            payload = response.read()
            return json.loads(payload) if response.headers.get_content_type() == "application/json" else payload

    def completed(job):
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            record = fetch("/api/jobs/" + job["id"])
            if record["status"] == "completed":
                return record["result"]
            if record["status"] == "failed":
                raise RuntimeError(record["error"])
            time.sleep(0.05)
        raise TimeoutError("Prediction or export did not finish.")

    assert b"Calculate flight" in fetch("/")
    assert b"loadDatasets" in fetch("/assets/app.js")
    assert fetch("/api/weather")["defaults"]["dataset"] == "demo"
    try:
        fetch("/api/storage/cleanup", {"target": "results"}, {"Origin": "https://example.invalid"})
    except HTTPError as error:
        assert error.code == 403
    else:
        raise RuntimeError("An unrelated website could submit a cleanup request.")

    request = {
        "launch_datetime": "2024-01-15T12:30:00Z", "ascent_rate": 5,
        "burst_altitude": 1000, "flight_mode": "ascent", "landing_mode": "fixed",
    }
    completed(fetch("/api/predictions", {"dataset": "demo", **request}))
    LOG.info("Demo flight ran with ascent rate exactly 5 m/s.")

    directory = store.settings.data_dir
    for packing in ("grid_ccsds", "grid_jpeg"):
        source = directory / "raw" / (packing + ".grib2")
        write_grib(source, packing)
        with read_grib([source], expected_cycle=CYCLE, attrs={"source": "test", "label": packing}) as ds:
            assert ds.sizes["time"] == 2 and ds.sizes["level"] == 3
            assert np.allclose(ds.u.values[0, 0], 10 + np.arange(9).reshape(3, 3)[::-1] / 100)
            store.save(packing, ds)
        LOG.info("Decoded and cached GRIB (%s).", packing)

    tile = store.terrain.root / "test-tile.tif"
    with rasterio.open(
        tile, "w", driver="GTiff", width=3, height=3, count=1, dtype="int16",
        crs="EPSG:4326", transform=from_origin(-6, 55, 1, 1), nodata=-9999,
    ) as dataset:
        dataset.write(np.full((3, 3), 123, dtype="int16"), 1)
    assert fetch("/api/terrain/sample?latitude=54.5&longitude=-5.5")["altitude"] == 123
    LOG.info("Wrote and sampled a geographic GeoTIFF.")

    flight = fetch("/api/predictions", {"dataset": "grid_ccsds", **request})
    completed(flight)
    for endpoint, expected in (("sonde-export", "sonde"), ("model-box", "model-box")):
        job = fetch(f"/api/predictions/{flight['id']}/{endpoint}", {"scope": "ascent"})
        result = completed(job)
        payload = fetch(f"/api/exports/{job['id']}/download")
        assert payload[:8] == b"\x89HDF\r\n\x1a\n" and len(payload) == result["bytes"]
        path = directory / "raw" / (expected + ".nc")
        path.write_bytes(payload)
        with Dataset(path) as nc:
            if expected == "sonde":
                assert nc.featureType == "trajectory" and nc.trajectory_scope == "ascent"
            else:
                assert len(nc.dimensions["time"]) == 2 and len(nc.dimensions["level"]) == 3
                assert np.all(np.isfinite(nc["u"][:]))
        LOG.info("Generated, downloaded and reopened %s NetCDF.", expected)
