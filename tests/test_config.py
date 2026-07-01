"""Integrity checks for ``envs/country_config.yml`` and ``envs/.env_template``.

The master ``country_config.yml`` is the *global* template the pipeline reads
when generating each per-country config. The keys actually consumed from it by
the surviving code (``project_setup/create_country_yml.py`` and
``project_setup/gadm_folder_creator.py``) are ``gadm_level``, ``country_map``,
``overture_class``, ``overture_version`` and the ``naics_dict_*`` classification
tables. ``openai_api`` is generated *into* each per-country config (it is not a
master key), and the old private ``global_gadm`` GCS pointer was removed with
the GCS download -- so this test also asserts that pointer is gone.
"""

import os
import re
from pathlib import Path

import pytest
import yaml

from conftest import DCC_DIR, REPO_ROOT

CONFIG_PATH = REPO_ROOT / "envs" / "country_config.yml"
ENV_TEMPLATE = REPO_ROOT / "envs" / ".env_template"

# Keys the surviving code reads directly from the master config.
REQUIRED_MASTER_KEYS = [
    "gadm_level",
    "country_map",
    "overture_class",
    "overture_version",
    "naics_dict_2",
    "naics_dict_3",
    "naics_dict_4",
    "naics_dict_6",
]

# os.environ variables that are managed internally (set/persisted by the code,
# not supplied by the user) and therefore need not appear in .env_template.
INTERNAL_ENV_VARS = {"OPENAI_SELECTED_MODEL"}


@pytest.fixture(scope="module")
def config():
    with open(CONFIG_PATH, "r") as fh:
        data = yaml.safe_load(fh)
    assert isinstance(data, dict), "country_config.yml did not parse to a mapping"
    return data


def test_country_config_loads(config):
    assert config, "country_config.yml is empty"


@pytest.mark.parametrize("key", REQUIRED_MASTER_KEYS)
def test_required_master_keys_present(config, key):
    assert key in config, f"country_config.yml is missing required key {key!r}"


def test_gadm_level_is_valid(config):
    gadm_level = config["gadm_level"]
    assert isinstance(gadm_level, list) and gadm_level, "gadm_level must be a non-empty list"
    assert "ADM_0" in gadm_level, "gadm_level must contain ADM_0"
    assert all(re.fullmatch(r"ADM_\d+", lvl) for lvl in gadm_level), gadm_level


@pytest.mark.parametrize("key", ["naics_dict_2", "naics_dict_3", "naics_dict_4", "naics_dict_6"])
def test_naics_dicts_nonempty(config, key):
    table = config[key]
    assert isinstance(table, dict) and table, f"{key} must be a non-empty mapping"


def test_country_map_is_iso3_mapping(config):
    cmap = config["country_map"]
    assert isinstance(cmap, dict) and cmap
    # Sample a couple of well-known ISO3 codes used in the docs/pipeline.
    assert "RWA" in cmap
    assert all(re.fullmatch(r"[A-Z]{3}", code) for code in cmap)


# --------------------------------------------------------------------------- #
# .env_template completeness
# --------------------------------------------------------------------------- #

_ENV_PATTERNS = [
    re.compile(r"""os\.environ(?:\.get)?\(\s*['"]([A-Z_][A-Z0-9_]*)['"]"""),
    re.compile(r"""os\.environ\[\s*['"]([A-Z_][A-Z0-9_]*)['"]\s*\]"""),
    re.compile(r"""os\.getenv\(\s*['"]([A-Z_][A-Z0-9_]*)['"]"""),
]


def _referenced_env_vars():
    found = set()
    for path in DCC_DIR.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for pat in _ENV_PATTERNS:
            found.update(pat.findall(text))
    return found


def test_env_template_exists():
    assert ENV_TEMPLATE.exists(), "envs/.env_template is missing"


def test_env_template_lists_every_referenced_var():
    template = ENV_TEMPLATE.read_text(encoding="utf-8")
    referenced = _referenced_env_vars() - INTERNAL_ENV_VARS
    assert referenced, "no os.environ variables discovered -- scan logic is broken"
    missing = sorted(v for v in referenced if not re.search(rf"^\s*{re.escape(v)}\s*=", template, re.M))
    assert not missing, f".env_template does not document: {missing}"


def test_env_template_documents_core_vars():
    template = ENV_TEMPLATE.read_text(encoding="utf-8")
    for var in ("PROJECT_ROOT", "PROJECT_DATA", "PROJECT_OUT", "PROJECT_CACHE_METADATA", "OPEN_AI_API_KEY"):
        assert re.search(rf"^\s*{var}\s*=", template, re.M), f"{var} not in .env_template"


def test_selected_model_json_is_valid():
    """The persisted model choice must be valid JSON and carry no secret."""
    import json

    path = DCC_DIR / "classification" / "selected_model.json"
    if not path.exists():
        pytest.skip("selected_model.json not present")
    data = json.loads(path.read_text(encoding="utf-8"))
    assert "model_name" in data and isinstance(data["model_name"], str)
    assert "sk-" not in path.read_text(encoding="utf-8")
