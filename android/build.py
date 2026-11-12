#!/usr/bin/env python3
"""Build and verify the APK using JDK 17, Android 36, and build-tools 35.0.0."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import tomllib
from urllib.request import urlopen
from zipfile import ZipFile, ZIP_DEFLATED

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
SOURCE = HERE / "app/src/main"
VERSION = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
JARS = {
    "jsch": ("com/github/mwiede/jsch/2.28.8/jsch-2.28.8.jar", "2afca1357d816028f2ac33f22407d2488a68406682973949cebb1b8ea6688a8b"),
    "bcprov": ("org/bouncycastle/bcprov-jdk18on/1.86/bcprov-jdk18on-1.86.jar", "2af190b300cbb0b35e248ccf5f4a06b6072030aeb3da7a98ec73abe5b4cb371f"),
}


def run(*args, **kw):
    subprocess.run([str(a) for a in args], check=True, **kw)


def digest(path):
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def libraries(work):
    assets = work / "assets"
    assets.mkdir()
    outputs = []
    for name, (url, expected) in JARS.items():
        original = work / f"{name}-download.jar"
        with urlopen("https://repo.maven.apache.org/maven2/" + url, timeout=60) as source, original.open("wb") as dest:
            shutil.copyfileobj(source, dest)
        if digest(original) != expected:
            raise RuntimeError(f"Invalid checksum for Android dependency {name}")
        output = work / f"{name}.jar"
        with ZipFile(original) as source, ZipFile(output, "w", ZIP_DEFLATED) as dest:
            for item in source.namelist():
                if item.endswith(".class") and not item.startswith("META-INF/") and item != "module-info.class":
                    dest.writestr(item, source.read(item))
                elif ("LICENSE" in item.upper() or "NOTICE" in item.upper()) and not item.endswith("/") and not item.startswith("META-INF/versions/"):
                    (assets / (name + "-" + item.replace("/", "_"))).write_bytes(source.read(item))
        outputs.append(output)
    for name in ("LICENSE", "THIRD_PARTY_NOTICES.md"):
        shutil.copy2(ROOT / name, assets / name)
    return outputs, assets


def build(args, work):
    sdk = args.sdk / "platforms/android-36/android.jar"
    tools = args.tools or args.sdk / "build-tools/35.0.0"
    if not sdk.is_file() or not tools.is_dir() or not shutil.which("javac"):
        raise RuntimeError("Install JDK 17, Android SDK platform 36 and build-tools 35.0.0.")
    env = os.environ.copy()
    key, alias = args.keystore, args.alias
    signing = "release" if key else "test"
    if not key:
        key, alias = work / "test.p12", "balloon-test"
        env.update(BP_APK_STORE_PASS="temporary-test-key", BP_APK_KEY_PASS="temporary-test-key")
        run("keytool", "-genkeypair", "-keystore", key, "-storetype", "PKCS12", "-alias", alias,
            "-storepass:env", "BP_APK_STORE_PASS", "-keypass:env", "BP_APK_KEY_PASS",
            "-keyalg", "RSA", "-keysize", "2048", "-validity", "3650", "-dname", "CN=BalloonPredictor Test Build", env=env)
    elif not env.get("BP_APK_STORE_PASS") or not env.get("BP_APK_KEY_PASS"):
        raise RuntimeError("Set BP_APK_STORE_PASS and BP_APK_KEY_PASS for release signing.")
    jars, assets = libraries(work)

    def apk(folder, base, output, sources, dependencies=(), classpath=()):
        classes, dex = folder / "classes", folder / "dex"
        classes.mkdir()
        dex.mkdir()
        run("javac", "--release", "8", "-encoding", "UTF-8", "-classpath",
            os.pathsep.join(map(str, (sdk, *dependencies, *classpath))), "-d", classes, *sources)
        run(tools / "d8", "--min-api", "26", "--lib", sdk, "--output", dex,
            *(arg for p in classpath for arg in ("--classpath", p)), *classes.rglob("*.class"), *dependencies)
        with ZipFile(base, "a", ZIP_DEFLATED) as file:
            for p in dex.glob("*.dex"):
                file.write(p, p.name)
        aligned = folder / "aligned.apk"
        run(tools / "zipalign", "-f", "4", base, aligned)
        run(tools / "apksigner", "sign", "--ks", key, "--ks-key-alias", alias,
            "--ks-pass", "env:BP_APK_STORE_PASS", "--key-pass", "env:BP_APK_KEY_PASS", "--out", output, aligned, env=env)
        run(tools / "apksigner", "verify", "--verbose", "--print-certs", output)
        Path(str(output) + ".idsig").unlink(missing_ok=True)
        return classes

    args.output.mkdir(parents=True, exist_ok=True)
    generated = work / "generated"
    generated.mkdir()
    res, base = work / "resources.zip", work / "base.apk"
    run(tools / "aapt2", "compile", "--dir", SOURCE / "res", "-o", res)
    major, minor, patch = map(int, VERSION.split("."))
    run(tools / "aapt2", "link", "-I", sdk, "--manifest", SOURCE / "AndroidManifest.xml",
        "--min-sdk-version", "26", "--target-sdk-version", "36", "--version-code", major*10000+minor*100+patch,
        "--version-name", VERSION, "--java", generated, "-A", assets, "-o", base, res)
    output = args.output / f"BalloonPredictor-{VERSION}-Android.apk"
    classes = apk(work, base, output, [*SOURCE.rglob("*.java"), *generated.rglob("*.java")], jars)
    (args.output / "SHA256SUMS.txt").write_text(f"{digest(output)}  {output.name}\n")
    (args.output / "android-build.json").write_text(json.dumps({"version": VERSION, "signing": signing, "min_android_api": 26}, indent=2) + "\n")
    if args.test_output:
        args.test_output.mkdir(parents=True, exist_ok=True)
        folder = work / "instrumentation"
        folder.mkdir()
        test_base = folder / "base.apk"
        tests = HERE / "tests"
        run(tools / "aapt2", "link", "-I", sdk, "--manifest", tests / "AndroidManifest.xml",
            "--min-sdk-version", "26", "--target-sdk-version", "36", "-o", test_base)
        apk(folder, test_base, args.test_output / "BalloonPredictor-instrumentation.apk", tests.rglob("*.java"),
            classpath=(classes, *jars))
    print(output)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--sdk", type=Path, default=os.environ.get("ANDROID_HOME") or os.environ.get("ANDROID_SDK_ROOT"))
    parser.add_argument("--tools", type=Path)
    parser.add_argument("--keystore", type=Path)
    parser.add_argument("--alias", default="balloon-predictor")
    parser.add_argument("--test-output", type=Path)
    args = parser.parse_args()
    if args.sdk is None:
        parser.error("Set ANDROID_HOME or pass --sdk.")
    with tempfile.TemporaryDirectory(prefix="balloon-android-") as work:
        build(args, Path(work))
