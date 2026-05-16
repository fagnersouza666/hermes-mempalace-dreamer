"""Production bootstrap workflow tests (strict TDD, written before code).

These cover the v1.0 productionization surface:

* explicit, injected cron creation (``--create-cron`` + ``--apply``);
* explicit, injected post-apply verification (``--verify-after-apply``);
* a deterministic ``hermes cron create`` argv contract;
* integration-style runs against an isolated fake Hermes home through the
  plugin CLI/helper seams.

Nothing here touches a real Hermes, real cron, Obsidian, or memory: every
side effect is dependency-injected and recorded.
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
    SCHEDULE_JOB_NAME,
    SetupResult,
    apply_setup_plan,
    build_cron_create_argv,
)


def load_plugin():
    spec = importlib.util.spec_from_file_location(
        "plugin_prod_test", ROOT / "__init__.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["plugin_prod_test"] = module
    spec.loader.exec_module(module)
    return module


def _plan(tmp_path, schedule=True, time="05:30"):
    module = load_plugin()
    return module.build_setup_plan(
        hermes_home=tmp_path, schedule_dreaming=schedule, time=time
    )


class Recorder:
    def __init__(self):
        self.mkdirs = []
        self.runs = []
        self.schedules = []
        self.verifies = 0

    def mkdir(self, path):
        self.mkdirs.append(path)

    def run(self, argv):
        self.runs.append(list(argv))

    def schedule(self, argv):
        self.schedules.append(list(argv))

    def verify(self):
        self.verifies += 1
        return {"ok": True, "warnings": [], "checks": {}}


# --- cron argv contract ---------------------------------------------------


def test_cron_argv_is_hermes_cron_create_with_deterministic_name(tmp_path):
    plan = _plan(tmp_path, schedule=True, time="05:30")
    argv = build_cron_create_argv(plan["schedule"])

    assert isinstance(argv, list)
    assert all(isinstance(p, str) for p in argv)
    assert argv[:3] == ["hermes", "cron", "create"]
    assert SCHEDULE_JOB_NAME in argv
    assert "--name" in argv
    assert "--deliver" in argv
    assert "--skill" in argv
    # Real CLI: schedule and prompt are positionals, not flags. The plugin
    # must not invent unsupported options.
    assert "--schedule" not in argv
    assert "--prompt" not in argv
    # Deterministic across calls.
    assert build_cron_create_argv(plan["schedule"]) == argv


def test_cron_argv_passes_schedule_as_positional_token(tmp_path):
    plan = _plan(tmp_path, schedule=True, time="05:30")
    argv = build_cron_create_argv(plan["schedule"])
    # Cron expression is a bare positional, immediately before the prompt
    # positional, and never introduced by a --schedule flag.
    assert "--schedule" not in argv
    assert argv[-2] == "30 05 * * *"

    plan2 = _plan(tmp_path, schedule=True, time="23:07")
    argv2 = build_cron_create_argv(plan2["schedule"])
    assert argv2[-2] == "07 23 * * *"


def test_cron_argv_defaults_to_safe_local_deliver_and_attaches_skill(tmp_path):
    plan = _plan(tmp_path, schedule=True)
    argv = build_cron_create_argv(plan["schedule"])

    assert "--deliver" in argv
    deliver = argv[argv.index("--deliver") + 1]
    assert deliver == "local"
    # Must not silently broadcast to chats.
    assert "chat" not in " ".join(argv).lower()

    assert "--skill" in argv
    skill = argv[argv.index("--skill") + 1]
    assert skill == "plugin:mempalace-dreaming"


def test_cron_prompt_is_final_positional_conservative_no_secrets(tmp_path):
    plan = _plan(tmp_path, schedule=True)
    argv = build_cron_create_argv(plan["schedule"])
    # Real CLI: prompt is the trailing positional, not a --prompt flag.
    assert "--prompt" not in argv
    prompt = argv[-1]
    low = prompt.lower()
    assert "mempalace" in low
    assert "conservative" in low or "minimal" in low
    # No secret-shaped material baked into the prompt.
    assert "sk-" not in prompt
    assert "ghp_" not in prompt


# --- apply WITHOUT cron (default) -----------------------------------------


def test_apply_without_create_cron_does_not_schedule(tmp_path):
    plan = _plan(tmp_path, schedule=True)
    rec = Recorder()

    result = apply_setup_plan(
        plan,
        mkdir_fn=rec.mkdir,
        run_fn=rec.run,
        schedule_fn=rec.schedule,
        apply=True,
    )

    assert isinstance(result, SetupResult)
    assert result.applied is True
    assert rec.schedules == []
    # No cron requested -> cron stays None (not "created: false noise").
    assert result.cron is None
    # Schedule is still reported.
    assert result.schedule_planned == plan["schedule"]


# --- apply WITH cron ------------------------------------------------------


def test_apply_with_create_cron_invokes_schedule_fn_with_argv(tmp_path):
    plan = _plan(tmp_path, schedule=True, time="05:30")
    rec = Recorder()

    result = apply_setup_plan(
        plan,
        mkdir_fn=rec.mkdir,
        run_fn=rec.run,
        schedule_fn=rec.schedule,
        apply=True,
        create_cron=True,
    )

    expected_argv = build_cron_create_argv(plan["schedule"])
    assert rec.schedules == [expected_argv]
    assert result.cron is not None
    assert result.cron["created"] is True
    assert result.cron["argv"] == expected_argv
    assert result.cron["error"] == ""


def test_cron_creation_failure_is_reported_not_raised(tmp_path):
    plan = _plan(tmp_path, schedule=True)

    def boom_schedule(argv):
        raise RuntimeError("cron daemon down")

    result = apply_setup_plan(
        plan,
        mkdir_fn=lambda p: None,
        run_fn=lambda a: None,
        schedule_fn=boom_schedule,
        apply=True,
        create_cron=True,
    )

    assert result.applied is True
    assert result.cron is not None
    assert result.cron["created"] is False
    assert "cron daemon down" in result.cron["error"]


def test_create_cron_requested_without_schedule_in_plan(tmp_path):
    plan = _plan(tmp_path, schedule=False)
    rec = Recorder()

    result = apply_setup_plan(
        plan,
        mkdir_fn=rec.mkdir,
        run_fn=rec.run,
        schedule_fn=rec.schedule,
        apply=True,
        create_cron=True,
    )

    assert rec.schedules == []
    assert result.cron is not None
    assert result.cron["created"] is False
    assert result.cron["error"]


# --- verify-after-apply ---------------------------------------------------


def test_verify_after_apply_runs_injected_verify_fn(tmp_path):
    plan = _plan(tmp_path, schedule=False)
    rec = Recorder()

    result = apply_setup_plan(
        plan,
        mkdir_fn=rec.mkdir,
        run_fn=rec.run,
        verify_fn=rec.verify,
        apply=True,
        verify_after_apply=True,
    )

    assert rec.verifies == 1
    assert result.verification is not None
    assert result.verification["ran"] is True
    assert result.verification["report"]["ok"] is True


def test_verify_not_requested_yields_none(tmp_path):
    plan = _plan(tmp_path, schedule=False)
    rec = Recorder()

    result = apply_setup_plan(
        plan,
        mkdir_fn=rec.mkdir,
        run_fn=rec.run,
        verify_fn=rec.verify,
        apply=True,
    )

    assert rec.verifies == 0
    assert result.verification is None


def test_verify_skipped_when_apply_fails_early(tmp_path):
    plan = _plan(tmp_path, schedule=True)
    rec = Recorder()

    def bad_mkdir(path):
        raise RuntimeError("disk full")

    result = apply_setup_plan(
        plan,
        mkdir_fn=bad_mkdir,
        run_fn=rec.run,
        schedule_fn=rec.schedule,
        verify_fn=rec.verify,
        apply=True,
        create_cron=True,
        verify_after_apply=True,
    )

    assert result.errors
    # Cron and verification must NOT run after an early apply failure.
    assert rec.schedules == []
    assert rec.verifies == 0
    assert result.cron is not None and result.cron["created"] is False
    assert result.verification is not None
    assert result.verification["ran"] is False
    assert "skip" in result.verification["reason"].lower()


# --- dry-run stays side-effect free ---------------------------------------


def test_dry_run_never_schedules_or_verifies(tmp_path):
    plan = _plan(tmp_path, schedule=True)
    rec = Recorder()

    result = apply_setup_plan(
        plan,
        mkdir_fn=rec.mkdir,
        run_fn=rec.run,
        schedule_fn=rec.schedule,
        verify_fn=rec.verify,
        apply=False,
        create_cron=True,
        verify_after_apply=True,
    )

    assert result.applied is False
    assert rec.mkdirs == []
    assert rec.runs == []
    assert rec.schedules == []
    assert rec.verifies == 0
    assert result.cron is None
    assert result.verification is None


# --- integration-style: through the CLI helper seam -----------------------


def test_cli_apply_with_cron_and_verify_end_to_end(tmp_path, monkeypatch):
    """Exercise an isolated fake Hermes home end-to-end via the helper.

    Real directory creation is allowed (into the tmp fake home) but config,
    cron, and verification are injected so no real Hermes is touched.
    """
    module = load_plugin()
    parser = argparse.ArgumentParser()
    module._setup_cli_parser(parser)
    args = parser.parse_args(
        [
            "setup",
            "--hermes-home",
            str(tmp_path),
            "--schedule-dreaming",
            "--time",
            "05:30",
            "--apply",
            "--create-cron",
            "--verify-after-apply",
        ]
    )

    rec = Recorder()
    result = module._apply_setup_from_args(
        args,
        mkdir_fn=rec.mkdir,
        run_fn=rec.run,
        schedule_fn=rec.schedule,
        verify_fn=rec.verify,
    )

    assert result.applied is True
    assert result.errors == []
    assert rec.schedules and rec.schedules[0][:3] == [
        "hermes",
        "cron",
        "create",
    ]
    assert result.cron["created"] is True
    assert result.verification["ran"] is True
    # Serializable as JSON for the CLI.
    import dataclasses

    json.dumps(dataclasses.asdict(result))


def test_cli_setup_help_exposes_new_flags(tmp_path):
    module = load_plugin()
    parser = argparse.ArgumentParser()
    module._setup_cli_parser(parser)
    args = parser.parse_args(["setup", "--hermes-home", str(tmp_path)])
    # New flags exist and default to off.
    assert args.create_cron is False
    assert args.verify_after_apply is False


def test_cli_dry_run_with_flags_stays_side_effect_free(capsys, tmp_path):
    module = load_plugin()
    parser = argparse.ArgumentParser()
    module._setup_cli_parser(parser)
    args = parser.parse_args(
        [
            "setup",
            "--hermes-home",
            str(tmp_path),
            "--schedule-dreaming",
            "--create-cron",
            "--verify-after-apply",
        ]
    )
    args.func(args)

    payload = json.loads(capsys.readouterr().out)
    assert payload["applied"] is False
    assert payload["cron"] is None
    assert payload["verification"] is None
    assert not (tmp_path / "mempalace").exists()


def test_plugin_status_reports_production_bootstrap_v1():
    module = load_plugin()
    status = module._build_status()
    assert status["version"] == "1.0.0"
    assert "production" in status["status"].lower()
    assert status["safety"]["cron_creation_explicit"] is True
    assert status["safety"]["verify_after_apply_explicit"] is True
