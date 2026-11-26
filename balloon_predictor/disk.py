"""Storage diagnostics which never include provider credentials or URLs."""
import errno
import os
from pathlib import Path
import shutil
import re
from tempfile import NamedTemporaryFile


def os_account():
    if os.name == "posix":
        import pwd
        return pwd.getpwuid(os.geteuid()).pw_name
    import getpass
    return getpass.getuser()


SHARED_DATA_ROOT = Path("/data1/tim/BP_Data")


def personal_data_dir(root, username=None):
    from .errors import PredictorError
    name = os_account() if username is None else username
    if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.@-]*\$?", name) or name.lower() in (".", "..", "wrf", "ifs", "gfs"):
        raise PredictorError("Cannot use this Unix account name for a personal data folder.")
    target = Path(root).expanduser().resolve() / name
    if target.is_symlink():
        raise PredictorError(f"The personal data folder must not be a symbolic link: {target}")
    if target.exists() and os.name == "posix" and target.stat().st_uid != os.geteuid():
        raise PredictorError(f"The personal data folder is not owned by this server process: {target}")
    return target


def storage_failure(error, directory):
    seen = set()
    while error is not None and id(error) not in seen:
        seen.add(id(error))
        code = getattr(error, "errno", None)
        reason = {
            errno.ENOSPC: "The disk is full or its available inodes have been exhausted",
            getattr(errno, "EDQUOT", 122): "This account's disk quota has been exceeded",
            errno.EACCES: "This account does not have permission to access the file or folder",
            errno.EPERM: "This account is not permitted to access the file or folder",
            errno.EROFS: "The filesystem is read-only",
        }.get(code)
        if reason:
            name = errno.errorcode.get(code, str(code))
            filename = getattr(error, "filename", None)
            affected = str(directory)
            if isinstance(filename, str) and Path(filename).is_absolute() and "://" not in filename:
                affected = filename
            return {"code": "storage_error", "errno": code, "errno_name": name,
                    "data_dir": str(directory), "affected_path": affected,
                    "message": f"{reason}: {affected} ({name}). Check space, quota and permissions. Data folder: {directory}. Then retry."}
        error = error.__cause__ or error.__context__
    return None


def check_writable(directory):
    # Actually allocating a small file detects per-account quotas which
    # shutil.disk_usage cannot measure on a shared filesystem.
    with NamedTemporaryFile(dir=directory, prefix=".bp-write-check-") as output:
        output.write(b"BalloonPredictor storage check\n")
        output.flush()
        os.fsync(output.fileno())


def disk_status(directory, *, probe=False):
    directory = Path(directory)
    result = {"account": os_account(), "data_dir": str(directory), "ok": True,
              "note": "Free space is for the filesystem. Your account may have a smaller disk quota."}
    try:
        usage = shutil.disk_usage(directory)
        result.update(filesystem_free_bytes=usage.free, filesystem_total_bytes=usage.total,
                      low_space=usage.free < 128 * 1024**2)
        if probe:
            check_writable(directory)
    except OSError as error:
        detail = storage_failure(error, directory)
        result.update(ok=False, **(detail or {"message": "Could not check the data folder. Check its path and permissions."}))
    return result
