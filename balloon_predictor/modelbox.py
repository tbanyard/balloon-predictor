"""Export native-grid weather boxes in bounded blocks, with source receipts."""
from datetime import datetime, timedelta, timezone
import copy
import json
import math
from pathlib import Path
import uuid
import zipfile
import numpy as np
from netCDF4 import Dataset, num2date

from .errors import PredictorError
from .provenance import validate_manifest, receipt
from .weather.interpolate import DatasetWeather
from .weather.io import NETCDF_LOCK
from .weather.era5_native.data import open_nc, decode
from .weather.era5_native.physics import full_pressure, hydrostatic_height, A, B
from .weather.schema import geometric_height, G

FIELDS = ("u", "v", "temperature", "pressure", "height", "w", "specific_humidity")
UNITS = {"u":"m s-1", "v":"m s-1", "w":"m s-1", "temperature":"K", "pressure":"Pa", "height":"m", "terrain":"m", "specific_humidity":"kg kg-1"}


def bounds(points, degrees=.5, km=50):
    lat = np.array([p["latitude"] for p in points])
    lon = np.mod([p["longitude"] for p in points], 360)
    ordered = np.sort(lon)
    gaps = np.diff(np.r_[ordered, ordered[0]+360])
    start = ordered[(int(np.argmax(gaps))+1) % len(ordered)]
    unwrapped = start + (lon-start) % 360
    angular = km / 6371.0088
    lat_margin = max(degrees, math.degrees(angular))
    extreme = min(89.9999, max(abs(float(lat.min())), abs(float(lat.max())))+lat_margin)
    lon_margin = max(degrees, math.degrees(math.asin(min(1., math.sin(angular) / math.cos(math.radians(extreme))))))
    return {"south": max(-90., float(lat.min())-lat_margin), "north": min(90., float(lat.max())+lat_margin),
            "west_unwrapped": float(unwrapped.min())-lon_margin, "east_unwrapped": float(unwrapped.max())+lon_margin,
            "longitude_centre": float((unwrapped.min()+unwrapped.max())/2),
            "margin_degrees_minimum": degrees, "margin_km_minimum": km}


def selection(weather, points, options):
    box = bounds(points, options.margin_degrees, options.margin_km)
    local_lon = box["longitude_centre"] + (weather.lon-box["longitude_centre"]+180)%360-180
    # Include native interpolation nodes enclosing the requested box, even if
    # the box is narrower than one grid cell.
    if weather.regular:
        def enclosing(axis, low, high):
            inside = np.flatnonzero((axis >= low) & (axis <= high))
            extra = [int(np.argmin(abs(axis-low))), int(np.argmin(abs(axis-high)))]
            indices = np.unique(np.r_[inside, extra])
            return np.arange(max(0, int(indices.min())-1), min(len(axis), int(indices.max())+2))
        ys = enclosing(weather.lat[:,0], box["south"], box["north"])
        xs = enclosing(local_lon[0], box["west_unwrapped"], box["east_unwrapped"])
        # Global grids crossing their stored longitude seam need disjoint native
        # x indices, ordered continuously around the flight rather than a globe.
        if getattr(weather, "periodic", False):
            axis = local_lon[0]
            order = np.argsort(axis)
            xs = order[enclosing(axis[order], box["west_unwrapped"], box["east_unwrapped"])]
    else:
        inside = ((weather.lat >= box["south"]) & (weather.lat <= box["north"]) &
                  (local_lon >= box["west_unwrapped"]) & (local_lon <= box["east_unwrapped"]))
        for p in points:
            for y,x,w in weather.spatial_weights(p["latitude"],p["longitude"]):
                inside[y,x] = True
        yy,xx = np.nonzero(inside)
        ys = np.arange(max(0,yy.min()-1), min(weather.ny,yy.max()+2))
        xs = np.arange(max(0,xx.min()-1), min(weather.nx,xx.max()+2))
    sublat = weather.lat[np.ix_(ys,xs)]
    sublon = local_lon[np.ix_(ys,xs)]
    box["clipped_sides"] = [name for name, clipped in (
        ("south", sublat.min()>box["south"]+1e-6), ("north", sublat.max()<box["north"]-1e-6),
        ("west", sublon.min()>box["west_unwrapped"]+1e-6), ("east", sublon.max()<box["east_unwrapped"]-1e-6)) if clipped]
    stamps = [datetime.fromisoformat(p["datetime"].replace("Z","+00:00")).timestamp() for p in points]
    low, high = min(stamps), max(stamps)
    weather._bracket(weather.times, low, "Time")
    weather._bracket(weather.times, high, "Time")
    t0 = max(0, int(np.searchsorted(weather.times,low,side="right"))-1)
    t1 = min(len(weather.times)-1, int(np.searchsorted(weather.times,high,side="left")))
    return ys,xs,list(range(t0,t1+1)),box


