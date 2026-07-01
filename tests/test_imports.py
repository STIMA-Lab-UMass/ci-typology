"""Every surviving ``dcc`` module must import cleanly.

Offline-safe:
* A missing *third-party* dependency (e.g. ``duckdb``) skips that module rather
  than failing, so the suite stays green on a partial environment.
* A reference to a *deleted internal* module (``dcc``/``utils``/``data``/
  ``features``) is a hard failure -- that is exactly the "dangling import"
  regression these tests guard against.
* Any other exception (``NameError``, ``SyntaxError``, ...) is a hard failure.
"""

import importlib
import re
from pathlib import Path

import pytest

from conftest import REPO_ROOT, iter_dcc_modules

MODULES = sorted(iter_dcc_modules())

# Top-level package names that live *inside* this repo. A ModuleNotFoundError
# whose missing root is one of these means a surviving module still points at
# code we deleted -> fail loudly. Anything else is an external dependency.
INTERNAL_ROOTS = {"dcc", "utils", "data", "features"}

# Basenames of modules/symbols removed from the public pipeline (the Step-4
# building-footprint join). None may be referenced by surviving imports.
DELETED_NAMES = [
    "join_footprint_ot_class",
    "join_overture_data_v01",
    "AddOvertureFootprints",
]


def test_module_list_nonempty():
    assert MODULES, "no dcc modules discovered -- conftest path setup is wrong"


@pytest.mark.parametrize("modname", MODULES)
def test_module_imports(modname):
    try:
        importlib.import_module(modname)
    except ModuleNotFoundError as exc:
        missing_root = (exc.name or "").split(".")[0]
        if missing_root in INTERNAL_ROOTS:
            pytest.fail(
                f"{modname} references missing internal module {exc.name!r} "
                f"(likely a dangling import to deleted code): {exc!r}"
            )
        pytest.skip(f"optional dependency {missing_root!r} not installed: {exc!r}")
    except Exception as exc:  # NameError, SyntaxError, ImportError, ...
        pytest.fail(f"{modname} failed to import: {exc!r}")


@pytest.mark.parametrize("modname", MODULES)
def test_no_references_to_deleted_modules(modname):
    """Static check: surviving source must not import any removed module."""
    path = REPO_ROOT / (modname.replace(".", "/"))
    src_file = path.with_suffix(".py")
    if not src_file.exists():
        src_file = path / "__init__.py"
    text = Path(src_file).read_text(encoding="utf-8", errors="replace")
    # Only inspect actual (non-comment) import lines.
    import_lines = [
        ln
        for ln in text.splitlines()
        if re.match(r"\s*(from|import)\s", ln) and not ln.lstrip().startswith("#")
    ]
    blob = "\n".join(import_lines)
    for name in DELETED_NAMES:
        assert not re.search(rf"\b{re.escape(name)}\b", blob), (
            f"{modname} imports deleted module/symbol {name!r}"
        )
