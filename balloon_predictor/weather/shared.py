"""Read-only shared IFS/GFS normalized NetCDFs; weather volumes remain on disk."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import numpy as np
from netCDF4 import Dataset, num2date
from ..errors import PredictorError
from ..provenance import record_file
from .io import NETCDF_LOCK
from .native import NativeWeather, ColumnCache


def times(nc):
    v = nc['time']
    return np.array([datetime(t.year,t.month,t.day,t.hour,t.minute,t.second,tzinfo=timezone.utc).timestamp()
                     for t in num2date(v[:],v.units,calendar=getattr(v,'calendar','standard'),only_use_cftime_datetimes=False)])


class SharedWeather(NativeWeather):
    def __init__(self, path, info, cache, max_gap):
        self.source_path, self.info, self.cache, self.max_gap = path, dict(info), cache, max_gap
        self.source_receipts = {}
        record_file(self,path)
        with Dataset(path) as nc:
            self.times = times(nc)
            self.records = [(stamp,str(path),i) for i,stamp in enumerate(self.times)]
            self.levels = np.asarray(nc['level'][:])
            self.setup_grid(np.asarray(nc['latitude'][:]),np.asarray(nc['longitude'][:]))

    def raw_column(self, ti, y, x):
        path = self.source_path
        receipt = record_file(self,path)
        stamp = path.stat()
        key = ('shared',str(path),stamp.st_mtime_ns,stamp.st_size,ti,y,x)
        cached = self.cache.get(key)
        if cached is not None:
            return cached
        with Dataset(path) as nc:
            def read(v):
                return np.ma.asarray(v[tuple(ti if d=='time' else y if d=='y' else x if d=='x' else slice(None) for d in v.dimensions)],dtype=float).filled(np.nan)
            raw = {k:read(nc[k]) for k in ('height','u','v','temperature','pressure','w','specific_humidity') if k in nc.variables}
            raw['terrain'] = float(read(nc['terrain'])) if 'terrain' in nc.variables else -10000.
            keys = {'u':'surface_u','v':'surface_v','temperature':'surface_temperature','pressure':'surface_pressure','specific_humidity':'surface_specific_humidity'}
            surface = {k:float(read(nc[v])) for k,v in keys.items() if v in nc.variables}
            if self.info['terrain_available'] and all(k in surface for k in ('u','v','temperature','pressure')):
                raw['surface'] = {**surface,'w':0.}
        record_file(self,path)
        return self.cache.put(key,raw)

    def terrain(self, lat, lon):
        if not self.info['terrain_available']:
            raise PredictorError('This shared forecast has no model terrain. Choose a known elevation or physical DEM.')
        with NETCDF_LOCK:
            return sum(self.raw_column(0,y,x)['terrain']*w for y,x,w in self.spatial_weights(lat,lon))


class SharedCatalog:
    def __init__(self, settings):
        self.settings = settings
        self.scan()

    def scan(self):
        self.entries, self.messages, self.loaded = {}, [], {}
        self.cache = ColumnCache(self.settings.cache_mb)
        root = self.settings.common_weather_root
        if not root:
            return
        with NETCDF_LOCK:
            for source in ('ifs','gfs'):
                folder = root/source
                if not folder.is_dir():
                    continue
                for path in sorted(folder.rglob('*.nc')):
                    if not path.resolve().is_relative_to(folder.resolve()) or path.name.endswith('.tmp.nc'):
                        continue
                    try:
                        with Dataset(path) as nc:
                            required = ('time','level','u','v','height','temperature','pressure','latitude','longitude')
                            if any(k not in nc.variables for k in required) or getattr(nc,'schema_version',None)!=1:
                                raise PredictorError('Use BalloonPredictor normalized NetCDF (import-ifs/import-gfs), with earth-relative winds and AMSL heights.')
                            if getattr(nc,'wind_reference',None)!='earth' or getattr(nc,'height_reference',None)!='AMSL':
                                raise PredictorError('Expected wind_reference=earth and height_reference=AMSL.')
                            stamps = times(nc)
                            if not len(stamps) or np.any(np.diff(stamps)<=0):
                                raise PredictorError('Times must be unique and increasing.')
                            if any(len(nc.dimensions.get(k,[]))<2 for k in ('level','y','x')) or any(nc[k].dimensions!=('time','level','y','x') for k in ('u','v','height','temperature','pressure')):
                                raise PredictorError('Expected normalized time/level/y/x dimensions.')
                            lat,lon=np.asarray(nc['latitude'][:]),np.asarray(nc['longitude'][:])
                            ident=source+'-shared-'+hashlib.sha256(str(path.relative_to(folder)).encode()).hexdigest()[:12]
                            provenance=json.loads(getattr(nc,'provenance_json','{}'))
                            provenance.update(file=str(path),shared=True)
                            info={'id':ident,'label':getattr(nc,'label',path.stem)+' · shared','source':source,'shared':True,'read_only':True,'native':False,'available':True,
                                  'start':datetime.fromtimestamp(stamps[0],timezone.utc).isoformat().replace('+00:00','Z'),'end':datetime.fromtimestamp(stamps[-1],timezone.utc).isoformat().replace('+00:00','Z'),
                                  'area':[float(lat.max()),float(lon.min()),float(lat.min()),float(lon.max())],'minimum_top_altitude':None,'maximum_altitude':None,
                                  'terrain_available':'terrain' in nc.variables,'vertical_wind_available':'w' in nc.variables,'humidity_available':'specific_humidity' in nc.variables,
                                  'warnings':json.loads(getattr(nc,'warnings_json','[]')),'provenance':provenance}
                        self.entries[ident]=(path,info)
                    except (OSError,ValueError,RuntimeError,PredictorError,KeyError) as error:
                        self.messages.append(f'{source}/{path.name}: {error}')

    def list(self):
        return [info for _,info in self.entries.values()]

    def get(self,name,when=None):
        with NETCDF_LOCK:
            if name not in self.loaded:
                path,info=self.entries[name]
                self.loaded[name]=SharedWeather(path,info,self.cache,self.settings.max_time_gap_hours*3600)
            weather=self.loaded[name]
            if when:
                weather.time_bracket(when)
            return weather
