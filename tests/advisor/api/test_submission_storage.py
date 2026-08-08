"""ABS-87: submission storage writability probe + staging helper.

The production advisor runs `read_only: true`, so `SUBMISSION_STORAGE_DIR`
only works when a writable volume is mounted there. These tests pin the two
application-side guarantees that make a missing volume diagnosable:

1. `probe_storage_root` distinguishes writable from read-only *without*
   writing anything (it runs on every /healthz hit).
2. `stage_upload` turns an unwritable root into a 503 naming the path,
   instead of an OSError-500, and never lets a client-supplied filename
   escape the per-user directory.
"""
from __future__ import annotations

import io
import os
import stat
from pathlib import Path

import pytest
from fastapi import HTTPException

from advisor.api.submission_storage import (
    STORAGE_OK,
    STORAGE_UNWRITABLE,
    probe_storage_root,
    resolve_storage_root,
    stage_upload,
)

# chmod-based read-only simulation is meaningless for root (CAP_DAC_OVERRIDE
# lets it write anyway). Skip rather than emit a false pass.
requires_non_root = pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() == 0,
    reason="chmod cannot make a directory read-only for root",
)


@pytest.fixture()
def read_only_dir(tmp_path: Path):
    """A directory that exists but rejects new entries."""
    target = tmp_path / "locked"
    target.mkdir()
    target.chmod(stat.S_IRUSR | stat.S_IXUSR)  # r-x------
    yield target
    # Restore write so pytest's tmp_path cleanup can remove it.
    target.chmod(stat.S_IRWXU)


# ----------------------------------------------------------------------
# resolve_storage_root
# ----------------------------------------------------------------------


def test_resolve_prefers_explicit_dir_over_settings(tmp_path: Path):
    assert resolve_storage_root(tmp_path / "explicit") == tmp_path / "explicit"


def test_resolve_falls_back_to_settings():
    from layer1.config import get_settings

    assert resolve_storage_root() == Path(get_settings().submission_storage_dir)


# ----------------------------------------------------------------------
# probe_storage_root
# ----------------------------------------------------------------------


def test_probe_ok_for_existing_writable_dir(tmp_path: Path):
    root = tmp_path / "submissions"
    root.mkdir()
    assert probe_storage_root(root) == STORAGE_OK


def test_probe_ok_when_root_absent_but_parent_writable(tmp_path: Path):
    """The prod mount exists; the dev default doesn't. Both are fine as long
    as the deepest existing ancestor would accept the mkdir."""
    assert probe_storage_root(tmp_path / "not" / "created" / "yet") == STORAGE_OK


@requires_non_root
def test_probe_unwritable_for_read_only_dir(read_only_dir: Path):
    assert probe_storage_root(read_only_dir) == STORAGE_UNWRITABLE


@requires_non_root
def test_probe_unwritable_when_ancestor_is_read_only(read_only_dir: Path):
    """The ABS-70 shape: SUBMISSION_STORAGE_DIR points somewhere that doesn't
    exist yet, under a read-only filesystem — the mkdir on first upload is
    what fails, and the probe has to predict that."""
    assert probe_storage_root(read_only_dir / "submissions") == STORAGE_UNWRITABLE


def test_probe_unwritable_when_root_is_a_file(tmp_path: Path):
    bogus = tmp_path / "not-a-dir"
    bogus.write_text("oops")
    assert probe_storage_root(bogus) == STORAGE_UNWRITABLE


def test_probe_does_not_create_anything(tmp_path: Path):
    root = tmp_path / "submissions"
    assert probe_storage_root(root) == STORAGE_OK
    assert not root.exists()
    assert list(tmp_path.iterdir()) == []


# ----------------------------------------------------------------------
# stage_upload
# ----------------------------------------------------------------------


def test_stage_upload_writes_under_per_user_dir(tmp_path: Path):
    path = stage_upload(
        tmp_path / "submissions",
        user_id=7,
        filename="model.ifc",
        fileobj=io.BytesIO(b"ISO-10303-21;"),
    )
    assert path == tmp_path / "submissions" / "user-7" / "model.ifc"
    assert path.read_bytes() == b"ISO-10303-21;"


def test_stage_upload_streams_multi_chunk_payload(tmp_path: Path):
    """Payloads larger than the 1 MiB chunk must round-trip intact."""
    payload = b"x" * ((1 << 20) + 4242)
    path = stage_upload(
        tmp_path / "submissions",
        user_id=1,
        filename="big.ifc",
        fileobj=io.BytesIO(payload),
    )
    assert path.read_bytes() == payload


def test_stage_upload_strips_path_traversal_from_filename(tmp_path: Path):
    root = tmp_path / "submissions"
    path = stage_upload(
        root,
        user_id=3,
        filename="../../etc/evil.ifc",
        fileobj=io.BytesIO(b"data"),
    )
    assert path == root / "user-3" / "evil.ifc"
    assert not (tmp_path.parent / "etc").exists()


def test_stage_upload_rejects_filename_with_no_basename(tmp_path: Path):
    with pytest.raises(HTTPException) as exc_info:
        stage_upload(
            tmp_path / "submissions",
            user_id=3,
            filename="../",
            fileobj=io.BytesIO(b"data"),
        )
    assert exc_info.value.status_code == 400
    assert exc_info.value.detail["code"] == "invalid_filename"


@requires_non_root
def test_stage_upload_raises_503_when_storage_is_read_only(read_only_dir: Path):
    """The ABS-87 acceptance inverse: no writable volume must yield a clear
    503 naming the path, not an opaque 500 OSError."""
    with pytest.raises(HTTPException) as exc_info:
        stage_upload(
            read_only_dir,
            user_id=1,
            filename="model.ifc",
            fileobj=io.BytesIO(b"data"),
        )
    assert exc_info.value.status_code == 503
    detail = exc_info.value.detail
    assert detail["code"] == "submission_storage_unavailable"
    assert str(read_only_dir) in detail["message"]
    assert "SUBMISSION_STORAGE_DIR" in detail["message"]
