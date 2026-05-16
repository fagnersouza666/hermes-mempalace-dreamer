"""CLI surface tests (strict TDD, written before implementation).

The ``status`` and ``schedule-plan`` commands must be pure: they only print
JSON, never call Hermes memory and never mutate anything (no cron, no config,
no filesystem writes).
"""
from pathlib import Path
import argparse
import importlib.util
import json
import sys

ROOT = Path(__file__).resolve().parents[1]


def load_plugin():
    spec = importlib.util.spec_from_file_location(
        "plugin_cli_test", ROOT / "__init__.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["plugin_cli_test"] = module
    spec.loader.exec_module(module)
    return module


def _parse(module, argv):
    parser = argparse.ArgumentParser()
    module._setup_cli_parser(parser)
    return parser.parse_args(argv)


# --- status ---------------------------------------------------------------


def test_status_prints_json_describing_plugin_and_safety(capsys):
    module = load_plugin()
    args = _parse(module, ["status"])
    args.func(args)

    payload = json.loads(capsys.readouterr().out)
    assert payload["plugin"] == "mempalace-dreaming"
    assert payload["version"] == "1.0.0"
    assert payload["status"]
    assert payload["bundled_skill_exists"] is True
    assert payload["engine_module_available"] is True
    assert payload["setup_module_available"] is True

    safety = payload["safety"]
    assert safety["no_obsidian_writes"] is True
    assert safety["no_setup_memory_writes"] is True
    assert safety["schedule_report_only"] is True


def test_build_status_is_pure_and_deterministic():
    module = load_plugin()
    first = module._build_status()
    second = module._build_status()
    assert first == second
    assert isinstance(first, dict)


# --- schedule-plan --------------------------------------------------------


def test_schedule_plan_prints_only_a_json_plan(capsys):
    module = load_plugin()
    args = _parse(module, ["schedule-plan", "--time", "05:30"])
    args.func(args)

    payload = json.loads(capsys.readouterr().out)
    assert payload["time"] == "05:30"
    assert payload["skill"] == "plugin:mempalace-dreaming"
    assert payload["report_only"] is True
    # No cron is created: the payload must say so explicitly.
    assert "cron" in json.dumps(payload).lower()


def test_schedule_plan_uses_default_time(capsys):
    module = load_plugin()
    args = _parse(module, ["schedule-plan"])
    args.func(args)
    payload = json.loads(capsys.readouterr().out)
    assert payload["time"] == "05:30"


def test_build_schedule_plan_is_report_only():
    module = load_plugin()
    plan = module.build_schedule_plan(time="06:15")
    assert plan["time"] == "06:15"
    assert plan["report_only"] is True


# --- existing commands stay intact ----------------------------------------


def test_setup_plan_still_prints_json(capsys, tmp_path):
    module = load_plugin()
    args = _parse(
        module, ["setup-plan", "--hermes-home", str(tmp_path), "--time", "07:00"]
    )
    args.func(args)
    payload = json.loads(capsys.readouterr().out)
    assert payload["config"]["memory.provider"] == "mempalace"


def test_setup_still_dry_run_by_default(capsys, tmp_path):
    module = load_plugin()
    args = _parse(module, ["setup", "--hermes-home", str(tmp_path)])
    args.func(args)
    payload = json.loads(capsys.readouterr().out)
    assert payload["applied"] is False
