"""Packaging / metadata contract for GitHub plugin consumers.

Written before the pyproject change (strict TDD). These tests assert the
repo is consistent and importable, without introducing build dependencies.
"""
from pathlib import Path
import importlib
import sys
import tomllib

import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_pyproject_declares_build_system():
    data = tomllib.loads((ROOT / "pyproject.toml").read_text())
    assert data["project"]["name"] == "hermes-mempalace-dreamer"
    assert data["project"]["version"] == "1.0.1"

    build_system = data["build-system"]
    assert build_system["requires"], "build-system.requires must not be empty"
    assert build_system["build-backend"]


def test_plugin_yaml_is_well_formed_and_side_effect_free():
    data = yaml.safe_load((ROOT / "plugin.yaml").read_text())
    assert data["name"] == "mempalace-dreaming"
    assert str(data["version"]) == "1.0.1"
    # No hooks => no hidden side effects wired at register time.
    assert data["hooks"] == []


def test_skill_frontmatter_version_matches_plugin():
    text = (ROOT / "skills" / "mempalace-dreaming" / "SKILL.md").read_text()
    assert text.startswith("---")
    end = text.find("\n---\n", 3)
    fm = yaml.safe_load(text[3:end])
    assert fm["name"] == "mempalace-dreaming"
    assert str(fm["version"]) == "1.0.1"


def test_package_modules_import_cleanly():
    sys.path.insert(0, str(ROOT))
    engine = importlib.import_module("mempalace_dreaming.engine")
    setup = importlib.import_module("mempalace_dreaming.setup")

    for attr in (
        "run_light_dream",
        "render_report",
        "audit_retrieval_noise",
        "RetrievalAuditReport",
    ):
        assert hasattr(engine, attr), f"engine missing {attr}"
    assert hasattr(setup, "apply_setup_plan")
