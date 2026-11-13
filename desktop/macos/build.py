"""Build one relocatable, native Intel + Apple silicon macOS disk image."""
import hashlib
import json
from pathlib import Path
import plistlib
import re
import shutil
import subprocess
import sys
import tarfile
from tempfile import TemporaryDirectory
import tomllib
from urllib.request import urlopen

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
VERSION = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
MIN_MACOS = "15.0"
PYTHON_VERSION = "3.13.18"
PYTHON_RELEASE = "20261110"
RUNTIMES = {
    "arm64": ("aarch64", "7a2384641584ab9984221fcb0fc9265df465922af71be71410529e2a9c3f0540"),
    "x86_64": ("x86_64", "d27d59a722db9f69926eb6d9564071247221f45a04da920ea7f3001c97e32f9b"),
}
MACHO_MAGIC = {
    bytes.fromhex(x) for x in ("feedface", "feedfacf", "cefaedfe", "cffaedfe", "cafebabe", "cafebabf")
}


def run(*args, **kwargs):
    return subprocess.run(args, check=True, **kwargs)


def sha256(path):
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def stage_runtime(arch, destination, work):
    target, expected = RUNTIMES[arch]
    name = f"cpython-{PYTHON_VERSION}%2B{PYTHON_RELEASE}-{target}-apple-darwin-install_only_stripped.tar.gz"
    url = f"https://github.com/astral-sh/python-build-standalone/releases/download/{PYTHON_RELEASE}/{name}"
    archive = work / f"{arch}.tar.gz"
    with urlopen(url, timeout=60) as response, archive.open("wb") as output:
        shutil.copyfileobj(response, output)
    if sha256(archive) != expected:
        raise RuntimeError(f"The {arch} CPython archive did not match its published SHA-256.")
    unpack = work / f"unpack-{arch}"
    with tarfile.open(archive) as file:
        file.extractall(unpack, filter="data")
    shutil.move(unpack / "python", destination)
    site = destination / "lib" / "python3.13" / "site-packages"
    lock = HERE / f"requirements-{arch}.txt"
    run(
        sys.executable, "-m", "pip", "install", "--require-hashes", "--no-deps", "--no-compile",
        "--only-binary=:all:", "--platform", f"macosx_15_0_{arch}", "--implementation", "cp",
        "--python-version", PYTHON_VERSION, "--abi", "cp313",
        "--target", str(site), "-r", str(lock),
    )
    # pip's generated scripts refer to the build interpreter. The app uses
    # its own private Python executable and never needs these scripts.
    if (site / "bin").is_dir():
        shutil.rmtree(site / "bin")
    return {"python_archive_sha256": expected, "requirements_sha256": sha256(lock)}


def native_files(root):
    for path in root.rglob("*"):
        if path.is_file() and not path.is_symlink():
            with path.open("rb") as source:
                if source.read(4) in MACHO_MAGIC:
                    yield path


def verify_minimum(path, arch):
    result = run("otool", "-arch", arch, "-l", str(path), capture_output=True, text=True)
    # New and older Mach-O load commands encode the minimum OS differently.
    minimums = re.findall(
        r"\bcmd LC_BUILD_VERSION\s+cmdsize \d+\s+platform \S+\s+minos ([\d.]+)",
        result.stdout,
    ) + re.findall(
        r"\bcmd LC_VERSION_MIN_MACOSX\s+cmdsize \d+\s+version ([\d.]+)", result.stdout,
    )
    if not minimums:
        raise RuntimeError(f"No macOS deployment target for {arch}: {path}")
    for minimum in minimums:
        actual = tuple(int(x) for x in minimum.split(".")) + (0, 0)
        if actual[:3] > (15, 0, 0):
            raise RuntimeError(f"{path.name} ({arch}) needs macOS {minimum}; the app targets {MIN_MACOS}.")
    loads = re.findall(
        r"\bcmd LC_(?:LOAD|LOAD_WEAK|REEXPORT|LOAD_UPWARD|LAZY_LOAD)_DYLIB\s+"
        r"cmdsize \d+\s+name (.*?) \(offset \d+\)", result.stdout,
    )
    for load in loads:
        if load.startswith("/") and not load.startswith(("/usr/lib/", "/System/Library/")):
            raise RuntimeError(f"{path.name} ({arch}) links to an unbundled library: {load}")