def era_block(weather, ti, ys, xs):
    _, fid, index = weather.records[ti]
    meta = weather.archive.meta(fid)
    if not np.array_equal(meta["levels"],weather.meta["levels"]) or not np.array_equal(meta["lat"],weather.meta["lat"]) or not np.array_equal(meta["lon"],weather.meta["lon"]):
        raise PredictorError("ERA5 source grids changed across the export window.")
    td,ld,yd,xd = meta["dims"]
    def read(var, level=True):
        selectors=[]; remaining=[]
        for dim,size in zip(var.dimensions,var.shape):
            if dim==td: selectors.append(index)
            elif dim==yd: selectors.append(meta["yi"][ys]); remaining.append(yd)
            elif dim==xd: selectors.append(meta["xi"][xs]); remaining.append(xd)
            elif dim==ld and level: selectors.append(meta["li"]); remaining.append(ld)
            elif size==1: selectors.append(0)
            else: raise PredictorError(f"Unsupported ERA5 export dimension {dim}.")
        arr=decode(var,tuple(selectors))
        dims=([ld] if ld in remaining else [])+[yd,xd]
        return np.transpose(arr,[remaining.index(d) for d in dims])
    with open_nc(meta["path"]) as nc:
        fields={"u":read(nc["u"]),"v":read(nc["v"]),"temperature":read(nc["t"])}
        sp=np.exp(read(nc["lnsp"],False)) if "lnsp" in nc.variables else read(nc["sp"],False)
        q=read(nc["q"]) if "q" in meta["fields"] else None
        if meta["surface_z"]: z=read(nc[meta["surface_z"]],False)
        else:
            yi,xi=meta["topo_indices"]
            z=weather.archive.topography["z"][np.ix_(yi[ys],xi[xs])]
        fields["pressure"]=full_pressure(sp,meta["levels"])
        fields["height"]=geometric_height(hydrostatic_height(fields["temperature"],sp,meta["levels"],q)+z/G)
        if q is not None: fields["specific_humidity"]=q
        return fields,{"terrain":geometric_height(z/G),"surface_pressure":sp}


def wrf_block(weather, ti, ys, xs):
    _,path,index=weather.records[ti]
    # Enforce the same fixed-grid checks as trajectory sampling.
    weather.raw_column(ti,int(ys[0]),int(xs[0]))
    with Dataset(path) as nc:
        def read(name):
            var=nc[name]; selectors=[]
            for dim,size in zip(var.dimensions,var.shape):
                if dim=="Time": selectors.append(index)
                elif dim=="south_north": selectors.append(ys)
                elif dim=="west_east": selectors.append(xs)
                elif dim=="south_north_stag": selectors.append(np.arange(ys[0],ys[-1]+2))
                elif dim=="west_east_stag": selectors.append(np.arange(xs[0],xs[-1]+2))
                elif dim in ("bottom_top","bottom_top_stag"): selectors.append(slice(None))
                elif size==1: selectors.append(0)
                else: raise PredictorError(f"Unsupported WRF export dimension {dim}.")
            return np.ma.filled(var[tuple(selectors)],np.nan).astype(float)
        u,v=read("U"),read("V"); u=(u[...,:-1]+u[...,1:])/2; v=(v[...,:-1,:]+v[...,1:,:])/2
        ca,sa=read("COSALPHA"),read("SINALPHA")
        p=read("P")+read("PB"); h=(read("PH")+read("PHB"))/9.81
        fields={"u":u*ca-v*sa,"v":v*ca+u*sa,"temperature":(read("T")+300)*(p/100000)**(287/1004),"pressure":p,"height":(h[:-1]+h[1:])/2}
        if "W" in nc.variables:
            w=read("W"); fields["w"]=(w[:-1]+w[1:])/2
        if "QVAPOR" in nc.variables:
            r=read("QVAPOR"); fields["specific_humidity"]=np.where(r>=0,r/(1+r),np.nan)
        surface={"terrain":read("HGT")}
        if {"U10","V10","T2","PSFC"}.issubset(nc.variables):
            a,b=read("U10"),read("V10")
            surface.update(surface_u=a*ca-b*sa,surface_v=b*ca+a*sa,surface_temperature=read("T2"),surface_pressure=read("PSFC"))
            if "Q2" in nc.variables:
                r=read("Q2"); surface["surface_specific_humidity"]=np.where(r>=0,r/(1+r),np.nan)
        return fields,surface


