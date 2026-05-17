"""Weekly live-provider lean-check cron tests (TDD, mirrors the daily cron).

Nothing here touches a real Hermes, real cron, Obsidian, or memory: every
side effect is dependency-injected and recorded. The weekly job must:

* stay report-only by plan unless explicitly applied + opted in;
* use a deterministic, distinct job name and a weekly UTC cron expression;
* carry a conservative, report-only, live-provider prompt with no secrets;
* be gated by the same early-failure rules as the daily cron.
"""
from pathlib import Path
import argparse
import importlib.util
import io
import json
import sys
from contextlib import redirect_stdout

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mempalace_dreaming.setup import (  # noqa: E402
    LEAN_CHECK_JOB_NAME,
    SCHEDULE_JOB_NAME,
    CONSERVATIVE_LEAN_CHECK_PROMPT,
    apply_setup_plan,
    build_lean_check_cron_argv,
)


def load_plugin():
    spec = importlib.util.spec_from_file_location(
        "plugin_weekly_test", ROOT / "__init__.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["plugin_weekly_test"] = module
    spec.loader.exec_module(module)
    return module


def _plan(tmp_path, **kw):
    module = load_plugin()
    return module.build_setup_plan(
        hermes_home=tmp_path, schedule_lean_check=True, **kw
    )


class Recorder:
    def __init__(self):
        self.daily = []
        self.weekly = []

    def daily_schedule(self, argv):
        self.daily.append(list(argv))

    def weekly_schedule(self, argv):
        self.weekly.append(list(argv))


# --- plan surface ---------------------------------------------------------


def test_lean_check_schedule_absent_unless_requested(tmp_path):
    module = load_plugin()
    plan = module.build_setup_plan(hermes_home=tmp_path)
    assert "lean_check_schedule" not in plan


def test_lean_check_schedule_carries_weekly_utc_cron(tmp_path):
    plan = _plan(
        tmp_path,
        lean_check_time="06:30",
        timezone="America/Sao_Paulo",
        lean_check_weekday=0,
    )
    sched = plan["lean_check_schedule"]
    assert sched["time"] == "06:30"
    assert sched["timezone"] == "America/Sao_Paulo"
    assert sched["weekday"] == 0
    # UTC-3 -> 09:30, still Sunday (no midnight crossing).
    assert sched["cron_utc"] == "30 09 * * 0"
    assert sched["prompt_profile"] == "weekly-lean-check-live"


def test_lean_check_weekday_is_honored(tmp_path):
    plan = _plan(tmp_path, lean_check_time="06:30", lean_check_weekday=3)
    assert plan["lean_check_schedule"]["cron_utc"] == "30 06 * * 3"


def test_invalid_timezone_becomes_timezone_error_not_crash(tmp_path):
    plan = _plan(tmp_path, timezone="Bogus/Zone")
    sched = plan["lean_check_schedule"]
    assert "cron_utc" not in sched
    assert "Bogus/Zone" in sched["timezone_error"]


# --- argv contract --------------------------------------------------------


def test_weekly_argv_is_deterministic_with_distinct_job_name(tmp_path):
    plan = _plan(tmp_path)
    argv = build_lean_check_cron_argv(plan["lean_check_schedule"])
    assert argv[:3] == ["hermes", "cron", "create"]
    assert LEAN_CHECK_JOB_NAME in argv
    assert LEAN_CHECK_JOB_NAME != SCHEDULE_JOB_NAME
    assert "--deliver" in argv and argv[argv.index("--deliver") + 1] == "local"
    # schedule + prompt are positional (no invented flags).
    assert "--schedule" not in argv and "--prompt" not in argv
    assert argv[-1] == CONSERVATIVE_LEAN_CHECK_PROMPT
    assert build_lean_check_cron_argv(plan["lean_check_schedule"]) == argv


def test_weekly_prompt_is_report_only_and_secret_free():
    p = CONSERVATIVE_LEAN_CHECK_PROMPT.lower()
    assert "report" in p
    assert "do not delete" in p or "not delete" in p
    assert "live mempalace" in p
    for leak in ("sk-", "ghp_", "password", "token=", "api_key"):
        assert leak not in p


# --- apply gating ---------------------------------------------------------


def test_dry_run_keeps_lean_check_report_only(tmp_path):
    plan = _plan(tmp_path)
    rec = Recorder()
    result = apply_setup_plan(
        plan,
        mkdir_fn=lambda p: None,
        run_fn=lambda a: None,
        apply=False,
        lean_check_schedule_fn=rec.weekly_schedule,
        create_lean_check_cron=True,
    )
    assert result.lean_check_cron is None
    assert result.lean_check_schedule_planned is not None
    assert rec.weekly == []


def test_apply_without_flag_does_not_create_weekly_cron(tmp_path):
    plan = _plan(tmp_path)
    rec = Recorder()
    result = apply_setup_plan(
        plan,
        mkdir_fn=lambda p: None,
        run_fn=lambda a: None,
        apply=True,
        lean_check_schedule_fn=rec.weekly_schedule,
        create_lean_check_cron=False,
    )
    assert result.lean_check_cron is None
    assert rec.weekly == []


def test_apply_with_flag_creates_weekly_cron_via_injected_fn(tmp_path):
    plan = _plan(tmp_path)
    rec = Recorder()
    result = apply_setup_plan(
        plan,
        mkdir_fn=lambda p: None,
        run_fn=lambda a: None,
        apply=True,
        lean_check_schedule_fn=rec.weekly_schedule,
        create_lean_check_cron=True,
    )
    expected = build_lean_check_cron_argv(plan["lean_check_schedule"])
    assert rec.weekly == [expected]
    assert result.lean_check_cron["created"] is True
    assert result.lean_check_cron["argv"] == expected


def test_weekly_cron_failure_is_reported_not_raised(tmp_path):
    plan = _plan(tmp_path)

    def boom(argv):
        raise RuntimeError("cron daemon down")

    result = apply_setup_plan(
        plan,
        mkdir_fn=lambda p: None,
        run_fn=lambda a: None,
        apply=True,
        lean_check_schedule_fn=boom,
        create_lean_check_cron=True,
    )
    assert result.lean_check_cron["created"] is False
    assert "cron daemon down" in result.lean_check_cron["error"]


def test_weekly_cron_skipped_when_apply_fails_early(tmp_path):
    plan = _plan(tmp_path)
    rec = Recorder()

    def bad_mkdir(path):
        raise OSError("disk full")

    result = apply_setup_plan(
        plan,
        mkdir_fn=bad_mkdir,
        run_fn=lambda a: None,
        apply=True,
        lean_check_schedule_fn=rec.weekly_schedule,
        create_lean_check_cron=True,
    )
    assert rec.weekly == []
    assert result.lean_check_cron["created"] is False
    assert "apply failed early" in result.lean_check_cron["error"]


def test_invalid_timezone_blocks_weekly_cron_with_reason(tmp_path):
    plan = _plan(tmp_path, timezone="Bogus/Zone")
    rec = Recorder()
    result = apply_setup_plan(
        plan,
        mkdir_fn=lambda p: None,
        run_fn=lambda a: None,
        apply=True,
        lean_check_schedule_fn=rec.weekly_schedule,
        create_lean_check_cron=True,
    )
    assert rec.weekly == []
    assert result.lean_check_cron["created"] is False
    assert "Bogus/Zone" in result.lean_check_cron["error"]


def test_rollback_notes_mention_weekly_job_when_requested(tmp_path):
    plan = _plan(tmp_path)
    result = apply_setup_plan(
        plan,
        mkdir_fn=lambda p: None,
        run_fn=lambda a: None,
        apply=False,
        create_lean_check_cron=True,
    )
    assert any(
        LEAN_CHECK_JOB_NAME in note for note in result.rollback_notes
    )


# --- CLI seam -------------------------------------------------------------


def test_cli_setup_plan_exposes_weekly_flags_and_block(tmp_path):
    module = load_plugin()
    parser = argparse.ArgumentParser()
    module._setup_cli_parser(parser)
    args = parser.parse_args(
        [
            "setup-plan",
            "--hermes-home",
            str(tmp_path),
            "--schedule-lean-check",
            "--lean-check-time",
            "07:00",
            "--lean-check-weekday",
            "2",
            "--timezone",
            "UTC",
        ]
    )
    buf = io.StringIO()
    with redirect_stdout(buf):
        module._handle_cli(args)
    payload = json.loads(buf.getvalue())
    assert payload["lean_check_schedule"]["cron_utc"] == "00 07 * * 2"


def test_cli_setup_help_exposes_weekly_flags_defaulting_off(tmp_path):
    module = load_plugin()
    parser = argparse.ArgumentParser()
    module._setup_cli_parser(parser)
    args = parser.parse_args(["setup", "--hermes-home", str(tmp_path)])
    assert args.schedule_lean_check is False
    assert args.create_lean_check_cron is False
    assert args.lean_check_time == "06:30"


def test_cli_dry_run_with_weekly_flags_is_side_effect_free(capsys, tmp_path):
    module = load_plugin()
    parser = argparse.ArgumentParser()
    module._setup_cli_parser(parser)
    args = parser.parse_args(
        [
            "setup",
            "--hermes-home",
            str(tmp_path),
            "--schedule-lean-check",
            "--create-lean-check-cron",
        ]
    )

    def fail(*a, **k):  # pragma: no cover - must never be called on dry-run
        raise AssertionError("dry-run must not invoke side effects")

    result = module._apply_setup_from_args(
        args,
        mkdir_fn=fail,
        run_fn=fail,
        schedule_fn=fail,
        lean_check_schedule_fn=fail,
    )
    assert result.applied is False
    assert result.lean_check_cron is None
    assert not any(tmp_path.iterdir())
