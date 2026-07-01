"""Public CLI entry points wire up without error.

The two ``--config_name`` scripts must answer ``--help`` with exit code 0
(argparse short-circuits before requiring the argument). ``project_starter.py``
has no argparse parser of its own -- it drives an interactive setup -- so for it
we verify the module (and its ``argparse`` import) load cleanly instead, per the
spec's "runs --help *or* imports its argparse" requirement.
"""

import importlib
import os
import subprocess
import sys
from pathlib import Path

import pytest

# argparse-driven CLIs that support --help.
HELP_CLIS = [
    "dcc/overture_data_processing.py",
    "dcc/classify_overture.py",
]


@pytest.fixture(scope="session")
def documented_env(repo_root) -> dict:
    """Environment matching exactly how the README tells a user to run the CLIs.

    Crucially this does NOT inject ``PYTHONPATH``: a third-party user runs
    ``python dcc/<script>.py --help`` from the repo root with only ``.env``
    values set, so the entry points must put the repo root on ``sys.path``
    themselves. Injecting ``PYTHONPATH`` here would mask exactly the
    broken-import regression these tests guard against.
    """
    env = dict(os.environ)
    env["PROJECT_ROOT"] = str(repo_root)
    env.pop("PYTHONPATH", None)
    return env


@pytest.mark.parametrize("script", HELP_CLIS)
def test_cli_help_exits_zero(script, repo_root, documented_env):
    path = repo_root / script
    assert path.exists(), f"missing CLI entry point: {script}"
    proc = subprocess.run(
        [sys.executable, str(path), "--help"],
        cwd=str(repo_root),
        env=documented_env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    combined = (proc.stdout + proc.stderr).lower()
    assert proc.returncode == 0, f"{script} --help exited {proc.returncode}:\n{combined}"
    assert "usage" in combined, f"{script} --help printed no usage:\n{combined}"
    assert "--config_name" in combined, f"{script} --help missing --config_name option"


def test_project_starter_imports_cleanly():
    mod = importlib.import_module("dcc.project_starter")
    assert hasattr(mod, "ProjectStarter")
    starter = mod.ProjectStarter()
    assert hasattr(starter, "run")
    # The module imports argparse at the top; confirm the import succeeded.
    src = Path(mod.__file__).read_text(encoding="utf-8")
    assert "import argparse" in src