def sign_app(app, executable, runtime):
    run("lipo", str(executable), "-verify_arch", *RUNTIMES)
    for arch in RUNTIMES:
        verify_minimum(executable, arch)
        for path in native_files(runtime / arch):
            verify_minimum(path, arch)
            run("codesign", "--force", "--sign", "-", "--timestamp=none", str(path))
    # Ad hoc signatures make the bundle executable on Apple silicon. The
    # test build does not use a Developer ID or claim to be notarized.
    run("codesign", "--force", "--sign", "-", "--timestamp=none", str(app))
    run("codesign", "--verify", "--deep", "--strict", "--verbose=2", str(app))


def build(output):
    if sys.platform != "darwin":
        raise SystemExit("Build the Mac disk image on a macOS runner with Xcode command-line tools.")
    output.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="BalloonPredictor macOS build ") as directory:
        work = Path(directory)
        image = work / "image"
        app = image / "BalloonPredictor.app"
        contents = app / "Contents"
        resources = contents / "Resources"
        source = resources / "app"
        runtime = resources / "runtime"
        runtime.mkdir(parents=True)
        executable = contents / "MacOS" / "BalloonPredictor"
        executable.parent.mkdir()
        run(
            "xcrun", "--sdk", "macosx", "clang", "-arch", "arm64", "-arch", "x86_64",
            f"-mmacosx-version-min={MIN_MACOS}", "-fobjc-arc", "-O2",
            "-framework", "Cocoa", "-framework", "WebKit", "-framework", "QuartzCore", str(HERE / "launcher.m"),
            "-o", str(executable),
        )
        info = {
            "CFBundleDevelopmentRegion": "en",
            "CFBundleExecutable": "BalloonPredictor",
            "CFBundleName": "BalloonPredictor",
            "CFBundleDisplayName": "BalloonPredictor",
            "CFBundleIdentifier": "io.github.tbanyard.balloonpredictor",
            "CFBundleIconFile": "BalloonPredictor.icns",
            "CFBundleInfoDictionaryVersion": "6.0",
            "CFBundlePackageType": "APPL",
            "CFBundleShortVersionString": VERSION,
            "CFBundleVersion": VERSION,
            "CFBundleSupportedPlatforms": ["MacOSX"],
            "LSMinimumSystemVersion": MIN_MACOS,
            "NSHighResolutionCapable": True,
            "NSAppTransportSecurity": {
                # Explicit HTTP consent in the chooser; native navigation is origin limited.
                "NSAllowsArbitraryLoadsInWebContent": True
            },
        }
        (contents / "Info.plist").write_bytes(plistlib.dumps(info))
        shutil.copy2(HERE / "BalloonPredictor.icns", resources / "BalloonPredictor.icns")

        for name in ("balloon_predictor", "frontend"):
            shutil.copytree(REPO / name, source / name, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        for name in ("LICENSE", "THIRD_PARTY_NOTICES.md"):
            shutil.copy2(REPO / name, source / name)
        (source / "desktop" / "macos").mkdir(parents=True)
        for name in ("launcher.py", "smoke.py", "connection.py", "host.py"):
            shutil.copy2(HERE.parent / name, source / "desktop" / name)
        shutil.copytree(HERE.parent / "connect-ui", source / "desktop/connect-ui")
        runtime_info = {arch: stage_runtime(arch, runtime / arch, work) for arch in RUNTIMES}
        manifest = {
            "app_version": VERSION,
            "source_commit": run("git", "rev-parse", "HEAD", cwd=REPO, capture_output=True, text=True).stdout.strip(),
            "minimum_macos": MIN_MACOS,
            "python": PYTHON_VERSION,
            "runtimes": runtime_info,
            "signing": "ad hoc, not notarized",
        }
        (resources / "build-info.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        sign_app(app, executable, runtime)
        (image / "Applications").symlink_to("/Applications")
        shutil.copy2(HERE / "MACOS.md", image / "README-macOS.md")
        dmg = output / f"BalloonPredictor-{VERSION}-macOS-Universal.dmg"
        run("hdiutil", "create", "-ov", "-volname", "BalloonPredictor",
            "-srcfolder", str(image), "-fs", "HFS+", "-format", "UDZO", str(dmg))
        shutil.copy2(HERE / "MACOS.md", output / "README-macOS.md")
        shutil.copy2(resources / "build-info.json", output / "build-info.json")
        (output / "SHA256SUMS.txt").write_text(f"{sha256(dmg)}  {dmg.name}\n", encoding="ascii")
        print(f"Built {dmg}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python desktop/macos/build.py OUTPUT_DIRECTORY")
    build(Path(sys.argv[1]).resolve())
