"""Scoped native-resolution WRF prediction I/O, without parallel execution.

A calculation retains a small number of open frames. Decoded 3-D blocks share
ColumnCache's existing byte limit, including across flights/members. Context-local
sessions keep concurrent jobs' file handles separate; every netCDF call uses the
same process lock as the scalar reader. Outside predictions that reader remains
available for map/export code and reference checks.
"""
from collections import OrderedDict
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
import math

from netCDF4 import Dataset
import numpy as np

from ..errors import PredictorError
from ..provenance import record_file, receipt
from .io import NETCDF_LOCK
from .wrf_rotation import rotation

_ACTIVE = ContextVar('balloon_wrf_prediction_reader', default=None)
MAX_OPEN_FILES = 3
# Across all open frame handles; decoded tiles have the separate ColumnCache cap.
CHUNK_CACHE_BYTES = 24 * 1024**2


def active_reader():
    return _ACTIVE.get()


@contextmanager
def prediction_io():
    previous = _ACTIVE.get()
    if previous is not None:
        yield previous
        return
    reader = PredictionReader()
    token = _ACTIVE.set(reader)
    try:
        yield reader
    finally:
        try:
            reader.close()
        finally:
            _ACTIVE.reset(token)


def scoped_prediction(function):
    @wraps(function)
    def scoped(*args, **kwargs):
        with prediction_io():
            return function(*args, **kwargs)
    return scoped


