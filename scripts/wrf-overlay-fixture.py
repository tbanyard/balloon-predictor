"""Synthetic 1 km WRF fixture for map read/cache regression; no downloads."""
from pathlib import Path
import sys
import numpy as np
from netCDF4 import Dataset


def create(path, ny=192, nx=224, nz=60):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    with Dataset(path,'w') as nc:
        for key,size in [('Time',2),('DateStrLen',19),('bottom_top',nz),('bottom_top_stag',nz+1),('south_north',ny),('west_east',nx),('south_north_stag',ny+1),('west_east_stag',nx+1)]:nc.createDimension(key,size)
        nc.parent_cycle_utc='2024-01-15T00:00:00Z';nc.balloon_wind_reference='earth';nc.DX=1000.;nc.DY=1000.;nc.GRID_ID=2
        nc.createVariable('Times','S1',('Time','DateStrLen'))[:]=np.array([list(t) for t in ['2024-01-15_12:00:00','2024-01-15_18:00:00']],dtype='S1')
        def variable(name,dims,values):
            shape=tuple(len(nc.dimensions[d]) for d in dims)
            chunks=tuple(1 if d=='Time' else min(n,8 if d.startswith('bottom_top') else 32) for d,n in zip(dims,shape))
            var=nc.createVariable(name,'f4',dims,zlib=True,complevel=1,chunksizes=chunks)
            var[:]=np.broadcast_to(values,shape)
        grid=('Time','south_north','west_east');mass=('Time','bottom_top','south_north','west_east')
        y=np.arange(ny)[None,:,None];x=np.arange(nx)[None,None,:]
        terrain=80+0.2*y+0.1*x
        variable('XLAT',grid,52+y*.009);variable('XLONG',grid,-5+x*.0145);variable('HGT',grid,terrain)
        for name,value in [('U10',4.),('V10',1.),('T2',280.),('PSFC',101000.)]:variable(name,grid,value)
        half=np.linspace(0,36000,nz+1)[None,:,None,None]
        h=(half[:,:-1]+half[:,1:])/2
        variable('PH',('Time','bottom_top_stag','south_north','west_east'),(half+terrain[:,None])*9.81)
        variable('PHB',('Time','bottom_top_stag','south_north','west_east'),0.)
        pressure=101000*np.exp(-h/7000)
        variable('P',mass,pressure);variable('PB',mass,0.)
        actual=280-np.minimum(h,11000)*.006+np.maximum(0,h-22000)*.002
        variable('T',mass,actual/(pressure/100000)**(287/1004)-300)
        variable('U',('Time','bottom_top','south_north','west_east_stag'),5+np.arange(nz)[None,:,None,None]*.2)
        variable('V',('Time','bottom_top','south_north_stag','west_east'),2.)
        variable('W',('Time','bottom_top_stag','south_north','west_east'),.4*np.sin(half/6000))
    return path


if __name__=='__main__':create(sys.argv[1])
