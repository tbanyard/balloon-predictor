"""A process-wide lock for the non-thread-safe NetCDF/HDF5 libraries."""
import threading

NETCDF_LOCK = threading.RLock()
