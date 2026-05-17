"""Runtime home resolution tests.

Regression for real-host validation: plugin CLI/runtime helpers must honor the
active ``HERMES_HOME`` instead of hardcoding ``~/.hermes``.
"""
from pathlib import Path
import argparse
import importlib.util
import os
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def load_plugin():
    spec = importlib.util.spec_from_file_location(
        "plugin_runtime_home_test", ROOT / "__init__.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["plugin_runtime_home_test"] = module
    spec.loader.exec_module(module)
    return module


def _runner_ok(argv):
    argv = list(argv)
    if argv == ["hermes", "--version"]:
        return {"ok": True, "returncode": 0, "stdout": "Hermes Agent v0", "stderr": "", "error": ""}
    if argv == ["hermes", "memory", "status"]:
        return {
            "ok": True,
            "returncode": 0,
            "stdout": "Memory status\nProvider:  mempalace\n",
            "stderr": "",
            "error": "",
        }
    if argv == ["hermes", "config", "path"]:
        return {
            "ok": False,
            "returncode": 1,
            "stdout": "",
            "stderr": "",
            "error": "config unavailable in this regression stub",
        }
    if argv == ["hermes", "cron", "list"]:
        return {"ok": True, "returncode": 0, "stdout": "No scheduled jobs.\n", "stderr": "", "error": ""}
    raise AssertionError(f"unexpected argv: {argv}")


def test_cli_defaults_pick_up_active_hermes_home(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "fresh-home"))
    module = load_plugin()
    parser = argparse.ArgumentParser()
    module._setup_cli_parser(parser)

    for cmd in ("setup-plan", "setup", "verify-runtime", "doctor", "repair-plan"):
        args = parser.parse_args([cmd])
        assert args.hermes_home == str(tmp_path / "fresh-home")


def test_runtime_verification_uses_env_home_by_default(monkeypatch, tmp_path):
    fresh_home = tmp_path / "runtime-home"
    (fresh_home / "mempalace" / "palace").mkdir(parents=True)
    (fresh_home / "mempalace" / "hermes-corpus").mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(fresh_home))
    module = load_plugin()

    report = module.build_runtime_verification(run_fn=_runner_ok)

    assert report["hermes_home"] == str(fresh_home)
    assert report["checks"]["all_directories_exist"] is True


def test_doctor_and_repair_plan_use_env_home_by_default(monkeypatch, tmp_path):
    fresh_home = tmp_path / "doctor-home"
    (fresh_home / "mempalace" / "palace").mkdir(parents=True)
    (fresh_home / "mempalace" / "hermes-corpus").mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(fresh_home))
    module = load_plugin()

    doctor = module.build_doctor_report(run_fn=_runner_ok)
    repair = module.build_repair_plan(run_fn=_runner_ok)

    assert doctor["hermes_home"] == str(fresh_home)
    assert repair["hermes_home"] == str(fresh_home)
