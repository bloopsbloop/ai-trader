import hashlib

import pytest

from tests.artifact_manifest import assert_manifest_sha256, resolve_manifest_path


def test_windows_and_posix_manifest_paths_resolve_the_same_file(tmp_path):
    fixture = tmp_path / "data" / "history" / "BTCUSDT_4h.csv"
    fixture.parent.mkdir(parents=True)
    fixture.write_bytes(b"frozen fixture")

    windows = resolve_manifest_path(tmp_path, r"data\history\BTCUSDT_4h.csv")
    posix = resolve_manifest_path(tmp_path, "data/history/BTCUSDT_4h.csv")

    assert windows == posix == fixture.resolve()
    expected = hashlib.sha256(fixture.read_bytes()).hexdigest()
    assert_manifest_sha256(tmp_path, r"data\history\BTCUSDT_4h.csv", expected)
    assert_manifest_sha256(tmp_path, "data/history/BTCUSDT_4h.csv", expected)


def test_manifest_hash_mismatch_still_fails(tmp_path):
    fixture = tmp_path / "artifact.bin"
    fixture.write_bytes(b"actual")

    with pytest.raises(AssertionError, match="SHA-256 mismatch"):
        assert_manifest_sha256(tmp_path, "artifact.bin", hashlib.sha256(b"different").hexdigest())


def test_missing_manifest_file_still_fails(tmp_path):
    with pytest.raises(FileNotFoundError):
        assert_manifest_sha256(tmp_path, "missing.bin", hashlib.sha256(b"missing").hexdigest())


@pytest.mark.parametrize(
    "manifest_path",
    (r"..\outside.bin", "../outside.bin", r"C:\outside.bin", r"C:outside.bin", "/outside.bin", r"\\server\share\outside.bin"),
)
def test_manifest_path_rejects_escape_and_absolute_forms(tmp_path, manifest_path):
    with pytest.raises(ValueError):
        resolve_manifest_path(tmp_path, manifest_path)
