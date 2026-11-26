import importlib.util
import json
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("release_v152", Path(__file__).resolve().parents[1] / "scripts/release-v1.6.4.py")
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


def test_release_requires_all_checks_for_exact_commit():
    rows = [{"databaseId": i, "headSha": "new", "workflowName": name, "status": "completed", "conclusion": "success"}
            for i, name in enumerate(["Python and browser checks", *release.PLATFORMS])]
    assert len(release.latest_runs(rows, "new")) == 5
    wrong = [*rows[:-1], {**rows[-1], "headSha": "old"}]
    with pytest.raises(ValueError, match="exact source commit"):
        release.latest_runs(wrong, "new")
    failed = {**rows[0], "databaseId": 100, "conclusion": "failure"}
    with pytest.raises(ValueError, match="failure"):
        release.latest_runs([*rows, failed], "new")


def test_release_rejects_wrong_binary_or_source_commit(tmp_path):
    filename = "example.exe"
    binary = tmp_path / filename
    binary.write_bytes(b"tested artifact")
    (tmp_path / "SHA256SUMS.txt").write_text(release.digest(binary) + "  " + filename + "\n")
    (tmp_path / "build-info.json").write_text(json.dumps({"source_commit": "right"}))
    assert release.verify_artifact(tmp_path, filename, "right")[0] == binary
    with pytest.raises(ValueError, match="different source commit"):
        release.verify_artifact(tmp_path, filename, "wrong")
    binary.write_bytes(b"changed artifact")
    with pytest.raises(ValueError, match="checksum"):
        release.verify_artifact(tmp_path, filename, "right")


def api_run(path, name="Old display name", **overrides):
    return {"id": 123, "name": name, "path": path, "head_branch": release.BRANCH,
            "head_sha": "tested", "status": "completed", "conclusion": "success",
            "html_url": "https://example.invalid/run/123", **overrides}


def test_workflow_files_resolve_names_from_tested_branch(monkeypatch):
    rows = [api_run(path, "Python checks" if path.endswith("tests.yml") else "Renamed workflow", id=index)
            for index, path in enumerate(release.WORKFLOW_NAMES)]
    monkeypatch.setattr(release, "gh", lambda *args: json.dumps({"workflow_runs": rows}))
    selected = release.latest_runs(release.workflow_runs("tested"), "tested")
    assert len(selected) == 5
    assert selected["Python and browser checks"]["databaseId"] == 0


def test_workflow_file_identity_rejects_unrelated_run(monkeypatch):
    rows = [api_run(".github/workflows/unrelated.yml", "Python and browser checks"),
            api_run(".github/workflows/tests.yml", head_branch="main")]
    monkeypatch.setattr(release, "gh", lambda *args: json.dumps({"workflow_runs": rows}))
    assert release.workflow_runs("tested") == []


@pytest.mark.parametrize("state", ["in_progress", "failure", "wrong-commit"])
def test_api_lookup_retains_all_success_and_commit_checks(monkeypatch, state):
    rows = [api_run(path, id=index) for index, path in enumerate(release.WORKFLOW_NAMES)]
    if state == "in_progress":
        rows[0].update(status="in_progress", conclusion=None)
    elif state == "failure":
        rows[0]["conclusion"] = "failure"
    else:
        rows[0]["head_sha"] = "old"
    monkeypatch.setattr(release, "gh", lambda *args: json.dumps({"workflow_runs": rows}))
    with pytest.raises(ValueError, match="exact source commit"):
        release.latest_runs(release.workflow_runs("tested"), "tested")


def test_api_lookup_reads_another_page(monkeypatch):
    first = [api_run(".github/workflows/unrelated.yml", id=i) for i in range(100)]
    pages = [{"workflow_runs": first}, {"workflow_runs": [api_run(".github/workflows/tests.yml")]}]
    calls = []
    def fake_gh(*args):
        calls.append(args)
        return json.dumps(pages[len(calls)-1])
    monkeypatch.setattr(release, "gh", fake_gh)
    rows = release.workflow_runs("tested")
    assert len(calls) == 2 and "page=2" in calls[1][1]
    assert rows[0]["workflowName"] == "Python and browser checks"


def test_partial_download_retries_then_reuses_only_verified_files(tmp_path, monkeypatch):
    import subprocess
    artifact='installer';name='example.exe';commit='tested-source';folder=tmp_path/artifact
    folder.mkdir();(folder/'partial-download.zip').write_bytes(b'incomplete')
    calls=[]
    def download(*args,**kwargs):
        calls.append(args);target=Path(args[args.index('--dir')+1])
        if len(calls)==1:
            (target/'partial.zip').write_bytes(b'interrupted')
            raise subprocess.CalledProcessError(1,args)
        (target/name).write_bytes(b'tested installer')
        (target/'SHA256SUMS.txt').write_text(release.digest(target/name)+'  '+name+'\n')
        (target/'build-info.json').write_text(json.dumps({'source_commit':commit}))
    monkeypatch.setattr(release,'run',download)
    monkeypatch.setattr(release.time,'sleep',lambda _:None)
    binary,report=release.collect_artifact(folder,artifact,name,commit,123)
    assert binary.read_bytes()==b'tested installer' and report['source_commit']==commit
    assert len(calls)==2 and not (folder/'partial.zip').exists() and not (folder/'partial-download.zip').exists()
    assert release.collect_artifact(folder,artifact,name,commit,123)[0]==binary
    assert len(calls)==2, 'A complete verified artifact was downloaded again'
