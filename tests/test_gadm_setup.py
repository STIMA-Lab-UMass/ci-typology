"""GADM setup must be a public, user-supplied-file flow -- never a GCS download.

With no global GADM geopackage present (and even with all network blocked),
``GADMFilesCreator`` must:

* report the file is absent,
* print the manual gadm.org download walkthrough, and
* cause ``project_starter`` to exit non-zero,

without ever attempting a network/cloud download.
"""

import builtins
import importlib
import socket

import pytest

gpd = pytest.importorskip("geopandas")  # hard dep, but skip cleanly if absent


@pytest.fixture
def no_network(monkeypatch):
    """Make any attempt to open a socket raise, proving the code stays offline."""

    def _boom(*args, **kwargs):
        raise AssertionError("network access attempted during GADM setup")

    monkeypatch.setattr(socket, "socket", _boom)
    # Common higher-level helpers that some libs use directly.
    monkeypatch.setattr(socket, "create_connection", _boom, raising=False)


@pytest.fixture
def creator(tmp_path, monkeypatch):
    """A GADMFilesCreator pointed at an empty PROJECT_DATA (no GADM file)."""
    monkeypatch.setenv("PROJECT_DATA", str(tmp_path))
    mod = importlib.import_module("dcc.project_setup.gadm_folder_creator")
    return mod.GADMFilesCreator()


def test_module_has_no_gcs_references():
    mod = importlib.import_module("dcc.project_setup.gadm_folder_creator")
    src = open(mod.__file__, encoding="utf-8").read()
    for token in ("GcsDownloader", "google.cloud", "storage.Client", "download_file("):
        assert token not in src, f"gadm_folder_creator still references {token!r}"


def test_missing_file_reports_absent(creator):
    assert creator.file_exists() is False


def test_missing_file_prints_instructions_and_returns_false(creator, capsys, no_network):
    result = creator.ensure_global_gadm()
    assert result is False
    out = capsys.readouterr().out
    assert "gadm.org" in out
    assert "gadm_410-levels" in out
    # It must point the user at a local PROJECT_DATA path, not a bucket.
    assert "GADM_global" in out


def test_process_exits_nonzero_when_gadm_missing(creator, monkeypatch, no_network):
    # If get_country() were ever reached it would call input(); guard against it.
    def _no_input(*a, **k):
        raise AssertionError("input() called -- process did not exit before prompting")

    monkeypatch.setattr(builtins, "input", _no_input)

    with pytest.raises(SystemExit) as excinfo:
        creator.process()
    assert excinfo.value.code == 1
