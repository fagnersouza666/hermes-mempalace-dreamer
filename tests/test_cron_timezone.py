"""Timezone-aware cron scheduling tests (strict TDD, written before code).

Production bug: ``setup --apply --schedule-dreaming --time 05:30
--create-cron`` produced the cron ``30 05 * * *``. The scheduler interprets
cron in UTC, so the job actually ran at 02:30 America/Sao_Paulo, not 05:30 --
the plugin lied about "local time".

These tests pin the fix:

* an explicit ``--timezone`` argument converts the requested wall-clock time
  to a correct UTC cron expression;
* the default timezone is UTC (deterministic and honest -- *not* "local
  time");
* plan/CLI output shows both the requested local time/timezone and the
  resulting UTC cron;
* invalid timezones become a JSON warning, never a traceback.
"""
from pathlib import Path
import argparse
import importlib.util
import json
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mempalace_dreaming.setup import build_cron_create_argv  # noqa: E402


def load_plugin():
    spec = importlib.util.spec_from_file_location(
        "plugin_tz_test", ROOT / "__init__.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["plugin_tz_test"] = module
    spec.loader.exec_module(module)
    return module


def _parse(module, argv):
    parser = argparse.ArgumentParser()
    module._setup_cli_parser(parser)
    return parser.parse_args(argv)


# --- pure conversion ------------------------------------------------------


def test_convert_sao_paulo_local_time_to_utc_cron():
    """05:30 America/Sao_Paulo (UTC-3) must become the UTC cron 30 08."""
    module = load_plugin()
    result = module.convert_to_utc_cron("05:30", "America/Sao_Paulo")

    assert result["requested_time"] == "05:30"
    assert result["timezone"] == "America/Sao_Paulo"
    assert result["utc_time"] == "08:30"
    assert result["cron_utc"] == "30 08 * * *"


def test_convert_utc_is_identity():
    """UTC in -> identical cron out (regression guard for existing behavior)."""
    module = load_plugin()
    result = module.convert_to_utc_cron("05:30", "UTC")
    assert result["utc_time"] == "05:30"
    assert result["cron_utc"] == "30 05 * * *"


def test_convert_invalid_timezone_raises_zoneinfo_error():
    module = load_plugin()
    from zoneinfo import ZoneInfoNotFoundError

    with pytest.raises(ZoneInfoNotFoundError):
        module.convert_to_utc_cron("05:30", "Not/AZone")


# --- schedule-plan shows local + UTC --------------------------------------


def test_schedule_plan_shows_requested_local_and_resulting_utc(capsys):
    module = load_plugin()
    args = _parse(
        module,
        ["schedule-plan", "--time", "05:30", "--timezone", "America/Sao_Paulo"],
    )
    args.func(args)

    payload = json.loads(capsys.readouterr().out)
    assert payload["time"] == "05:30"
    assert payload["timezone"] == "America/Sao_Paulo"
    assert payload["utc_time"] == "08:30"
    assert payload["cron_utc"] == "30 08 * * *"


def test_schedule_plan_default_timezone_is_utc_not_local(capsys):
    module = load_plugin()
    args = _parse(module, ["schedule-plan", "--time", "05:30"])
    args.func(args)

    payload = json.loads(capsys.readouterr().out)
    assert payload["timezone"] == "UTC"
    assert payload["cron_utc"] == "30 05 * * *"
    # The plugin must not claim the time is "local" when it is UTC.
    assert "local time" not in json.dumps(payload).lower()


def test_schedule_plan_invalid_timezone_is_warning_not_traceback(capsys):
    module = load_plugin()
    args = _parse(
        module, ["schedule-plan", "--time", "05:30", "--timezone", "Bogus/TZ"]
    )
    # Must not raise: invalid tz -> JSON warning.
    args.func(args)

    payload = json.loads(capsys.readouterr().out)
    assert "warnings" in payload
    assert any("Bogus/TZ" in w for w in payload["warnings"])
    assert "cron_utc" not in payload


# --- setup-plan / build_setup_plan carry the converted UTC cron -----------


def test_build_setup_plan_schedule_carries_converted_utc_cron(tmp_path):
    module = load_plugin()
    plan = module.build_setup_plan(
        hermes_home=tmp_path,
        schedule_dreaming=True,
        time="05:30",
        timezone="America/Sao_Paulo",
    )
    sched = plan["schedule"]
    assert sched["time"] == "05:30"
    assert sched["timezone"] == "America/Sao_Paulo"
    assert sched["cron_utc"] == "30 08 * * *"


def test_build_cron_create_argv_uses_converted_utc_expression(tmp_path):
    module = load_plugin()
    plan = module.build_setup_plan(
        hermes_home=tmp_path,
        schedule_dreaming=True,
        time="05:30",
        timezone="America/Sao_Paulo",
    )
    argv = build_cron_create_argv(plan["schedule"])
    # Cron expression is the trailing-but-one positional, in UTC.
    assert argv[-2] == "30 08 * * *"


def test_setup_plan_cli_accepts_timezone(capsys, tmp_path):
    module = load_plugin()
    args = _parse(
        module,
        [
            "setup-plan",
            "--hermes-home",
            str(tmp_path),
            "--schedule-dreaming",
            "--time",
            "05:30",
            "--timezone",
            "America/Sao_Paulo",
        ],
    )
    args.func(args)
    payload = json.loads(capsys.readouterr().out)
    assert payload["schedule"]["cron_utc"] == "30 08 * * *"


def test_setup_cli_accepts_timezone_and_converts_cron(tmp_path):
    module = load_plugin()
    args = _parse(
        module,
        [
            "setup",
            "--hermes-home",
            str(tmp_path),
            "--schedule-dreaming",
            "--time",
            "05:30",
            "--timezone",
            "America/Sao_Paulo",
        ],
    )
    payload = vars(args)
    assert payload["timezone"] == "America/Sao_Paulo"

    plan = module.build_setup_plan(
        hermes_home=tmp_path,
        schedule_dreaming=True,
        time="05:30",
        timezone="America/Sao_Paulo",
    )
    assert plan["schedule"]["cron_utc"] == "30 08 * * *"


def test_setup_apply_create_cron_invalid_timezone_is_reported_not_raised(
    tmp_path,
):
    """Invalid tz must not crash apply; cron is reported as not created."""
    module = load_plugin()
    plan = module.build_setup_plan(
        hermes_home=tmp_path,
        schedule_dreaming=True,
        time="05:30",
        timezone="Bogus/TZ",
    )

    from mempalace_dreaming.setup import apply_setup_plan

    scheduled = []
    result = apply_setup_plan(
        plan,
        mkdir_fn=lambda p: None,
        run_fn=lambda a: None,
        schedule_fn=lambda a: scheduled.append(a),
        apply=True,
        create_cron=True,
    )

    assert result.cron is not None
    assert result.cron["created"] is False
    assert "Bogus/TZ" in result.cron["error"]
    # No misleading cron was ever scheduled.
    assert scheduled == []
