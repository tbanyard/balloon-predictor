from datetime import datetime, timezone

import numpy as np
import pytest
import xarray as xr

from balloon_predictor.weather.schema import G, dataset_from_arrays


@pytest.fixture
def utc():
    return datetime(2024, 1, 15, 12, tzinfo=timezone.utc)


def make_normalized():
    shape = (2, 3, 3, 3)
    heights = np.broadcast_to(np.array([100, 10000, 35000])[None, :, None, None], shape).copy()
    u = 2 + heights / 1000
    u[1] += 6
    fields = {"u": u, "v": np.full(shape, 3), "height": heights,
              "temperature": np.full(shape, 270), "pressure": np.broadcast_to(
                  np.array([100000, 25000, 500])[None, :, None, None], shape).copy()}
    surface_shape = (2,3,3)
    surface = {"terrain": np.zeros((3,3)), "surface_u": np.full(surface_shape, 2),
               "surface_v": np.full(surface_shape, 3), "surface_temperature": np.full(surface_shape, 280),
               "surface_pressure": np.full(surface_shape, 101000)}
    return dataset_from_arrays(["2024-01-15T12:00", "2024-01-15T18:00"], [51,53,55], [-6,-4,-2],
                               fields, surface, {"source": "test", "label": "Test weather"})


def make_pressure_file(path, forecast=False, expver=False):
    times = np.array(["2024-01-15T12:00", "2024-01-15T18:00"], dtype="datetime64[ns]")
    lat, lon, levels = [55,53,51], [354,356,358], [1000,500,10]
    shape = (2,3,3,3)
    data = {
        "u": np.full(shape,10,dtype="float32"), "v": np.full(shape,2,dtype="float32"),
        "t": np.full(shape,270,dtype="float32"),
        "z": np.broadcast_to(np.array([100,5500,31000])[None,:,None,None]*G,shape).copy()
    }
    dims = ("valid_time","pressure_level","latitude","longitude")
    ds = xr.Dataset({name:(dims,value) for name,value in data.items()},
                    coords={"valid_time":times,"pressure_level":levels,"latitude":lat,"longitude":lon})
    if forecast:
        ds = ds.rename(valid_time="step")
        ds = ds.assign_coords(step=np.array([0,6],dtype="timedelta64[h]"))
        ds = ds.expand_dims(time=[times[0]])
        ds["valid_time"] = (("time","step"), times[None])
    if expver:
        ds = ds.expand_dims(expver=[1,5]).copy(deep=True)
        # Final fields are missing in the second half; preliminary fields fill them.
        for name in data:
            ds[name].loc[{"expver":1}] = ds[name].sel(expver=1).where(ds.valid_time < times[1])
    ds.to_netcdf(path)
    return ds


def make_wrf_file(path, angle=90, moving=False, times=None):
    times = times or ["2024-01-15_12:00:00", "2024-01-15_18:00:00"]
    nt, nz, ny, nx = len(times), 3, 3, 3
    mass = ("Time","bottom_top","south_north","west_east")
    grid = ("Time","south_north","west_east")
    height = np.broadcast_to(np.array([0,1000,10000,40000],dtype=float)[None,:,None,None],(nt,nz+1,ny,nx)).copy()
    lat, lon = np.meshgrid([51,53,55],[-6,-4,-2],indexing="ij")
    lat = np.broadcast_to(lat,(nt,ny,nx)).copy().astype("float32")
    lon = np.broadcast_to(lon,(nt,ny,nx)).copy().astype("float32")
    if moving:
        lat[-1] += .05
    pressure = np.broadcast_to(np.array([95000,50000,1500])[None,:,None,None],(nt,nz,ny,nx)).copy()
    variables = {
        "Times":(("Time","DateStrLen"),np.array([list(s) for s in times],dtype="S1")),
        "XLAT":(grid,lat),"XLONG":(grid,lon),"HGT":(grid,np.zeros((nt,ny,nx))),
        "COSALPHA":(grid,np.full((nt,ny,nx),np.cos(np.radians(angle)))),
        "SINALPHA":(grid,np.full((nt,ny,nx),np.sin(np.radians(angle)))),
        "U":(("Time","bottom_top","south_north","west_east_stag"),np.full((nt,nz,ny,nx+1),10)),
        "V":(("Time","bottom_top","south_north_stag","west_east"),np.full((nt,nz,ny+1,nx),2)),
        "PH":(("Time","bottom_top_stag","south_north","west_east"),np.zeros_like(height)),
        "PHB":(("Time","bottom_top_stag","south_north","west_east"),height*9.81),
        "W":(("Time","bottom_top_stag","south_north","west_east"),np.full_like(height,.25)),
        "P":(mass,np.zeros_like(pressure)),"PB":(mass,pressure),"T":(mass,np.zeros_like(pressure)),
        "U10":(grid,np.full((nt,ny,nx),4)),"V10":(grid,np.full((nt,ny,nx),1)),
        "T2":(grid,np.full((nt,ny,nx),280)),"PSFC":(grid,np.full((nt,ny,nx),101000))
    }
    ds=xr.Dataset(variables)
    ds.to_netcdf(path)
    return ds


def make_grib(path, version=1, surface=True):
    import eccodes as ec
    with open(path,"wb") as f:
        for step in (0,6):
            for level, height in ((1000,100),(500,5500),(10,31000)):
                for param, value in ((131,10),(132,2),(130,270),(129,height*G),(135,-.1)):
                    gid=ec.codes_grib_new_from_samples("regular_ll_pl_grib2")
                    try:
                        ec.codes_set(gid,"setLocalDefinition",1)
                        for key,val in {"Ni":3,"Nj":3,"latitudeOfFirstGridPointInDegrees":55,
                                        "latitudeOfLastGridPointInDegrees":51,"longitudeOfFirstGridPointInDegrees":354,
                                        "longitudeOfLastGridPointInDegrees":358,"iDirectionIncrementInDegrees":2,
                                        "jDirectionIncrementInDegrees":2,"jScansPositively":0,
                                        "dataDate":20240115,"dataTime":1200,"forecastTime":step,"level":level,
                                        "paramId":param,"experimentVersionNumber":str(version).zfill(4)}.items():
                            ec.codes_set(gid,key,val)
                        ec.codes_set_values(gid,np.full(9,value))
                        ec.codes_write(gid,f)
                    finally:
                        ec.codes_release(gid)
            if surface:
                for param,value in ((165,4),(166,1),(167,280),(134,101000),(129,0)):
                    gid=ec.codes_grib_new_from_samples("regular_ll_sfc_grib2")
                    try:
                        ec.codes_set(gid,"setLocalDefinition",1)
                        for key,val in {"Ni":3,"Nj":3,"latitudeOfFirstGridPointInDegrees":55,
                                        "latitudeOfLastGridPointInDegrees":51,"longitudeOfFirstGridPointInDegrees":354,
                                        "longitudeOfLastGridPointInDegrees":358,"iDirectionIncrementInDegrees":2,
                                        "jDirectionIncrementInDegrees":2,"jScansPositively":0,
                                        "dataDate":20240115,"dataTime":1200,"forecastTime":step,
                                        "paramId":param,"experimentVersionNumber":str(version).zfill(4)}.items():
                            ec.codes_set(gid,key,val)
                        ec.codes_set_values(gid,np.full(9,value))
                        ec.codes_write(gid,f)
                    finally:
                        ec.codes_release(gid)
