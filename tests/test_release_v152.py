import importlib.util
import json
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("release_v152", Path(__file__).resolve().parents[1] / "scripts/release-v1.5.3.py")
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


def test_release_requires_all_checks_for_exact_commit():
    rows = [{"databaseId": i, "headSha": "new", "workflowName": name, "status": "completed", "conclusion": "success"}
            for i, name in enumerate(["Python and browser checks", *release.PLATFORMS])]
    assert len(release.latest_runs(rows, "new")) == 5
    wrong = [*rows[:-1], {**rows[-1], "headSha": "old"}]
    with pytest.raises(ValueError, match="older successful runs"):
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
