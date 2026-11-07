"""Offline fail-closed checks for the disposable browser/restore harness."""
import json
from pathlib import Path
import runpy
import os
import subprocess
import sys

import pytest

PROBE = Path(__file__).resolve().parents[2] / "frontend/e2e/pg-database.py"


@pytest.fixture
def probe(tmp_path, monkeypatch):
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))
    monkeypatch.setenv("PULT_E2E_PG_ROOT", str(tmp_path / "pult-pg-e2e.guards"))
    return runpy.run_path(str(PROBE))


def test_fresh_owned_root_and_private_password(probe):
    assert probe["run"]("init", []) == {"owned": True}
    root = probe["owned_root"]()
    assert len((root / "password.txt").read_text().strip()) == 64
    assert json.loads((root / "owned.json").read_text())["port"] == 55432
    with pytest.raises(AssertionError, match="not empty"):
        probe["run"]("init", [])


@pytest.mark.parametrize("flag", [True, False])
def test_optimized_python_rejected_before_root_or_cluster_access(probe, flag):
    environment = dict(os.environ)
    environment["PYTHONOPTIMIZE"] = "1"
    process = subprocess.run([sys.executable, *(["-O"] if flag else []), str(PROBE), "init"],
                             env=environment, capture_output=True, text=True, timeout=15)
    assert process.returncode != 0
    assert "Optimized Python is forbidden" in process.stderr
    assert not probe["root_path"]().exists()


@pytest.mark.parametrize("root", ["relative/pult-pg-e2e.test", "/", "/tmp/foreign", "/tmp/pult-pg-e2e.test"])
def test_unsafe_roots_rejected_before_connection(probe, monkeypatch, root):
    monkeypatch.setenv("PULT_E2E_PG_ROOT", root)
    with pytest.raises(AssertionError):
        probe["root_path"]()


def test_nonempty_directory_is_not_adopted(probe):
    root = probe["root_path"]()
    root.mkdir()
    (root / "unrelated.txt").write_text("preserve")
    with pytest.raises(AssertionError, match="not empty"):
        probe["run"]("init", [])
    assert (root / "unrelated.txt").read_text() == "preserve"


def test_wrong_marker_cannot_authorize_database_access(probe):
    probe["run"]("init", [])
    (probe["root_path"]() / "owned.json").write_text('{"purpose":"foreign"}')
    with pytest.raises(AssertionError):
        probe["owned_root"]()


@pytest.mark.parametrize("database", ["production", "pult", "pult_browser_source;DROP DATABASE postgres"])
def test_arbitrary_database_names_rejected_before_connection(probe, database):
    with pytest.raises(AssertionError):
        probe["connection"](database)


def test_only_equivalent_array_cast_is_normalized(probe):
    canonical = probe["canonical_schema"]
    a = "    CONSTRAINT ck CHECK ((kind = ANY ((ARRAY['a'::character varying, 'b'::character varying])::text[])))"
    b = "    CONSTRAINT ck CHECK ((kind = ANY (ARRAY[('a'::character varying)::text, ('b'::character varying)::text])))"
    assert canonical(a) == canonical(b)
    assert canonical(a) != canonical(a.replace("'b'", "'c'"))
    assert canonical(a) != canonical(a.replace("ck", "ck_changed"))
    assert canonical(a) != canonical(a.replace("ANY", "ALL"))
    grant = "GRANT SELECT ON TABLE users TO reader;"
    assert canonical(grant) == grant


def test_data_and_sequence_state_changes_are_distinguishable(probe):
    digest = probe["digest"]
    assert digest(["a", "a"]) != digest(["a"])
    assert digest(["a"]) != digest(["b"])
    assert digest([7, True]) != digest([7, False])
    assert digest([7, True]) != digest([8, True])
