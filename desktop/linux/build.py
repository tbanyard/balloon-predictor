"""Build the Debian 12+ amd64 desktop package with an isolated Python runtime."""
import argparse
import hashlib
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
import tomllib
from urllib.request import urlopen

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
VERSION = tomllib.loads((REPO / "pyproject.toml").read_text())["project"]["version"]
RELEASE, PYTHON = "20261110", "3.13.18"
RUNTIME_HASH = "50c774f6674740739f791c05a9177ed03e2fe02abebda9bca3512eb7309b37cc"


def digest(path):
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def run(*args):
    subprocess.run([str(a) for a in args], check=True)


def write(path, value, executable=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")
    if executable:
        path.chmod(0o755)


def build(output, work, wheels):
    if platform.system() != "Linux" or platform.machine() != "x86_64":
        raise RuntimeError("Build on x86-64 Linux.")
    output.mkdir(parents=True, exist_ok=True)
    stage = work / "package"
    dest = stage / "opt/balloon-predictor"
    dest.mkdir(parents=True)
    archive = work / "python.tar.gz"
    url = f"https://github.com/astral-sh/python-build-standalone/releases/download/{RELEASE}/cpython-{PYTHON}%2B{RELEASE}-x86_64-unknown-linux-gnu-install_only.tar.gz"
    with urlopen(url, timeout=60) as source, archive.open("wb") as file:
        shutil.copyfileobj(source, file)
    if digest(archive) != RUNTIME_HASH:
        raise RuntimeError("The Linux CPython archive failed its SHA-256 check.")
    with tarfile.open(archive) as source:
        source.extractall(work, filter="data")
    shutil.move(work / "python", dest / "runtime")
    site = dest / "runtime/lib/python3.13/site-packages"
    tags = [*(f"manylinux_2_{n}_x86_64" for n in range(36,16,-1)), "manylinux2014_x86_64", "manylinux2010_x86_64", "manylinux1_x86_64"]
    run(sys.executable, "-m", "pip", "install", "--require-hashes", "--no-deps", "--no-compile",
        "--only-binary=:all:", "--implementation", "cp", "--python-version", PYTHON, "--abi", "cp313",
        *(arg for tag in tags for arg in ("--platform", tag)), "-r", HERE / "requirements-amd64.txt",
        "--target", site, *(("--no-index", "--find-links", wheels) if wheels else ()))
    shutil.rmtree(site / "bin", ignore_errors=True)
    for name in ("balloon_predictor", "frontend"):
        shutil.copytree(REPO / name, dest / name, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    (dest / "desktop/linux").mkdir(parents=True)
    for name in ("launcher.py", "connection.py", "host.py", "smoke.py"):
        shutil.copy2(HERE.parent / name, dest / "desktop" / name)
    shutil.copy2(HERE / "launcher.py", dest / "desktop/linux/launcher.py")
    shutil.copytree(HERE.parent / "connect-ui", dest / "desktop/connect-ui")
    for name in ("LICENSE", "THIRD_PARTY_NOTICES.md"):
        shutil.copy2(REPO / name, dest / name)
    write(stage / "DEBIAN/control", f"""Package: balloon-predictor
Version: {VERSION}
Section: science
Priority: optional
Architecture: amd64
Maintainer: BalloonPredictor contributors
Depends: python3 (>= 3.11), python3-gi, gir1.2-gtk-3.0, gir1.2-webkit2-4.1, libstdc++6, ca-certificates
Homepage: https://github.com/tbanyard/balloon-predictor
Description: Balloon trajectories and weather data in a desktop app
 Run the predictor locally or connect to a BalloonPredictor server by web or SSH.
""")
    write(stage / "usr/bin/balloon-predictor", '#!/bin/sh\nexec /usr/bin/python3 -I -B /opt/balloon-predictor/desktop/linux/launcher.py "$@"\n', True)
    write(stage / "usr/share/applications/balloon-predictor.desktop", """[Desktop Entry]
Type=Application
Name=BalloonPredictor
Comment=Predict atmospheric balloon trajectories
Exec=balloon-predictor
Icon=balloon-predictor
Terminal=false
Categories=Science;Education;
""")
    icon = stage / "usr/share/icons/hicolor/scalable/apps/balloon-predictor.svg"
    icon.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(REPO / "frontend/favicon.svg", icon)
    package = output / f"BalloonPredictor-{VERSION}-Debian-amd64.deb"
    run("dpkg-deb", "--root-owner-group", "--build", "-Zxz", "-z3", stage, package)
    write(output / "SHA256SUMS.txt", f"{digest(package)}  {package.name}\n")
    shutil.copy2(REPO / "docs/V1.5.md", output / "README-Linux.md")
    print(package)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--wheelhouse", type=Path)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="balloon-debian-") as work:
        build(args.output.resolve(), Path(work), args.wheelhouse)
