"""Build a per-user Windows x64 Setup.exe from the repository sources."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys
from tempfile import TemporaryDirectory
import tomllib
from urllib.request import urlopen
from zipfile import ZipFile

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
PYTHON_VERSION = "3.13.16"
PYTHON_SHA256 = "97dae5274cc54867065e8d5a3226e48c35017ed332a0fdb0e27d5b5821961297"
# Exact Microsoft Evergreen bootstrapper, checked against Microsoft's signing
# chain and timestamp. The fwlink is for discovering updates; its payload changes.
# To update this pin, verify the new Authenticode signature and record the resolved
# Microsoft CDN URL and whole-file SHA-256 together.
WEBVIEW_URL = (
    "https://msedge.sf.dl.delivery.mp.microsoft.com/filestreamingservice/files/"
    "068be74d-a831-494f-8f57-c173756d5a33/MicrosoftEdgeWebview2Setup.exe"
)
WEBVIEW_SHA256 = "cf2f2445fef0c5c14e372045f84998771eeb8bb01d59be4afd00692fa160082e"
VERSION = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]


def run(*args, **kwargs):
    return subprocess.run(args, check=True, **kwargs)


def download(url, path):
    with urlopen(url, timeout=60) as response, path.open("wb") as output:
        shutil.copyfileobj(response, output)


def sha256(path):
    with path.open("rb") as file:
        return hashlib.file_digest(file, "sha256").hexdigest()


def fetch_webview_bootstrapper(path):
    download(WEBVIEW_URL, path)
    digest = sha256(path)
    if digest != WEBVIEW_SHA256:
        path.unlink(missing_ok=True)
        raise RuntimeError(
            f"Microsoft WebView2 bootstrapper SHA-256 mismatch: expected {WEBVIEW_SHA256}, got {digest}."
        )
    print(f"Verified Microsoft WebView2 bootstrapper SHA-256: {digest}", flush=True)


def copy_msvc_runtime(runtime):
    # Include Microsoft's app-local redistributable DLLs. A hosted runner
    # has MSVCP already; a clean user's PC may not.
    vswhere = Path(os.environ["ProgramFiles(x86)"]) / "Microsoft Visual Studio/Installer/vswhere.exe"
    installs = run(
        str(vswhere), "-products", "*", "-latest", "-property", "installationPath",
        capture_output=True, text=True,
    ).stdout.strip()
    candidates = list((Path(installs) / "VC/Redist/MSVC").glob("*/x64/Microsoft.VC*.CRT"))
    if not candidates:
        raise RuntimeError("The Visual Studio x64 C++ redistributable files are missing.")
    crt = max(candidates, key=lambda path: tuple(map(int, re.findall(r"\d+", path.parents[1].name))))
    dlls = list(crt.glob("*.dll"))
    if not {"msvcp140.dll", "vcruntime140.dll", "vcruntime140_1.dll"} <= {p.name.lower() for p in dlls}:
        raise RuntimeError("The Visual Studio runtime directory is incomplete.")
    for dll in dlls:
        shutil.copy2(dll, runtime / dll.name)
    for openmp in crt.parent.glob("Microsoft.VC*.OpenMP/vcomp140.dll"):
        shutil.copy2(openmp, runtime / openmp.name)
    return crt.parents[1].name


def stage(payload, work, source_commit):
    runtime, app = payload / "runtime", payload / "app"
    runtime.mkdir(parents=True)
    app.mkdir()
    archive = work / "python-embed.zip"
    download(f"https://www.python.org/ftp/python/{PYTHON_VERSION}/python-{PYTHON_VERSION}-embed-amd64.zip", archive)
    if sha256(archive) != PYTHON_SHA256:
        raise RuntimeError("The CPython embeddable package did not match the published SHA-256.")
    with ZipFile(archive) as zip_file:
        zip_file.extractall(runtime)
    msvc = copy_msvc_runtime(runtime)
    # _pth mode isolates the desktop app from PATH, PYTHONHOME, Conda and
    # user Python packages. Source paths are relative to the installed runtime.
    (runtime / "python313._pth").write_text(
        "python313.zip\n.\nLib\\site-packages\n..\\app\nimport site\n", encoding="ascii",
    )
    lock = HERE / "requirements-win-x64.txt"
    run(
        sys.executable, "-m", "pip", "install", "--require-hashes", "--no-deps", "--no-compile",
        "--only-binary=:all:", "--no-binary=proxy-tools", "--no-build-isolation",
        "--target", str(runtime / "Lib/site-packages"), "-r", str(lock),
    )
    for name in ("balloon_predictor", "frontend"):
        shutil.copytree(REPO / name, app / name, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    for name in ("LICENSE", "THIRD_PARTY_NOTICES.md"):
        shutil.copy2(REPO / name, app / name)
    (app / "desktop/windows").mkdir(parents=True)
    for name in ("launcher.py", "smoke.py"):
        shutil.copy2(HERE.parent / name, app / "desktop" / name)
    shutil.copy2(HERE / "BalloonPredictor.ico", app / "desktop/windows/BalloonPredictor.ico")
    shutil.copy2(HERE / "WINDOWS.md", payload / "README-Windows.md")
    manifest = {
        "app_version": VERSION,
        "source_commit": source_commit,
        "python": PYTHON_VERSION,
        "python_sha256": PYTHON_SHA256,
        "webview2_bootstrapper_sha256": WEBVIEW_SHA256,
        "windows_requirements_sha256": sha256(lock),
        "msvc_runtime": msvc,
        "architecture": "x64",
    }
    (payload / "build-info.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


def build(output):
    if sys.platform != "win32" or struct.calcsize("P") != 8:
        raise SystemExit("Run this build with 64-bit CPython on Windows.")
    if sys.version.split()[0] != PYTHON_VERSION:
        raise SystemExit(f"Use CPython {PYTHON_VERSION} to match the bundled runtime.")
    if not re.fullmatch(r"\d+\.\d+\.\d+", VERSION):
        raise SystemExit("The installer requires a numeric major.minor.patch app version.")
    iscc = Path(os.environ["ProgramFiles(x86)"]) / "Inno Setup 6/ISCC.exe"
    if not iscc.is_file():
        raise SystemExit("Inno Setup 6.3 or later is required at the runner's standard install path.")
    source_commit = run("git", "rev-parse", "HEAD", cwd=REPO, capture_output=True, text=True).stdout.strip()
    output.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="BalloonPredictor Build ") as temp:
        work = Path(temp)
        bootstrapper = work / "MicrosoftEdgeWebview2Setup.exe"
        fetch_webview_bootstrapper(bootstrapper)
        payload = work / "payload"
        stage(payload, work, source_commit)
        run(
            str(iscc), f"/DAppVersion={VERSION}", f"/DPayload={payload}",
            f"/DBootstrapper={bootstrapper}", f"/O{output}", "/Qp",
            str(HERE / "installer.iss"),
        )
        shutil.copy2(payload / "build-info.json", output / "build-info.json")
        shutil.copy2(HERE / "WINDOWS.md", output / "README-Windows.md")
    installer = output / f"BalloonPredictor-{VERSION}-Windows-x64-Setup.exe"
    (output / "SHA256SUMS.txt").write_text(f"{sha256(installer)}  {installer.name}\n", encoding="ascii")
    print(f"Built {installer}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python desktop/windows/build.py OUTPUT_DIRECTORY")
    build(Path(sys.argv[1]).resolve())