def model_weather(store, dataset, when):
    path=store.settings.data_dir/"weather"/(dataset+".nc")
    if not path.is_file():
        return store.get(dataset,when)
    with Dataset(path) as nc:
        weather=DatasetWeather.__new__(DatasetWeather)
        var=nc["time"]
        times=num2date(var[:],var.units,calendar=getattr(var,"calendar","standard"),only_use_cftime_datetimes=False)
        weather.times=np.array([datetime(t.year,t.month,t.day,t.hour,t.minute,t.second,t.microsecond,tzinfo=timezone.utc).timestamp() for t in times])
        weather.setup_grid(nc["latitude"][:],nc["longitude"][:])
        weather.source_path=path
        weather.info=json.loads(path.with_suffix(".json").read_text())
        weather.levels=np.asarray(nc["level"][:])
    return weather


def write_box(path, weather, points, options, max_mb, progress):
    if not hasattr(weather,"lat"):
        raise PredictorError("Synthetic demo weather has no native model grid to export. Select ERA5, IFS, GFS or WRF.")
    if hasattr(weather,"ensure_time"):
        start=min(datetime.fromisoformat(p["datetime"].replace("Z","+00:00")) for p in points)
        end=max(datetime.fromisoformat(p["datetime"].replace("Z","+00:00")) for p in points)
        stamp=start
        while stamp<end:
            weather.ensure_time(stamp); stamp+=timedelta(days=1)
        weather.ensure_time(end)
    ys,xs,times,box=selection(weather,points,options)
    if hasattr(weather,"source_path"):
        def block(ti, yy, xx):
            with Dataset(weather.source_path) as src:
                fields={k:np.ma.filled(src[k][ti,:,yy,xx],np.nan) for k in FIELDS if k in src.variables}
                surface={k:np.ma.filled(v[yy,xx] if v.ndim==2 else v[ti,yy,xx],np.nan)
                         for k,v in src.variables.items() if k=="terrain" or k.startswith("surface_") and v.ndim==3}
                return fields,surface
        levels=weather.levels
    else:
        block=lambda ti,yy,xx:(era_block if hasattr(weather,"archive") else wrf_block)(weather,ti,yy,xx)
        if hasattr(weather,"archive"): levels=weather.meta["levels"]
        else:
            with Dataset(weather.records[0][1]) as src: levels=np.arange(len(src.dimensions["bottom_top"]))
    estimate=len(times)*len(levels)*len(ys)*len(xs)*len(FIELDS)*4 + len(ys)*len(xs)*len(times)*32
    if estimate>max_mb*1024**2:
        raise PredictorError(f"Model box estimate {estimate/1024**2:.0f} MB exceeds max_export_mb ({max_mb}). Export one flight or shorten the plan.")
    source_files = ([Path(weather.source_path)] if hasattr(weather,"source_path") else
                    [weather.archive.meta(weather.records[ti][1])["path"] for ti in times] if hasattr(weather,"archive") else
                    [Path(weather.records[ti][1]) for ti in times])
    if hasattr(weather,"archive") and weather.archive.topography:
        source_files.append(weather.archive.root / "era5_topo.nc")
    source_receipts = [receipt(p) for p in sorted(set(source_files))]
    provenance = copy.deepcopy(weather.info.get("provenance",{}))
    provenance["files"] = [str(p) for p in sorted(set(source_files))]
    progress(f"Model box: {len(times)} times × {len(levels)} levels × {len(ys)} × {len(xs)} cells; up to {estimate/1024**2:.1f} MB before compression")
    with Dataset(path,"w",format="NETCDF4") as out:
        for name,size in (("time",len(times)),("level",len(levels)),("y",len(ys)),("x",len(xs))): out.createDimension(name,size)
        t=out.createVariable("time","f8",("time",)); t[:]=weather.times[times]; t.units="seconds since 1970-01-01 00:00:00 UTC"; t.calendar="proleptic_gregorian"
        out.createVariable("level","i4",("level",))[:]=levels
        out.createVariable("y","i4",("y",))[:]=ys
        out.createVariable("x","i4",("x",))[:]=xs
        for name,data,unit in (("latitude",weather.lat,"degrees_north"),("longitude",weather.lon,"degrees_east")):
            v=out.createVariable(name,"f8",("y","x")); v[:]=data[np.ix_(ys,xs)]; v.units=unit
        if hasattr(weather,"archive"):
            out.createDimension("half_level",138)
            a=out.createVariable("hybrid_a","f8",("half_level",)); a[:]=A; a.units="Pa"
            b=out.createVariable("hybrid_b","f8",("half_level",)); b[:]=B; b.units="1"
        out.setncatts({"Conventions":"CF-1.8", "title":"BalloonPredictor native-grid model box", "source":weather.info["source"],
            "source_dataset":weather.info["id"],"provenance_json":json.dumps(provenance), "input_manifest_json":json.dumps(source_receipts),
            "requested_box_json":json.dumps(box),"estimated_uncompressed_bytes":estimate,
            "height_reference":"AMSL", "wind_reference":"earth", "schema_version":1,
            "description":"All available native levels and bracketing timestamps. No horizontal/vertical resampling; WRF winds destaggered/rotated and ERA5 heights reconstructed exactly as for trajectories. Model terrain is unchanged. Missing fields are not invented."})
        total=len(times)*math.ceil(len(ys)/8); done=0
        for oi,ti in enumerate(times):
            for offset in range(0,len(ys),8):
                yy=ys[offset:offset+8]
                fields,surface=block(ti,yy,xs)
                for name,data in {**fields,**surface}.items():
                    static=name=="terrain"
                    dims=("time","level","y","x") if name in fields else ("y","x") if static else ("time","y","x")
                    if name not in out.variables:
                        chunks = tuple(1 if d=="time" else min(len(levels),16) if d=="level" else min(len(ys),8) if d=="y" else min(len(xs),64) for d in dims)
                        v=out.createVariable(name,"f4",dims,zlib=True,complevel=2,chunksizes=chunks,fill_value=np.float32(np.nan))
                        v.units=UNITS.get(name,UNITS.get(name.removeprefix("surface_"),"1"))
                        if name in fields: v.coordinates="latitude longitude" if name=="height" else "latitude longitude height"
                    v=out[name]
                    if name in fields: v[oi,:,offset:offset+len(yy),:]=data
                    elif static: v[offset:offset+len(yy),:]=data
                    else: v[oi,offset:offset+len(yy),:]=data
                done+=1; progress(f"Writing model box · {done}/{total} blocks · estimate {estimate/1024**2:.1f} MB")
    for entry in source_receipts:
        if receipt(entry["path"]) != entry:
            raise PredictorError("Model source changed while exporting. Re-run the prediction and export.")
    return {"file":path.name,"bytes":path.stat().st_size,"requested_box":box,"times":len(times),"levels":len(levels),"estimated_bytes":estimate}


