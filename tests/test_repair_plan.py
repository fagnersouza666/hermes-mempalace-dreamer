"""repair-plan command tests (strict TDD — written before implementation).

``repair-plan`` turns the problems detected by ``doctor`` into an explicit,
machine-readable repair plan. It must:

* print JSON only;
* be report-only — never write config, cron, memory, Obsidian, or the
  filesystem, and never execute ``hermes config set`` / ``hermes cron`` / etc.;
* reuse ``build_doctor_report`` internally but expose its own shape;
* expose plugin/version/ok/summary/warnings/repairs;
* order ``repairs`` by priority (high -> medium -> low);
* return ``ok: True`` and ``repairs: []`` when doctor is fully green;
* never invent a cron job id;
* never raise a traceback in any of these scenarios.
"""
from pathlib import Path
import argparse
import importlib.util
import json
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load_plugin():
    spec = importlib.util.spec_from_file_location(
        "plugin_repair_plan_test", ROOT / "__init__.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["plugin_repair_plan_test"] = module
    spec.loader.exec_module(module)
    return module


def _parse(module, argv):
    parser = argparse.ArgumentParser()
    module._setup_cli_parser(parser)
    return parser.parse_args(argv)


def _fake_runner(mapping):
    def run_fn(argv):
        return mapping[tuple(argv)]

    return run_fn


def _ok(stdout=""):
    return {"ok": True, "returncode": 0, "stdout": stdout, "stderr": "", "error": ""}


def _fail(error="boom"):
    return {
        "ok": False,
        "returncode": 1,
        "stdout": "",
        "stderr": error,
        "error": error,
    }


CRON_LIST_WITH_DAILY = """\
ID    NAME                          SCHEDULE         STATUS
1     mempalace-dreaming-daily      30 08 * * *      active
"""

CRON_LIST_WITH_DUPLICATE = """\
ID    NAME                          SCHEDULE         STATUS
1     mempalace-dreaming-daily      30 08 * * *      active
2     Sonhos diários MemPalace      30 05 * * *      active
"""

CRON_LIST_NO_DAILY = """\
ID    NAME           SCHEDULE     STATUS
1     backup-nightly 0 2 * * *    active
"""


def _write_live_config(tmp_path, overrides=None):
    cfg = {
        "memory": {
            "memory_enabled": True,
            "user_profile_enabled": True,
            "provider": "mempalace",
        },
        "plugins": {
            "mempalace-dreaming": {
                "enabled": True,
                "skill": "mempalace-dreaming:mempalace-dreaming",
            }
        },
    }
    if overrides:
        for dotted, value in overrides.items():
            node = cfg
            parts = [
                p.replace("mempalace_dreaming", "mempalace-dreaming")
                for p in dotted.split(".")
            ]
            for seg in parts[:-1]:
                node = node.setdefault(seg, {})
            node[parts[-1]] = value
    import yaml

    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    return path


def _build_all_green_runner(tmp_path, cron_stdout=CRON_LIST_WITH_DAILY, config_overrides=None):
    typed_overrides = None
    if config_overrides:
        typed_overrides = {}
        for dotted, raw in config_overrides.items():
            if dotted.endswith("_enabled") and isinstance(raw, str):
                typed_overrides[dotted] = raw.strip().lower() == "true"
            else:
                typed_overrides[dotted] = raw
    cfg_path = _write_live_config(tmp_path, overrides=typed_overrides)
    mapping = {
        ("hermes", "--version"): _ok("hermes 1.2.3"),
        ("hermes", "memory", "status"): _ok(json.dumps({"provider": "mempalace"})),
        ("hermes", "config", "path"): _ok(str(cfg_path)),
        ("hermes", "cron", "list"): _ok(cron_stdout),
    }
    return _fake_runner(mapping)


def _ids(report):
    return [r["id"] for r in report["repairs"]]


def _by_kind(report, kind):
    return [r for r in report["repairs"] if r["kind"] == kind]


# ---------------------------------------------------------------------------
# All green -> no repairs
# ---------------------------------------------------------------------------


def test_repair_plan_all_green_no_repairs(tmp_path):
    module = load_plugin()
    run_fn = _build_all_green_runner(tmp_path)
    report = module.build_repair_plan(str(tmp_path), run_fn=run_fn)

    assert report["plugin"] == "mempalace-dreaming"
    assert report["version"] == "1.1.1"
    assert report["ok"] is True
    assert report["repairs"] == []
    assert report["warnings"] == []
    assert isinstance(report["summary"], str) and report["summary"]
    json.dumps(report)


def test_repair_plan_all_green_with_schedule_match(tmp_path):
    module = load_plugin()
    run_fn = _build_all_green_runner(tmp_path, cron_stdout=CRON_LIST_WITH_DAILY)
    report = module.build_repair_plan(
        str(tmp_path), run_fn=run_fn, expected_time="08:30", timezone="UTC"
    )
    assert report["ok"] is True
    assert report["repairs"] == []


# ---------------------------------------------------------------------------
# Provider not mempalace
# ---------------------------------------------------------------------------


def test_repair_plan_provider_not_mempalace(tmp_path):
    module = load_plugin()
    cfg_path = _write_live_config(tmp_path)
    mapping = {
        ("hermes", "--version"): _ok("hermes 1.2.3"),
        ("hermes", "memory", "status"): _ok(json.dumps({"provider": "builtin"})),
        ("hermes", "config", "path"): _ok(str(cfg_path)),
        ("hermes", "cron", "list"): _ok(CRON_LIST_WITH_DAILY),
    }
    report = module.build_repair_plan(str(tmp_path), run_fn=_fake_runner(mapping))

    assert report["ok"] is False
    provider_repairs = [r for r in report["repairs"] if r["id"] == "set-memory-provider"]
    assert len(provider_repairs) == 1
    rep = provider_repairs[0]
    assert rep["kind"] == "config"
    assert rep["priority"] == "high"
    assert "mempalace" in rep["reason"].lower()
    assert rep["command_preview"] == "hermes config set memory.provider mempalace"
    json.dumps(report)


# ---------------------------------------------------------------------------
# Config incoherent
# ---------------------------------------------------------------------------


def test_repair_plan_config_incoherent_generates_config_set_preview(tmp_path):
    module = load_plugin()
    run_fn = _build_all_green_runner(
        tmp_path,
        config_overrides={
            "memory.memory_enabled": "false",
            "plugins.mempalace_dreaming.enabled": False,
        },
    )
    report = module.build_repair_plan(str(tmp_path), run_fn=run_fn)

    assert report["ok"] is False
    config_repairs = _by_kind(report, "config")
    assert config_repairs
    previews = [r["command_preview"] for r in config_repairs]
    assert "hermes config set memory.memory_enabled true" in previews
    assert "hermes config set plugins.mempalace_dreaming.enabled true" in previews
    for r in config_repairs:
        assert r["priority"] == "high"
        assert r["reason"]
        assert r["suggested_action"]
    json.dumps(report)


def test_repair_plan_config_unreadable_no_invented_fix(tmp_path):
    module = load_plugin()

    def run_fn(argv):
        t = tuple(argv)
        if t == ("hermes", "--version"):
            return _ok("hermes 1.2.3")
        if t == ("hermes", "memory", "status"):
            return _ok(json.dumps({"provider": "mempalace"}))
        if t == ("hermes", "config", "path"):
            return _fail("config path: not found")
        if t == ("hermes", "cron", "list"):
            return _ok(CRON_LIST_WITH_DAILY)
        raise AssertionError(f"unexpected: {argv}")

    report = module.build_repair_plan(str(tmp_path), run_fn=run_fn)
    assert report["ok"] is False
    config_repairs = _by_kind(report, "config")
    assert config_repairs
    # No `hermes config set` is invented when the config could not be read.
    for r in config_repairs:
        preview = r.get("command_preview")
        assert preview is None or not preview.startswith("hermes config set")
    json.dumps(report)


# ---------------------------------------------------------------------------
# Cron absent
# ---------------------------------------------------------------------------


def test_repair_plan_cron_absent(tmp_path):
    module = load_plugin()
    run_fn = _build_all_green_runner(tmp_path, cron_stdout=CRON_LIST_NO_DAILY)
    report = module.build_repair_plan(str(tmp_path), run_fn=run_fn)

    assert report["ok"] is False
    cron_repairs = _by_kind(report, "cron")
    assert any(r["id"] == "create-daily-cron" for r in cron_repairs)
    rep = next(r for r in cron_repairs if r["id"] == "create-daily-cron")
    assert rep["priority"] == "medium"
    assert "setup" in rep["command_preview"]
    assert "--create-cron" in rep["command_preview"]
    json.dumps(report)


# ---------------------------------------------------------------------------
# Cron duplicated
# ---------------------------------------------------------------------------


def test_repair_plan_cron_duplicated_no_invented_id(tmp_path):
    module = load_plugin()
    run_fn = _build_all_green_runner(tmp_path, cron_stdout=CRON_LIST_WITH_DUPLICATE)
    report = module.build_repair_plan(str(tmp_path), run_fn=run_fn)

    assert report["ok"] is False
    dup = [r for r in report["repairs"] if r["id"] == "remove-duplicate-cron"]
    assert len(dup) == 1
    rep = dup[0]
    assert rep["kind"] == "cron"
    assert rep["priority"] == "medium"
    # Honest: tells the operator to list jobs and remove by id, never invents one.
    assert "hermes cron list" in rep["command_preview"]
    assert "remove" not in rep["command_preview"]
    assert "id" in rep["suggested_action"].lower()
    json.dumps(report)


# ---------------------------------------------------------------------------
# Schedule mismatch
# ---------------------------------------------------------------------------


def test_repair_plan_schedule_mismatch(tmp_path):
    module = load_plugin()
    run_fn = _build_all_green_runner(tmp_path, cron_stdout=CRON_LIST_WITH_DAILY)
    report = module.build_repair_plan(
        str(tmp_path), run_fn=run_fn, expected_time="06:00", timezone="UTC"
    )
    assert report["ok"] is False
    mismatch = [r for r in report["repairs"] if r["id"] == "align-cron-schedule"]
    assert len(mismatch) == 1
    rep = mismatch[0]
    assert rep["kind"] == "cron"
    assert rep["priority"] == "medium"
    # Coherent preview with documented setup path; never auto-applied.
    assert rep["command_preview"] is None or "setup" in rep["command_preview"]
    json.dumps(report)


# ---------------------------------------------------------------------------
# hermes not callable -> no magic fix
# ---------------------------------------------------------------------------


def test_repair_plan_hermes_not_callable_no_magic(tmp_path):
    module = load_plugin()
    mapping = {
        ("hermes", "--version"): _fail("command not found"),
        ("hermes", "memory", "status"): _fail("command not found"),
        ("hermes", "config", "path"): _fail("command not found"),
        ("hermes", "cron", "list"): _fail("command not found"),
    }
    report = module.build_repair_plan(str(tmp_path), run_fn=_fake_runner(mapping))

    assert report["ok"] is False
    env = _by_kind(report, "environment")
    assert any(r["id"] == "install-hermes-cli" for r in env)
    rep = next(r for r in env if r["id"] == "install-hermes-cli")
    assert rep["priority"] == "high"
    # No invented magic fix — only a manual instruction, no command to run.
    assert rep["command_preview"] is None
    assert "path" in rep["suggested_action"].lower()
    json.dumps(report)


def test_repair_plan_run_fn_raises_no_traceback(tmp_path):
    module = load_plugin()

    def exploding(argv):
        raise RuntimeError("subprocess exploded")

    report = module.build_repair_plan(str(tmp_path), run_fn=exploding)
    assert report["ok"] is False
    assert report["repairs"]
    json.dumps(report)


# ---------------------------------------------------------------------------
# Priority ordering
# ---------------------------------------------------------------------------


def test_repair_plan_repairs_ordered_by_priority(tmp_path):
    module = load_plugin()
    # Provider wrong (high) + cron absent (medium) at once.
    cfg_path = _write_live_config(tmp_path)
    mapping = {
        ("hermes", "--version"): _ok("hermes 1.2.3"),
        ("hermes", "memory", "status"): _ok(json.dumps({"provider": "builtin"})),
        ("hermes", "config", "path"): _ok(str(cfg_path)),
        ("hermes", "cron", "list"): _ok(CRON_LIST_NO_DAILY),
    }
    report = module.build_repair_plan(str(tmp_path), run_fn=_fake_runner(mapping))

    rank = {"high": 0, "medium": 1, "low": 2}
    priorities = [rank[r["priority"]] for r in report["repairs"]]
    assert priorities == sorted(priorities)
    # high provider repair must come before the medium cron repair
    assert _ids(report).index("set-memory-provider") < _ids(report).index(
        "create-daily-cron"
    )


def test_repair_plan_repair_item_shape(tmp_path):
    module = load_plugin()
    run_fn = _build_all_green_runner(tmp_path, cron_stdout=CRON_LIST_NO_DAILY)
    report = module.build_repair_plan(str(tmp_path), run_fn=run_fn)
    assert report["repairs"]
    for r in report["repairs"]:
        assert set(["id", "priority", "kind", "reason", "suggested_action"]).issubset(r)
        assert r["priority"] in ("high", "medium", "low")
        assert r["kind"] in ("config", "cron", "plugin", "memory", "environment")
        # command_preview is optional but, when present, is a plain string.
        if "command_preview" in r and r["command_preview"] is not None:
            assert isinstance(r["command_preview"], str)


# ---------------------------------------------------------------------------
# CLI wiring
# ---------------------------------------------------------------------------


def test_repair_plan_cli_default_args():
    module = load_plugin()
    args = _parse(module, ["repair-plan"])
    assert args.hermes_home == module._default_hermes_home()
    assert args.expected_time is None
    assert args.timezone is None
    assert hasattr(args, "func")


def test_repair_plan_cli_prints_valid_json(capsys, tmp_path):
    module = load_plugin()
    args = _parse(module, ["repair-plan", "--hermes-home", str(tmp_path)])
    args.func(args)
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert isinstance(payload["ok"], bool)
    assert isinstance(payload["repairs"], list)
    assert isinstance(payload["warnings"], list)
    assert "summary" in payload
    # Report-only: must not create any directory.
    assert not (tmp_path / "mempalace").exists()


def test_repair_plan_cli_accepts_schedule_args():
    module = load_plugin()
    args = _parse(
        module,
        ["repair-plan", "--expected-time", "05:30", "--timezone", "America/Sao_Paulo"],
    )
    assert args.expected_time == "05:30"
    assert args.timezone == "America/Sao_Paulo"


def test_repair_plan_does_not_break_existing_commands():
    module = load_plugin()
    for cmd in ("status", "verify-runtime", "schedule-plan", "doctor", "lean-check"):
        args = _parse(module, [cmd])
        assert args.mempalace_dreaming_command == cmd
