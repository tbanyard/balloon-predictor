"""Verify the exact CI commit, collect installers, and create a GitHub release."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory

REPO = "tbanyard/balloon-predictor"
REMOTE = "https://github.com/" + REPO + ".git"
BRANCH, VERSION = "release-v1.6.3-test", "1.6.3"
PLATFORMS = {
    "Windows installer (test)": ("BalloonPredictor-Windows-x64-test", "BalloonPredictor-1.6.3-Windows-x64-Setup.exe"),
    "macOS installer (test)": ("BalloonPredictor-macOS-Universal-test", "BalloonPredictor-1.6.3-macOS-Universal.dmg"),
    "Android app (test)": ("BalloonPredictor-Android-test", "BalloonPredictor-1.6.3-Android.apk"),
    "Debian installer (test)": ("BalloonPredictor-Debian-amd64-test", "BalloonPredictor-1.6.3-Debian-amd64.deb"),
}
WORKFLOW_NAMES = {
    ".github/workflows/tests.yml": "Python and browser checks",
    ".github/workflows/windows-installer.yml": "Windows installer (test)",
    ".github/workflows/macos-installer.yml": "macOS installer (test)",
    ".github/workflows/android-app.yml": "Android app (test)",
    ".github/workflows/linux-installer.yml": "Debian installer (test)",
}


def run(*args, **options):
    return subprocess.run(args, check=True, text=True, **options)


def gh(*args):
    return run("gh", *args, capture_output=True).stdout.strip()


def digest(path):
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def workflow_runs(commit):
    # gh run list may obtain workflowName from the default branch. That name
    # can differ from the tested branch's name, even for the same workflow ID.
    # Identify the actual run by workflow file and keep the exact-commit check.
    result, page = [], 1
    while True:
        data = json.loads(gh("api", f"repos/{REPO}/actions/runs?branch={BRANCH}&head_sha={commit}&per_page=100&page={page}"))
        rows = data["workflow_runs"]
        for item in rows:
            name = WORKFLOW_NAMES.get(item.get("path", "").split("@", 1)[0])
            if name and item.get("head_branch") == BRANCH:
                result.append({"databaseId": item["id"], "headSha": item["head_sha"],
                               "workflowName": name, "status": item["status"],
                               "conclusion": item["conclusion"], "url": item["html_url"]})
        if len(rows) < 100:
            return result
        page += 1


def latest_runs(runs, commit):
    selected = {}
    for item in sorted(runs, key=lambda row: row["databaseId"], reverse=True):
        if item["headSha"] == commit:
            selected.setdefault(item["workflowName"], item)
    expected = ["Python and browser checks", *PLATFORMS]
    for name in expected:
        item = selected.get(name)
        if not item or item["status"] != "completed" or item["conclusion"] != "success":
            state = "not found" if not item else item["conclusion"] or item["status"]
            raise ValueError(f"{name}: {state} for {commit[:12]}. All five workflows must finish successfully for this exact source commit.")
    return {name: selected[name] for name in expected}


def verify_artifact(folder, filename, commit):
    binary = folder / filename
    if not binary.is_file():
        raise ValueError("The tested artifact is missing: " + filename)
    checksums = (folder / "SHA256SUMS.txt").read_text().splitlines()
    found = False
    for line in checksums:
        parts = line.split(maxsplit=1)
        if len(parts) == 2 and parts[1].strip().lstrip("*") == filename:
            found = parts[0] == digest(binary)
    if not found:
        raise ValueError("The checksum did not match: " + filename)
    metadata = folder / ("android-build.json" if filename.endswith(".apk") else "build-info.json")
    value = json.loads(metadata.read_text())
    if value.get("source_commit") != commit:
        raise ValueError("The installer was built from a different source commit: " + filename)
    return binary, value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("balloon-v1.6.3-release"))
    parser.add_argument("--publish", action="store_true", help="Publish the release and fast-forward main after verification.")
    parser.add_argument("--allow-test-apk", action="store_true", help="Allow a temporarily signed APK in a draft release.")
    parser.add_argument("--check-only", action="store_true", help="Download and verify files without changing GitHub.")
    args = parser.parse_args()
    if not shutil.which("gh") or not shutil.which("git"):
        raise ValueError("Git and GitHub CLI (gh) are required.")
    run("gh", "auth", "status", "--hostname", "github.com", stdout=subprocess.DEVNULL)
    commit = gh("api", f"repos/{REPO}/git/ref/heads/{BRANCH}", "--jq", ".object.sha")
    selected = latest_runs(workflow_runs(commit), commit)
    output = args.output.expanduser().resolve() / commit[:12]
    output.mkdir(parents=True, exist_ok=True, mode=0o700)
    assets, metadata = [], {}
    for name, (artifact, filename) in PLATFORMS.items():
        folder = output / artifact
        if not folder.exists():
            run("gh", "run", "download", str(selected[name]["databaseId"]), "--repo", REPO,
                "--name", artifact, "--dir", str(folder))
        binary, report = verify_artifact(folder, filename, commit)
        assets.append(binary)
        metadata[name] = report
        if filename.endswith(".dmg"):
            for arch in ("arm64", "x86_64"):
                check = json.loads((folder / f"smoke-{arch}.json").read_text())
                if check.get("architecture") != arch or not all(check.get(key, "").startswith("passed:") for key in ("engine", "window", "download")):
                    raise ValueError("The Mac image does not have passing installed tests for " + arch)
        if filename.endswith(".apk") and report.get("signing") != "release":
            if args.publish or not args.allow_test_apk:
                raise ValueError("This APK uses a temporary test key. Run setup-android-signing.sh before preparing a fresh test commit, or use --allow-test-apk for a draft that can be installed for testing. Google Play is not required.")
    source_zip = output / "balloon-predictor-v1.6.3.zip"
    with TemporaryDirectory(prefix="source-", dir=output) as work:
        checkout = Path(work) / "repo"
        run("git", "clone", "--quiet", "--no-tags", "--single-branch", "--branch", BRANCH, REMOTE, str(checkout))
        actual = run("git", "rev-parse", "HEAD", cwd=checkout, capture_output=True).stdout.strip()
        if actual != commit:
            raise ValueError("The test branch moved while files were being verified. Rerun to use its new commit.")
        run("git", "archive", "--format=zip", "--prefix=balloon-predictor-v1.6.3/",
            "--output", str(source_zip), commit, cwd=checkout)
        assets.append(source_zip)
        summary = output / "BUILD-REPORT.json"
        summary.write_text(json.dumps({"version": VERSION, "source_commit": commit, "workflows": selected,
                                       "builds": metadata}, indent=2) + "\n")
        assets.append(summary)
        sums = output / "SHA256SUMS.txt"
        sums.write_text("".join(f"{digest(file)}  {file.name}\n" for file in assets))
        assets.append(sums)
        print("Verified installers and source:", output, flush=True)
        if args.check_only:
            return 0
        # A draft is reviewable; main advances only when --publish is explicit.
        tag = "v" + VERSION
        notes = output / "RELEASE-NOTES.md"
        notes.write_text((checkout / "docs/V1.6.3.md").read_text(), encoding="utf-8")
        refs = run("git", "ls-remote", REMOTE, f"refs/tags/{tag}", f"refs/tags/{tag}^{{}}",
                   capture_output=True).stdout.splitlines()
        tagged = next((line.split()[0] for line in refs if line.endswith("^{}")), refs[0].split()[0] if refs else None)
        if tagged and tagged != commit:
            raise ValueError("The release tag already points at a different commit. Nothing was overwritten.")
        current = subprocess.run(["gh", "release", "view", tag, "--repo", REPO, "--json", "isDraft,url"],
                                 text=True, capture_output=True)
        if current.returncode == 0:
            existing = json.loads(current.stdout)
            if not existing["isDraft"]:
                print("The release is already public:", existing["url"])
                return 0
            run("gh", "release", "edit", tag, "--repo", REPO, "--target", commit, "--notes-file", str(notes))
            run("gh", "release", "upload", tag, "--repo", REPO, "--clobber", *(str(file) for file in assets))
        else:
            run("gh", "release", "create", tag, "--repo", REPO, "--target", commit, "--draft",
                "--title", "BalloonPredictor v1.6.3", "--notes-file", str(notes), *(str(file) for file in assets))
        if args.publish:
            if not tagged:
                run("git", "-c", "credential.helper=", "-c", "credential.helper=!gh auth git-credential",
                    "push", "--no-follow-tags", REMOTE, f"{commit}:refs/tags/{tag}", cwd=checkout)
            run("git", "fetch", "--quiet", "origin", "main", cwd=checkout)
            ancestor = subprocess.run(["git", "merge-base", "--is-ancestor", "FETCH_HEAD", commit], cwd=checkout)
            if ancestor.returncode:
                raise ValueError("main contains changes outside this tested release. Merge and test them before publishing.")
            run("git", "-c", "credential.helper=", "-c", "credential.helper=!gh auth git-credential",
                "push", "--no-follow-tags", REMOTE, f"{commit}:refs/heads/main", cwd=checkout)
            run("gh", "release", "edit", tag, "--repo", REPO, "--draft=false", "--latest")
        print(gh("release", "view", tag, "--repo", REPO, "--json", "url", "--jq", ".url"))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        print(f"Stopped: {error}", file=sys.stderr)
        raise SystemExit(1)