def export_model_boxes(result, options, store, progress):
    if result.get("type")=="series":
        entries=[e for e in result["flights"] if e.get("status")=="completed" and (options.selection=="plan" or e["index"]==options.flight_index)]
        results=[e["result"] for e in entries]
    else:
        results=[result] if options.flight_index==0 or options.selection=="plan" else []
    if not results: raise PredictorError("No completed flights selected for this model box.")
    groups={}
    for flight in results:
        validate_manifest(flight)
        key=(flight["request"]["dataset"],json.dumps({k:v for k,v in flight["weather"].get("provenance",{}).items() if k != "files"},sort_keys=True))
        groups.setdefault(key,[]).append(flight)
    root=store.settings.data_dir/"exports"; token=uuid.uuid4().hex; paths=[]; boxes=[]
    try:
        # netCDF C/HDF5 access is serialized; buffers are one native row block.
        with NETCDF_LOCK:
            for gi,((dataset,_),flights) in enumerate(groups.items()):
                points=[]
                for flight in flights:
                    members=[flight]+(flight.get("ensemble",[]) if options.include_ensemble else [])
                    for member in members:
                        for stage in member["prediction"]:
                            if options.scope=="full" or stage["stage"]=="ascent": points.extend(stage["trajectory"])
                when=datetime.fromisoformat(points[0]["datetime"].replace("Z","+00:00"))
                weather=model_weather(store,dataset,when)
                path=root/f"{token}-{gi+1}.nc"; paths.append(path)
                box=write_box(path,weather,points,options,store.settings.max_export_mb,progress)
                for flight in flights: validate_manifest(flight)
                boxes.append(box)
        if len(paths)==1:
            target=paths[0]; filename=f"model-box-{dataset}.nc"
        else:
            target=root/(token+".zip")
            with zipfile.ZipFile(target,"w",zipfile.ZIP_STORED) as archive:
                for path in paths: archive.write(path,path.name)
                archive.writestr("manifest.json",json.dumps(boxes,indent=2))
            for path in paths: path.unlink()
            paths=[target]; filename="model-boxes.zip"
        return {"path":str(target),"filename":filename,"bytes":target.stat().st_size,"boxes":boxes,
                "warnings":["Requested margins are clipped to available model coverage." for b in boxes if b["requested_box"]["clipped_sides"]]}
    except Exception:
        for path in paths: path.unlink(missing_ok=True)
        raise