class PredictionReader:
    def __init__(self):
        self.handles = OrderedDict()
        self.shapes = {}
        self.opens = self.block_reads = self.cache_hits = 0

    def close(self):
        with NETCDF_LOCK:
            while self.handles:
                _, (handle, _) = self.handles.popitem(last=False)
                handle.close()

    def opened(self, path, stamp):
        if path in self.handles:
            handle, original = self.handles[path]
            if original != stamp:
                raise PredictorError('WRF source changed during prediction. Rescan local data and recalculate.')
            self.handles.move_to_end(path)
            return handle
        while len(self.handles) >= MAX_OPEN_FILES:
            _, (old, _) = self.handles.popitem(last=False)
            old.close()
        handle = Dataset(path)
        try:
            variables = [v for v in handle.variables.values() if v.ndim >= 2]
            budget = max(1, CHUNK_CACHE_BYTES // MAX_OPEN_FILES // max(1, len(variables)))
            if handle.data_model in ('NETCDF4','NETCDF4_CLASSIC'):
                for var in variables:var.set_var_chunk_cache(budget, 1009, .75)
        except Exception:
            handle.close()
            raise
        self.handles[path] = (handle, dict(stamp))
        self.opens += 1
        return handle

    @staticmethod
    def check_grid(handle, model, path, index, stamp):
        key = (path, stamp['path'], stamp['mtime_ns'], stamp['size'], index)
        if key in model.checked_grids:
            return
        def grid(name):
            var = handle[name]
            return np.asarray(var[index] if 'Time' in var.dimensions else var[:], dtype=float)
        lat, lon = grid('XLAT'), (grid('XLONG')+180)%360-180
        if (lat.shape != model.lat.shape or not np.allclose(lat, model.lat, atol=1e-5, rtol=0)
            or not np.allclose(lon, model.lon, atol=1e-5, rtol=0)
            or not np.allclose(grid('HGT'), model.ground, atol=.01, rtol=0)):
            raise PredictorError('WRF grid or terrain changes. Moving nests and mixed grids are unsupported; select one fixed domain/run.')
        model.checked_grids.add(key)
        # The unchanged scalar map/export reader can reuse this fixed-grid
        # validation. Prediction keys additionally identify resolved targets.
        model.checked_grids.add((path, stamp['mtime_ns'], index))

    @staticmethod
    def tile_shape(handle, model):
        # Prefer source chunk boundaries when small enough. Full vertical
        # columns stay intact, and even tiny configured caches stay bounded.
        var = handle['P'];chunks = var.chunking()
        chunk_sizes = dict(zip(var.dimensions, chunks)) if isinstance(chunks, (list, tuple)) else {}
        def side(dimension):
            size = int(chunk_sizes.get(dimension, 16))
            return max((d for d in range(1, min(size, 32)+1) if size % d == 0), default=1)
        ny,nx = min(model.ny, side('south_north')), min(model.nx, side('west_east'))
        levels = len(handle.dimensions['bottom_top'])
        # Includes all fields, transient coordinate arrays and both time tiles.
        target = max(1, min(8*1024**2, model.cache.limit//4))
        while ny*nx*max(1, levels)*10*8 > target and (ny>1 or nx>1):
            if nx >= ny and nx > 1:nx=max(1,nx//2)
            else:ny=max(1,ny//2)
        return ny,nx

    def column(self, model, ti, y, x):
        with NETCDF_LOCK:
            _,path,index = model.records[ti]
            stamp = record_file(model, path)
            # A changed source cannot be hidden by a decoded tile cache hit.
            if path in self.handles and self.handles[path][1] != stamp:
                raise PredictorError('WRF source changed during prediction. Rescan local data and recalculate.')
            handle = self.opened(path, stamp)
            self.check_grid(handle, model, path, index, stamp)
            shape_key = (path, stamp['mtime_ns'], stamp['size'], model.cache.limit)
            if shape_key not in self.shapes:self.shapes[shape_key] = self.tile_shape(handle, model)
            sy,sx = self.shapes[shape_key]
            y0,x0 = int(y)//sy*sy, int(x)//sx*sx
            key = ('wrf-flight-v166', path, stamp['path'], stamp['mtime_ns'], stamp['size'], index,
                   model.info['provenance'].get('wind_reference'), sy,sx,y0,x0)
            tile = model.cache.get(key)
            if tile is None:
                tile = read_block(handle, index, slice(y0,min(y0+sy,model.ny)),
                                  slice(x0,min(x0+sx,model.nx)), model.info['provenance'].get('wind_reference'))
                if receipt(path) != stamp:
                    raise PredictorError('WRF source changed while reading a prediction block. Rescan and recalculate.')
                model.cache.put(key, tile)
                self.block_reads += 1
            else:
                self.cache_hits += 1
            iy,ix = int(y)-y0,int(x)-x0
            if not tile['rotation_good'][iy,ix]:raise PredictorError('WRF rotation coefficients are invalid.')
            raw = {name: values[:,iy,ix] for name,values in tile['fields'].items()}
            raw['terrain'] = float(tile['terrain'][iy,ix])
            surface = {name:float(value[iy,ix]) for name,value in tile['surface'].items()}
            # Match the scalar reader, including invalid Q2 disabling its anchor.
            if surface and all(math.isfinite(v) for v in surface.values()) and surface['temperature']>0 and surface['pressure']>0:
                raw['surface'] = surface
            return raw


def read_block(nc, index, ys, xs, frame):
    """Float64 native fields, with exactly the scalar reader's conversions."""
    def read(name, dy=0, dx=0):
        var=nc[name];selectors=[]
        for dim,size in zip(var.dimensions,var.shape):
            if dim=='Time':selectors.append(index)
            elif dim in ('south_north','south_north_stag'):selectors.append(slice(ys.start,ys.stop+dy))
            elif dim in ('west_east','west_east_stag'):selectors.append(slice(xs.start,xs.stop+dx))
            elif dim in ('bottom_top','bottom_top_stag'):selectors.append(slice(None))
            elif size==1:selectors.append(0)
            else:raise PredictorError(f'Unsupported WRF dimension {dim}.')
        return np.ma.filled(var[tuple(selectors)],np.nan).astype(float)
    u,v=read('U',dx=1),read('V',dy=1)
    u,v=(u[...,:-1]+u[...,1:])/2,(v[...,:-1,:]+v[...,1:,:])/2
    if (frame or getattr(nc,'balloon_wind_reference','grid')) == 'grid' and {'COSALPHA','SINALPHA'}.issubset(nc.variables):
        ca,sa=read('COSALPHA'),read('SINALPHA')
    else:
        ca,sa=rotation(nc.__dict__,read('XLAT'),read('XLONG'),frame=frame)
    # Invalid rotation in an unrelated cell must not invalidate the requested
    # column; masking it reproduces the scalar reader's coverage failure there.
    rotation_good=np.isfinite(ca+sa)&(np.abs(ca*ca+sa*sa-1)<=.01)
    pressure=read('P')+read('PB');height=(read('PH')+read('PHB'))/9.81
    fields={'u':np.where(rotation_good,u*ca-v*sa,np.nan),'v':np.where(rotation_good,v*ca+u*sa,np.nan),
            'temperature':(read('T')+300)*(pressure/100000)**(287/1004),
            'pressure':pressure,'height':(height[:-1]+height[1:])/2}
    if 'W' in nc.variables:
        w=read('W');fields['w']=(w[:-1]+w[1:])/2
    if 'QVAPOR' in nc.variables:
        r=read('QVAPOR');fields['specific_humidity']=np.where(r>=0,r/(1+r),np.nan)
    terrain=read('HGT');surface={}
    if {'U10','V10','T2','PSFC'}.issubset(nc.variables):
        a,b=read('U10'),read('V10')
        surface=dict(u=a*ca-b*sa,v=b*ca+a*sa,temperature=read('T2'),pressure=read('PSFC'),w=np.zeros_like(terrain))
        if 'Q2' in nc.variables:
            r=read('Q2');surface['specific_humidity']=np.where(r>=0,r/(1+r),np.nan)
    for array in [*fields.values(),terrain,*surface.values()]:array.setflags(write=False)
    return dict(fields=fields,terrain=terrain,surface=surface,rotation_good=np.asarray(rotation_good))
