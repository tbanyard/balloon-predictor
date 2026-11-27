"""WRF map slices: retained file handles, vector columns and byte-bounded tiles.

This reader is only for displaying native WRF. Flight integration and all
ERA5/IFS/GFS readers are unchanged. No vertical coordinates are invented.
"""
import base64
import copy
import math
from pathlib import Path

import numpy as np
from netCDF4 import Dataset

from .errors import CoverageError, PredictorError
from .provenance import record_file, receipt
from .weather.io import NETCDF_LOCK
from .weather.wrf_rotation import rotation

KEYS = ("u", "v", "temperature", "pressure", "height")
TILE_Y, TILE_X = 64, 128
MAX_CELLS = 262144


def dimensions(model, area, options):
    if not options.wrf_detail:
        return options.grid_size, options.grid_size, 1
    n,w,s,e = area
    dy = float(np.nanmedian(np.hypot(np.diff(model.lat,axis=0), np.diff(model.lon,axis=0)*math.cos(math.radians((n+s)/2)))))
    dx = float(np.nanmedian(np.hypot(np.diff(model.lat,axis=1), np.diff(model.lon,axis=1)*math.cos(math.radians((n+s)/2)))))
    native = max(1e-6, min(dx,dy))
    nx = min(1024, max(8, math.ceil((e-w)*max(.05,math.cos(math.radians((n+s)/2)))/native)+1), max(8,options.viewport_width))
    ny = min(1024, max(8, math.ceil((n-s)/native)+1), max(8,options.viewport_height))
    factor = max(1.,math.sqrt(nx*ny/MAX_CELLS))
    nx,ny = max(8,int(nx/factor)),max(8,int(ny/factor))
    step = max(1, int(min((n-s)/(ny-1)/max(dy,1e-6), (e-w)*math.cos(math.radians((n+s)/2))/(nx-1)/max(dx,1e-6))))
    step = min(step,max(1,min(model.ny,model.nx)//4))
    return ny,nx,step


def geometry(model, latitudes, longitudes, step):
    yy = np.unique(np.r_[np.arange(0,model.ny,step),model.ny-1]).astype(int)
    xx = np.unique(np.r_[np.arange(0,model.nx,step),model.nx-1]).astype(int)
    if step == 1:
        grid = model
    else:
        # One coarse geometry per active WRF run, not an unbounded collection.
        previous = getattr(model,"overlay_geometry",None)
        if previous and previous[0] == step:
            grid = previous[1]
        else:
            grid = copy.copy(model)
            grid.setup_grid(model.lat[np.ix_(yy,xx)],model.lon[np.ix_(yy,xx)])
            model.overlay_geometry = (step,grid)
    lon,lat = np.meshgrid(longitudes,latitudes)
    lat,lon = lat.ravel(),lon.ravel()
    if grid.regular:
        centre = (grid.lons[0]+grid.lons[-1])/2
        lon = centre+(lon-centre+180)%360-180
        valid = (lat>=grid.lats[0]-1e-7)&(lat<=grid.lats[-1]+1e-7)&(lon>=grid.lons[0]-1e-7)&(lon<=grid.lons[-1]+1e-7)
        y1 = np.clip(np.searchsorted(grid.lats,lat,side="right"),1,grid.ny-1);y0=y1-1
        x1 = np.clip(np.searchsorted(grid.lons,lon,side="right"),1,grid.nx-1);x0=x1-1
        fy = np.clip((lat-grid.lats[y0])/(grid.lats[y1]-grid.lats[y0]),0,1)
        fx = np.clip((lon-grid.lons[x0])/(grid.lons[x1]-grid.lons[x0]),0,1)
        ys,xs = np.stack([yy[y0],yy[y0],yy[y1],yy[y1]],1),np.stack([xx[x0],xx[x1],xx[x0],xx[x1]],1)
        weights = np.stack([(1-fy)*(1-fx),(1-fy)*fx,fy*(1-fx),fy*fx],1)
    else:
        lon=grid.reference_lon+(lon-grid.reference_lon+180)%360-180
        points=np.column_stack([lon*grid.scale,lat]);simplex=grid.tri.find_simplex(points)
        valid=(simplex>=0)&grid.valid_triangles[np.maximum(simplex,0)]
        transform=grid.tri.transform[np.maximum(simplex,0)]
        bary=np.einsum('nij,nj->ni',transform[:,:2],points-transform[:,2])
        weights=np.column_stack([bary,1-bary.sum(1)])
        indices=grid.tri.simplices[np.maximum(simplex,0)]
        ys,xs=yy[indices//grid.nx],xx[indices%grid.nx]
    weights=np.where(weights>1e-12,weights,0)
    weights[~valid]=0
    return ys,xs,weights,valid,yy,xx


def plane(fields, surface, altitude, pressure, policy):
    """Vector form of the native column sampler, preserving adjacent holes."""
    shape=fields['height'].shape[1:]
    keys=(*KEYS,'w') if 'w' in fields else KEYS
    f={k:np.asarray(fields[k],dtype=float).reshape(len(fields[k]),-1) for k in keys}
    h=f['height'];nz,n=h.shape;col=np.arange(n)
    ground=np.asarray(surface['terrain'],float).ravel()
    surf={k:np.asarray(surface.get('surface_'+k,np.full(shape,np.nan)),float).ravel() for k in keys if k!='height'}
    if 'w' in keys:surf['w']=np.asarray(surface.get('surface_w',np.zeros(shape)),float).ravel()
    surf['height']=ground+10
    surface_good=np.isfinite(ground)
    for key in ('u','v','temperature','pressure'):surface_good &= np.isfinite(surf[key])
    surface_good &= (surf['temperature']>0)&(surf['pressure']>0)
    if pressure is None:
        order=np.argsort(h,axis=0)
        f={k:np.take_along_axis(a,order,axis=0) for k,a in f.items()};h=f['height']
    else:
        finite=np.isfinite(h);first_h=finite.argmax(0);last_h=nz-1-finite[::-1].argmax(0)
        reverse=h[last_h,col]<h[first_h,col]
        f={k:np.where(reverse[None,:],a[::-1],a) for k,a in f.items()};h=f['height']
    good=np.isfinite(h)&(h>ground[None,:]+.1)
    for key in ('u','v','temperature','pressure'):good &= np.isfinite(f[key])
    good &= (f['temperature']>0)&(f['pressure']>0)
    if pressure is not None:
        sp=surf['pressure'];has_sp=np.isfinite(sp)&(sp>0)
        good &= ~has_sp[None,:] | (f['pressure']<=sp[None,:]+1e-4)
    first=good.argmax(0);last=nz-1-good[::-1].argmax(0)
    first_height=h[first,col]
    insert=surface_good&(first_height>ground+(10 if pressure is None else 10.1))
    # Extra surface row lives at nz; ordinary native indices remain adjacent.
    full={k:np.vstack([a,surf[k]]) for k,a in f.items()}
    good_full=np.vstack([good,insert])
    if pressure is None:
        anchor=np.where(insert,ground+10,first_height)
        depth=np.maximum(0,anchor-altitude);estimated=altitude<anchor-1e-6
        valid=(good.sum(0)>=2)&(altitude<=h[last,col]+.5)
        valid &= ~estimated if policy=='strict' else depth<=3000
        target=np.where(estimated,anchor,altitude)
        j=np.clip((h<target[None,:]).sum(0),first,last)
        exact=np.abs(h[j,col]-target)<=.5
        i=np.maximum(first,j-1);i=np.where(exact,j,i)
        below=target<=first_height
        i=np.where(below,first,i);j=np.where(below,first,j)
        # An inserted surface must also reproduce the half-metre exact rule.
        between=insert&(target<first_height)&~(np.abs(first_height-target)<=.5)
        i=np.where(between,nz,i);j=np.where(between,first,j)
        at_surface=insert&(target<=ground+10)
        i=np.where(at_surface,nz,i);j=np.where(at_surface,nz,j)
        a,b=full['height'][i,col],full['height'][j,col]
        frac=np.divide(target-a,b-a,out=np.zeros(n),where=(b!=a));frac=np.clip(frac,0,1)
    else:
        p=f['pressure'];exact=good&np.isclose(p,pressure,rtol=1e-9,atol=1e-4)
        pairs=good[:-1]&good[1:]&(p[:-1]>pressure)&(p[1:]<pressure)&(h[1:]>h[:-1])
        has_pair=pairs.any(0);i=pairs.argmax(0);j=i+1
        surface_pair=insert&(surf['pressure']>pressure)&(pressure>p[first,col])&(first_height>ground+10)
        i=np.where(~has_pair&surface_pair,nz,i);j=np.where(~has_pair&surface_pair,first,j)
        surface_exact=insert&np.isclose(surf['pressure'],pressure,rtol=1e-9,atol=1e-4)
        has_exact=exact.any(0);idx=exact.argmax(0);i=np.where(has_exact,idx,i);j=np.where(has_exact,idx,j)
        i=np.where(surface_exact,nz,i);j=np.where(surface_exact,nz,j)
        valid=(np.isfinite(h).sum(0)>=2)&(has_pair|surface_pair|has_exact|surface_exact)&(~has_sp|(pressure<=sp+1e-4))
        a,b=full['pressure'][i,col],full['pressure'][j,col]
        with np.errstate(invalid='ignore',divide='ignore'):
            frac=np.divide(np.log(pressure)-np.log(a),np.log(b)-np.log(a),out=np.zeros(n),where=(b!=a))
        frac=np.clip(frac,0,1);estimated=np.zeros(n,bool)
    valid &= good_full[i,col]&good_full[j,col]
    out={}
    for key in keys:
        a,b=full[key][i,col],full[key][j,col]
        with np.errstate(invalid='ignore',divide='ignore',over='ignore'):
            value=np.exp(np.log(a)*(1-frac)+np.log(b)*frac) if key=='pressure' and pressure is None else a*(1-frac)+b*frac
        out[key]=value
    if pressure is None:
        old=out['temperature'].copy();out['temperature']+=np.where(estimated,.0065*depth,0)
        with np.errstate(invalid='ignore',divide='ignore',over='ignore'):
            out['pressure']*=np.where(estimated,(out['temperature']/old)**(9.80665/(287.05*.0065)),1)
        out['height']=np.full(n,altitude)
    else:out['pressure']=np.full(n,pressure)
    for key in KEYS:valid &= np.isfinite(out[key])
    return {**{k:np.where(valid,v,np.nan).astype('float32').reshape(shape) for k,v in out.items()},'estimated':(valid&estimated).astype('float32').reshape(shape)}


def read_plane(nc, model, index, ys, xs, altitude, pressure, policy, include_w=False):
    """Read a spatial tile once. Core fields and optionally W; no unused QVAPOR volumes."""
    def selector(values):
        if len(values)>1 and np.all(np.diff(values)==values[1]-values[0]):
            return slice(int(values[0]),int(values[-1])+1,int(values[1]-values[0]))
        return values
    def read(name, levels=slice(None), dy=0, dx=0):
        var=nc[name];select=[]
        for dim,size in zip(var.dimensions,var.shape):
            if dim=='Time':select.append(index)
            elif dim in ('south_north','south_north_stag'):select.append(selector(ys+dy))
            elif dim in ('west_east','west_east_stag'):select.append(selector(xs+dx))
            elif dim in ('bottom_top','bottom_top_stag'):select.append(levels)
            elif size==1:select.append(0)
            else:raise PredictorError(f'Unsupported WRF dimension {dim}.')
        return np.ma.asarray(var[tuple(select)],dtype='float32').filled(np.nan)
    ca,sa=rotation(nc.__dict__,read('XLAT'),read('XLONG'),read('COSALPHA') if 'COSALPHA' in nc.variables else None,read('SINALPHA') if 'SINALPHA' in nc.variables else None,frame=model.info['provenance'].get('wind_reference'))
    nz=len(nc.dimensions['bottom_top'])
    # Read the vertical coordinate first, then just the levels intersecting the
    # requested surface (plus bottom/top anchors). Unusual/missing columns use
    # the complete core column so their masks/policies remain identical.
    if pressure is None:
        ph=(read('PH').astype(float)+read('PHB'))/9.81
        coordinate=(ph[:-1]+ph[1:])/2
        hit=np.clip((coordinate<altitude).sum(0),1,nz-1)
    else:
        coordinate=(read('P').astype(float)+read('PB'))
        hit=np.clip((coordinate>pressure).sum(0),1,nz-1)
    levels=np.unique(np.r_[0,nz-1,hit.ravel(),(hit-1).ravel()]).astype(int)
    def fields_at(lev):
        u=(read('U',lev)+read('U',lev,dx=1))/2
        v=(read('V',lev)+read('V',lev,dy=1))/2
        p=coordinate[lev] if pressure is not None else (read('P',lev).astype(float)+read('PB',lev))
        if pressure is None:h=coordinate[lev]
        else:
            edges=np.unique(np.r_[lev,lev+1]);ph=(read('PH',edges).astype(float)+read('PHB',edges))/9.81
            h=(ph[np.searchsorted(edges,lev)]+ph[np.searchsorted(edges,lev+1)])/2
        with np.errstate(invalid='ignore'):
            temperature=(read('T',lev).astype(float)+300)*(p/100000)**(287/1004)
        result=dict(u=u*ca-v*sa,v=v*ca+u*sa,temperature=temperature,pressure=p,height=h)
        if include_w:
            if 'W' not in nc.variables:raise PredictorError('Vertical velocity is unavailable in this WRF frame.')
            edges=np.unique(np.r_[lev,lev+1]);w=read('W',edges)
            result['w']=(w[np.searchsorted(edges,lev)]+w[np.searchsorted(edges,lev+1)])/2
        return result
    fields=fields_at(levels)
    surface={'terrain':read('HGT')}
    if {'U10','V10','T2','PSFC'}.issubset(nc.variables):
        u,v=read('U10'),read('V10')
        surface.update(surface_u=u*ca-v*sa,surface_v=v*ca+u*sa,surface_temperature=read('T2'),surface_pressure=read('PSFC'))
    monotonic=np.all(np.diff(coordinate,axis=0)>0 if pressure is None else np.diff(coordinate,axis=0)<0)
    suspicious=not monotonic or any(not np.all(np.isfinite(a)) for a in fields.values()) or np.any(fields['height'][0]<=surface['terrain']+.1) or np.any(fields['temperature']<=0) or np.any(fields['pressure']<=0)
    if suspicious and len(levels)<nz:
        fields=fields_at(np.arange(nz))
    elif len(levels)<nz:
        sparse={k:np.full((nz,len(ys),len(xs)),np.nan) for k in fields}
        for key in fields:sparse[key][levels]=fields[key]
        if pressure is None:sparse['height']=coordinate
        else:sparse['pressure']=coordinate
        fields=sparse
    return plane(fields,surface,altitude,pressure,policy)


def sample(model, when, latitudes, longitudes, altitude, pressure, policy, step, progress, include_w=False):
    with NETCDF_LOCK:
        model.ensure_time(when);i,j,f=model.time_bracket(when)
        times=[(i,1.)] if i==j or f<=1e-12 else [(j,1.)] if f>=1-1e-12 else [(i,1-f),(j,f)]
        ys,xs,weights,valid,yy,xx=geometry(model,latitudes,longitudes,step)
        needed=weights>1e-12
        if not valid.any():raise CoverageError('The visible map is outside this WRF domain.')
        # Tiles are in the sampled native-index lattice, including both faces
        # for staggered velocities. A pan reuses overlapping prepared tiles.
        iy,ix=np.searchsorted(yy,ys),np.searchsorted(xx,xs)
        ty,tx=iy//TILE_Y,ix//TILE_X
        tiles=sorted(set(zip(ty[needed].tolist(),tx[needed].tolist())))
        keys=(*KEYS,'w') if include_w else KEYS
        output={k:np.zeros(len(valid),float) for k in keys};estimated=np.zeros(len(valid),bool)
        for tn,(ti,tw) in enumerate(times):
            _,path,index=model.records[ti];before=receipt(path);record_file(model,path)
            # The scalar check validates fixed geometry once per frame and
            # shares exactly the trajectory reader's moving-grid rejection.
            model.raw_column(ti,int(ys[needed][0]),int(xs[needed][0]))
            node_values={k:np.full(weights.shape,np.nan) for k in (*keys,'estimated')}
            nc=None
            try:
                for num,(a,b) in enumerate(tiles):
                    progress(f'WRF weather · time {tn+1}/{len(times)} · tile {num+1}/{len(tiles)}')
                    key=('wrf-map-v166',include_w,path,before['mtime_ns'],before['size'],index,altitude if pressure is None else None,pressure,policy if pressure is None else None,model.info['provenance'].get('wind_reference'),step,a,b)
                    tile=model.cache.get(key)
                    if tile is None:
                        if nc is None:
                            nc=Dataset(path)
                            # Retain chunk caches for this frame, bounded across
                            # the variables rather than reopening for each block.
                            for var in nc.variables.values():
                                if hasattr(var,'set_var_chunk_cache'):var.set_var_chunk_cache(4*1024**2,1009,.75)
                        y=yy[a*TILE_Y:(a+1)*TILE_Y];x=xx[b*TILE_X:(b+1)*TILE_X]
                        tile=model.cache.put(key,read_plane(nc,model,index,y,x,altitude,pressure,policy,include_w))
                    positions=np.where(needed&(ty==a)&(tx==b))
                    for key in node_values:node_values[key][positions]=tile[key][iy[positions]-a*TILE_Y,ix[positions]-b*TILE_X]
            finally:
                if nc is not None:nc.close()
            if receipt(path)!=before:raise PredictorError('WRF file changed while reading the map. Recalculate the flight.')
            good=np.all(~needed|np.isfinite(node_values['u']),axis=1);valid &= good
            for key in keys:output[key]+=np.sum(np.where(needed,node_values[key],0)*weights,axis=1)*tw
            estimated |= np.any(needed&(node_values['estimated']>0),axis=1)
        shape=(len(latitudes),len(longitudes))
        fields={'temperature_c':output['temperature']-273.15,'u':output['u'],'v':output['v'],'wind_speed':np.hypot(output['u'],output['v']),'altitude_m':output['height']}
        if include_w:fields['w']=output['w']
        return {k:np.where(valid,v,np.nan).reshape(shape) for k,v in fields.items()},valid.reshape(shape),(estimated&valid).reshape(shape)


def encode(fields):
    return {'encoding':'base64-float32-le',**{k:base64.b64encode(np.asarray(v,dtype='<f4').tobytes()).decode('ascii') for k,v in fields.items()}}
