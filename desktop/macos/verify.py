"""Mount the exact candidate DMG and test a copied app on this Mac."""
import json
import os
from pathlib import Path
import platform
import shutil
import sys
from tempfile import TemporaryDirectory

from build import run, sha256


def verify(dist, expected_arch, report):
    if sys.platform != "darwin" or platform.machine() != expected_arch:
        raise RuntimeError(f"Expected a native {expected_arch} macOS runner.")
    [dmg] = dist.glob("BalloonPredictor-*-macOS-Universal.dmg")
    manifest = json.loads((dist / "build-info.json").read_text(encoding="utf-8"))
    checksum = (dist / "SHA256SUMS.txt").read_text(encoding="ascii").strip()
    if checksum != f"{sha256(dmg)}  {dmg.name}":
        raise RuntimeError("The disk image no longer matches the build checksum.")
    run("hdiutil", "verify", str(dmg))
    with TemporaryDirectory(prefix="BalloonPredictor Clean Mac Install ") as directory:
        root = Path(directory)
        mount = root / "Disk Image"
        mount.mkdir()
        installed = root / "Installed Apps" / "BalloonPredictor.app"
        installed.parent.mkdir()
        data = root / "User Data"
        run("hdiutil", "attach", "-readonly", "-nobrowse", "-noautoopen", "-mountpoint", str(mount), str(dmg))
        try:
            assert (mount / "Applications").is_symlink()
            run("ditto", str(mount / "BalloonPredictor.app"), str(installed))
        finally:
            run("hdiutil", "detach", str(mount))

        run("codesign", "--verify", "--deep", "--strict", "--verbose=2", str(installed))
        env = {
            k: v for k, v in os.environ.items()
            if k not in {"PATH", "VIRTUAL_ENV", "CONDA_PREFIX", "PYTHONHOME", "PYTHONPATH"}
            and not k.startswith("DYLD_")
        }
        env.update({
            "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
            "PYTHONHOME": str(root / "Nonexistent Python"),
            "PYTHONPATH": str(root / "Nonexistent Packages"),
        })
        try:
            run(str(installed / "Contents/MacOS/BalloonPredictor"), "--smoke-test", str(data),
                cwd=root, env=env, timeout=175)
            result = data / "smoke-result.json"
            check = json.loads(result.read_text(encoding="utf-8"))
            if check["architecture"] != expected_arch or check["python"] != manifest["python"]:
                raise RuntimeError("The app did not use the expected bundled runtime.")
            if not all(check[key].startswith("passed:") for key in ("engine", "window", "download")):
                raise RuntimeError("A test did not pass.")
            report.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(result, report)
            print(json.dumps(check, indent=2))
        except Exception:
            log = data / "BalloonPredictor.log"
            if log.is_file():
                print(log.read_text(encoding="utf-8", errors="replace")[-12000:])
            raise


if __name__ == "__main__":
    if len(sys.argv) != 4:
        raise SystemExit("Usage: python desktop/macos/verify.py DIST_DIRECTORY ARCH REPORT")
    verify(Path(sys.argv[1]).resolve(), sys.argv[2], Path(sys.argv[3]).resolve())
