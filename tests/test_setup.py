"""Tests for the explicit setup apply layer.

Written before the implementation (strict TDD). Side effects are
dependency-injected so apply mode can be exercised without touching the
real filesystem or running Hermes.
"""
from pathlib import Path
import argparse
import importlib.util
import json
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]

sys.path.insert(0, str(ROOT))

from mempalace_dreaming.setup import (  # noqa: E402
    SetupResult,
    apply_setup_plan,
    build_config_commands,
)


def load_plugin():
    spec = importlib.util.spec_from_file_location("plugin_under_test", ROOT / "__init__.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["plugin_under_test"] = module
    spec.loader.exec_module(module)
    return module


def _plan(tmp_path, schedule=True):
    module = load_plugin()
    return module.build_setup_plan(
        hermes_home=tmp_path, schedule_dreaming=schedule, time="05:30"
    )


class Recorder:
    def __init__(self):
        self.mkdirs = []
        self.runs = []

    def mkdir(self, path):
        self.mkdirs.append(path)

    def run(self, argv):
        self.runs.append(argv)


# --- build_config_commands -------------------------------------------------


def test_build_config_commands_returns_argv_lists(tmp_path):
    plan = _plan(tmp_path)
    commands = build_config_commands(plan)

    assert isinstance(commands, list)
    for cmd in commands:
        assert isinstance(cmd, list)
        assert cmd[:3] == ["hermes", "config", "set"]
        assert all(isinstance(part, str) for part in cmd)

    assert ["hermes", "config", "set", "memory.provider", "mempalace"] in commands


def test_build_config_commands_lowercases_booleans(tmp_path):
    plan = _plan(tmp_path)
    commands = build_config_commands(plan)
    assert ["hermes", "config", "set", "memory.memory_enabled", "true"] in commands
    assert (
        ["hermes", "config", "set", "memory.user_profile_enabled", "true"] in commands
    )


# --- apply_setup_plan: dry-run --------------------------------------------


def test_dry_run_does_not_call_side_effects(tmp_path):
    plan = _plan(tmp_path)
    rec = Recorder()

    result = apply_setup_plan(
        plan, mkdir_fn=rec.mkdir, run_fn=rec.run, apply=False
    )

    assert rec.mkdirs == []
    assert rec.runs == []
    assert isinstance(result, SetupResult)
    assert result.applied is False
    # It still reports what *would* happen.
    assert result.created_directories == plan["directories"]
    assert result.config_commands == build_config_commands(plan)
    assert result.rollback_notes


# --- apply_setup_plan: apply ----------------------------------------------


def test_apply_creates_each_directory(tmp_path):
    plan = _plan(tmp_path)
    rec = Recorder()

    apply_setup_plan(plan, mkdir_fn=rec.mkdir, run_fn=rec.run, apply=True)

    assert rec.mkdirs == plan["directories"]


def test_apply_runs_config_commands_as_argv_lists(tmp_path):
    plan = _plan(tmp_path)
    rec = Recorder()

    apply_setup_plan(plan, mkdir_fn=rec.mkdir, run_fn=rec.run, apply=True)

    assert rec.runs == build_config_commands(plan)
    for argv in rec.runs:
        assert isinstance(argv, list)


def test_apply_result_marks_applied(tmp_path):
    plan = _plan(tmp_path)
    rec = Recorder()
    result = apply_setup_plan(
        plan, mkdir_fn=rec.mkdir, run_fn=rec.run, apply=True
    )
    assert result.applied is True


# --- schedule stays planned, never executed -------------------------------


def test_schedule_is_planned_but_not_executed(tmp_path):
    plan = _plan(tmp_path, schedule=True)
    rec = Recorder()

    result = apply_setup_plan(
        plan, mkdir_fn=rec.mkdir, run_fn=rec.run, apply=True
    )

    assert result.schedule_planned == plan["schedule"]
    # No cron / scheduling side effect of any kind.
    for argv in rec.runs:
        assert "cron" not in " ".join(argv).lower()
        assert "schedule" not in " ".join(argv).lower()


def test_no_schedule_key_yields_none(tmp_path):
    plan = _plan(tmp_path, schedule=False)
    rec = Recorder()
    result = apply_setup_plan(
        plan, mkdir_fn=rec.mkdir, run_fn=rec.run, apply=True
    )
    assert result.schedule_planned is None


# --- CLI ------------------------------------------------------------------


def test_cli_setup_without_apply_emits_dry_run_json(capsys, tmp_path):
    module = load_plugin()
    parser = argparse.ArgumentParser()
    module._setup_cli_parser(parser)
    args = parser.parse_args(
        ["setup", "--hermes-home", str(tmp_path), "--schedule-dreaming"]
    )
    args.func(args)

    payload = json.loads(capsys.readouterr().out)
    assert payload["applied"] is False
    assert payload["created_directories"]
    assert ["hermes", "config", "set", "memory.provider", "mempalace"] in payload[
        "config_commands"
    ]


def test_cli_apply_from_args_injects_side_effects(tmp_path):
    module = load_plugin()
    parser = argparse.ArgumentParser()
    module._setup_cli_parser(parser)
    args = parser.parse_args(
        ["setup", "--hermes-home", str(tmp_path), "--apply"]
    )
    rec = Recorder()

    result = module._apply_setup_from_args(
        args, mkdir_fn=rec.mkdir, run_fn=rec.run
    )

    assert result.applied is True
    assert rec.mkdirs
    assert rec.runs
    assert all(isinstance(argv, list) for argv in rec.runs)


# --- robust plugin import (no repo root on sys.path) ----------------------


def test_apply_setup_from_args_without_repo_root_on_syspath(
    tmp_path, monkeypatch
):
    """The plugin must apply setup even if the repo root is not importable.

    Loads ``__init__.py`` in isolation, then strips the repo root from
    ``sys.path`` (and chdir away from it) and drops the cached
    ``mempalace_dreaming`` package, so the normal
    ``from mempalace_dreaming.setup import ...`` would fail. Robust loading
    from ``PLUGIN_DIR`` must take over.
    """
    spec = importlib.util.spec_from_file_location(
        "plugin_isolated", ROOT / "__init__.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        sys,
        "path",
        [p for p in sys.path if p not in ("", str(ROOT))
         and Path(p).resolve() != ROOT],
    )
    for name in list(sys.modules):
        if name == "mempalace_dreaming" or name.startswith(
            "mempalace_dreaming."
        ):
            monkeypatch.delitem(sys.modules, name, raising=False)

    # Sanity: the normal import path is genuinely unavailable now.
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("mempalace_dreaming.setup")

    args = argparse.Namespace(
        mempalace_dreaming_command="setup",
        hermes_home=str(tmp_path),
        schedule_dreaming=False,
        time="05:30",
        apply=True,
    )
    rec = Recorder()
    result = module._apply_setup_from_args(
        args, mkdir_fn=rec.mkdir, run_fn=rec.run
    )

    assert result.applied is True
    assert rec.mkdirs
    assert rec.runs
    assert result.errors == []


# --- apply error handling -------------------------------------------------


class Boom(Exception):
    pass


def test_apply_mkdir_failure_records_error_and_runs_no_config(tmp_path):
    plan = _plan(tmp_path)
    runs = []

    def bad_mkdir(path):
        raise Boom("disk full")

    def run(argv):
        runs.append(argv)

    result = apply_setup_plan(
        plan, mkdir_fn=bad_mkdir, run_fn=run, apply=True
    )

    assert result.applied is True
    assert result.errors
    assert any("disk full" in e for e in result.errors)
    # First failure stops everything: no config command runs.
    assert runs == []
    assert result.config_commands == []
    assert result.created_directories == []


def test_apply_config_command_failure_records_error_and_stops(tmp_path):
    plan = _plan(tmp_path)
    rec_mkdirs = []
    attempted = []

    def mkdir(path):
        rec_mkdirs.append(path)

    def flaky_run(argv):
        attempted.append(argv)
        if len(attempted) == 2:
            raise Boom("config rejected")

    result = apply_setup_plan(
        plan, mkdir_fn=mkdir, run_fn=flaky_run, apply=True
    )

    expected = build_config_commands(plan)
    assert result.applied is True
    assert result.errors
    assert any("config rejected" in e for e in result.errors)
    # Directories were created before the config step failed.
    assert result.created_directories == plan["directories"]
    assert rec_mkdirs == plan["directories"]
    # Only the first command succeeded; later commands never ran.
    assert attempted == expected[:2]
    assert result.config_commands == expected[:1]


def test_successful_apply_has_empty_errors(tmp_path):
    plan = _plan(tmp_path)
    rec = Recorder()

    result = apply_setup_plan(
        plan, mkdir_fn=rec.mkdir, run_fn=rec.run, apply=True
    )

    assert result.errors == []
    assert result.created_directories == plan["directories"]
    assert result.config_commands == build_config_commands(plan)


def test_dry_run_has_no_errors_and_no_side_effects(tmp_path):
    plan = _plan(tmp_path)
    rec = Recorder()

    result = apply_setup_plan(
        plan, mkdir_fn=rec.mkdir, run_fn=rec.run, apply=False
    )

    assert result.errors == []
    assert rec.mkdirs == []
    assert rec.runs == []


def test_cli_apply_json_includes_errors(capsys, tmp_path):
    module = load_plugin()
    parser = argparse.ArgumentParser()
    module._setup_cli_parser(parser)
    args = parser.parse_args(
        ["setup", "--hermes-home", str(tmp_path)]
    )
    args.func(args)

    payload = json.loads(capsys.readouterr().out)
    assert "errors" in payload
    assert payload["errors"] == []
