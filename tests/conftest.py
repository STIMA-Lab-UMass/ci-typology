"""Shared pytest fixtures and environment bootstrap for the offline test suite.

The surviving ``dcc`` modules read several ``PROJECT_*`` paths from the
environment at instantiation time and use a mix of ``dcc.*`` and bare
``utils.``/``data.``/``features.`` imports.  To import them cleanly in-process
we:

* add the repository root (so ``import dcc...`` resolves) and ``<repo>/dcc``
  (so the bare ``utils``/``data``/``features`` packages resolve) to ``sys.path``;
* point every ``PROJECT_*`` variable at a throwaway temp directory so nothing
  touches the user's real data tree;
* set a clearly-fake OpenAI key so ``python-dotenv``'s ``load_dotenv`` (which the
  modules call with ``override=False``) never injects a real key into the tests.

Everything here is offline: no network, no OpenAI calls, no GCS.
"""

import os
import sys
import tempfile
from pathlib import Path

import pytest

# --- locate the repository root (tests/ lives directly under it) -------------
REPO_ROOT = Path(__file__).resolve().parent.parent
DCC_DIR = REPO_ROOT / "dcc"

# --- make both import styles resolve -----------------------------------------
for _p in (str(DCC_DIR), str(REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# --- throwaway PROJECT_* dirs so tests never read/write real data ------------
_TMP = Path(tempfile.mkdtemp(prefix="demand_bu_tests_"))
for _name, _sub in (
    ("PROJECT_DATA", "data"),
    ("PROJECT_OUT", "out"),
    ("PROJECT_CACHE_METADATA", "cache_metadata"),
):
    _d = _TMP / _sub
    _d.mkdir(parents=True, exist_ok=True)
    os.environ[_name] = str(_d)

# PROJECT_ROOT must point at the real repo (config files live in <repo>/envs).
os.environ["PROJECT_ROOT"] = str(REPO_ROOT)

# Fake, clearly-non-secret key. Set before any dcc import so dotenv's
# load_dotenv(override=False) cannot replace it with a real one on disk.
os.environ.setdefault("OPEN_AI_API_KEY", "offline-dummy-key-not-used")


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return REPO_ROOT


@pytest.fixture(scope="session")
def dcc_dir() -> Path:
    return DCC_DIR


@pytest.fixture(scope="session")
def base_env() -> dict:
    """A copy of ``os.environ`` suitable for spawning CLI subprocesses."""
    env = dict(os.environ)
    env["PROJECT_ROOT"] = str(REPO_ROOT)
    # Ensure the child can resolve both import styles too.
    env["PYTHONPATH"] = os.pathsep.join(
        [str(DCC_DIR), str(REPO_ROOT), env.get("PYTHONPATH", "")]
    ).rstrip(os.pathsep)
    return env


@pytest.fixture(scope="session")
def tracked_files(repo_root) -> list:
    """Files tracked by git (the tree that will be published)."""
    import subprocess

    out = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        check=True,
    )
    return [f for f in out.stdout.split("\0") if f]


def iter_dcc_modules():
    """Yield the dotted module name of every surviving ``dcc`` source file."""
    for path in sorted(DCC_DIR.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        rel = path.relative_to(REPO_ROOT).with_suffix("")
        parts = list(rel.parts)
        if parts[-1] == "__init__":
            parts = parts[:-1]
        if parts:
            yield ".".join(parts)
