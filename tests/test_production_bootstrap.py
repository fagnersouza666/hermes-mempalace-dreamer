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
    assert skill == "mempalace-dreaming:mempalace-dreaming"


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
    assert status["version"] == "1.1.0"
    assert "production" in status["status"].lower()
    assert status["safety"]["cron_creation_explicit"] is True
    assert status["safety"]["verify_after_apply_explicit"] is True
    # New explicit, opt-in MemPalace provider bootstrap.
    assert status["safety"]["provider_install_explicit"] is True


# --- explicit, opt-in MemPalace provider install --------------------------


class ProviderRecorder(Recorder):
    """Recorder that also captures provider copy/install side effects."""

    def __init__(self):
        super().__init__()
        self.copies = []
        self.installs = []

    def provider_copy(self, source, target):
        self.copies.append((source, target))

    def provider_install(self, argv):
        self.installs.append(list(argv))


def test_setup_plan_has_provider_install_block_only_when_requested(tmp_path):
    module = load_plugin()

    plan_off = module.build_setup_plan(hermes_home=tmp_path)
    assert "provider_install" not in plan_off

    plan_on = module.build_setup_plan(
        hermes_home=tmp_path, install_provider=True
    )
    block = plan_on["provider_install"]
    assert block["destination"] == str(
        Path(tmp_path) / "plugins" / "mempalace"
    )
    targets = {Path(f["target"]).name for f in block["files"]}
    assert targets == {"__init__.py", "plugin.yaml"}
    for f in block["files"]:
        assert Path(f["source"]).is_file(), f
        assert f["target"].startswith(block["destination"])
    assert block["cli_install_argv"] == [
        "uv",
        "tool",
        "install",
        "--upgrade",
        "mempalace",
    ]


def test_dry_run_with_install_provider_is_side_effect_free(tmp_path):
    plan = module = load_plugin().build_setup_plan(
        hermes_home=tmp_path, install_provider=True
    )
    rec = ProviderRecorder()

    result = apply_setup_plan(
        plan,
        mkdir_fn=rec.mkdir,
        run_fn=rec.run,
        provider_copy_fn=rec.provider_copy,
        provider_install_fn=rec.provider_install,
        apply=False,
        install_provider=True,
    )

    assert result.applied is False
    assert rec.copies == []
    assert rec.installs == []
    assert result.provider is None
    # Dry-run still reports what would be installed.
    assert result.provider_install_planned == plan["provider_install"]


def test_apply_without_install_provider_keeps_provider_none(tmp_path):
    module = load_plugin()
    plan = module.build_setup_plan(hermes_home=tmp_path)
    rec = ProviderRecorder()

    result = apply_setup_plan(
        plan,
        mkdir_fn=rec.mkdir,
        run_fn=rec.run,
        provider_copy_fn=rec.provider_copy,
        provider_install_fn=rec.provider_install,
        apply=True,
    )

    assert result.provider is None
    assert rec.copies == []
    assert rec.installs == []


def test_apply_with_install_provider_copies_and_installs(tmp_path):
    module = load_plugin()
    plan = module.build_setup_plan(
        hermes_home=tmp_path, install_provider=True
    )
    rec = ProviderRecorder()

    result = apply_setup_plan(
        plan,
        mkdir_fn=rec.mkdir,
        run_fn=rec.run,
        provider_copy_fn=rec.provider_copy,
        provider_install_fn=rec.provider_install,
        apply=True,
        install_provider=True,
    )

    block = plan["provider_install"]
    expected_copies = [(f["source"], f["target"]) for f in block["files"]]
    assert rec.copies == expected_copies
    assert rec.installs == [block["cli_install_argv"]]
    assert result.provider is not None
    assert result.provider["ok"] is True
    assert result.provider["copied_files"] == [
        f["target"] for f in block["files"]
    ]
    assert result.provider["cli_install"]["ran"] is True
    assert result.provider["cli_install"]["error"] == ""
    assert result.provider["error"] == ""
    # No shell strings anywhere.
    for argv in rec.installs:
        assert isinstance(argv, list)


def test_provider_copy_failure_is_reported_not_raised(tmp_path):
    module = load_plugin()
    plan = module.build_setup_plan(
        hermes_home=tmp_path, install_provider=True
    )
    rec = ProviderRecorder()

    def boom_copy(source, target):
        raise OSError("read-only filesystem")

    result = apply_setup_plan(
        plan,
        mkdir_fn=rec.mkdir,
        run_fn=rec.run,
        provider_copy_fn=boom_copy,
        provider_install_fn=rec.provider_install,
        schedule_fn=rec.schedule,
        verify_fn=rec.verify,
        apply=True,
        install_provider=True,
        create_cron=True,
        verify_after_apply=True,
    )

    assert result.provider is not None
    assert result.provider["ok"] is False
    assert "read-only filesystem" in result.provider["error"]
    # CLI install never attempted after a copy failure.
    assert rec.installs == []
    # Provider failure is an early failure: cron/verify must be skipped.
    assert rec.schedules == []
    assert rec.verifies == 0
    assert result.cron is not None and result.cron["created"] is False
    assert result.verification is not None
    assert result.verification["ran"] is False


def test_provider_cli_install_failure_is_reported_not_raised(tmp_path):
    module = load_plugin()
    plan = module.build_setup_plan(
        hermes_home=tmp_path, install_provider=True
    )
    rec = ProviderRecorder()

    def boom_install(argv):
        raise RuntimeError("uv not found")

    result = apply_setup_plan(
        plan,
        mkdir_fn=rec.mkdir,
        run_fn=rec.run,
        provider_copy_fn=rec.provider_copy,
        provider_install_fn=boom_install,
        schedule_fn=rec.schedule,
        verify_fn=rec.verify,
        apply=True,
        install_provider=True,
        create_cron=True,
        verify_after_apply=True,
    )

    # Files were copied before the CLI step failed.
    assert rec.copies
    assert result.provider is not None
    assert result.provider["ok"] is False
    assert result.provider["cli_install"]["ran"] is False
    assert "uv not found" in result.provider["cli_install"]["error"]
    # Provider failure gates cron/verify.
    assert rec.schedules == []
    assert rec.verifies == 0


def test_provider_install_skipped_when_apply_fails_early(tmp_path):
    module = load_plugin()
    plan = module.build_setup_plan(
        hermes_home=tmp_path, install_provider=True
    )
    rec = ProviderRecorder()

    def bad_mkdir(path):
        raise RuntimeError("disk full")

    result = apply_setup_plan(
        plan,
        mkdir_fn=bad_mkdir,
        run_fn=rec.run,
        provider_copy_fn=rec.provider_copy,
        provider_install_fn=rec.provider_install,
        apply=True,
        install_provider=True,
    )

    assert result.errors
    assert rec.copies == []
    assert rec.installs == []
    assert result.provider is not None
    assert result.provider["ok"] is False
    assert "skip" in result.provider["error"].lower()


def test_rollback_notes_mention_provider_when_requested(tmp_path):
    module = load_plugin()
    plan = module.build_setup_plan(
        hermes_home=tmp_path, install_provider=True
    )
    rec = ProviderRecorder()

    result = apply_setup_plan(
        plan,
        mkdir_fn=rec.mkdir,
        run_fn=rec.run,
        provider_copy_fn=rec.provider_copy,
        provider_install_fn=rec.provider_install,
        apply=True,
        install_provider=True,
    )

    joined = " ".join(result.rollback_notes).lower()
    assert "mempalace" in joined and "uv tool uninstall" in joined


def test_cli_install_provider_flag_defaults_off_and_is_apply_only(
    capsys, tmp_path
):
    module = load_plugin()
    parser = argparse.ArgumentParser()
    module._setup_cli_parser(parser)

    args = parser.parse_args(["setup", "--hermes-home", str(tmp_path)])
    assert args.install_provider is False

    # Dry-run with the flag set must not touch the filesystem.
    args = parser.parse_args(
        ["setup", "--hermes-home", str(tmp_path), "--install-provider"]
    )
    args.func(args)
    payload = json.loads(capsys.readouterr().out)
    assert payload["applied"] is False
    assert payload["provider"] is None
    assert payload["provider_install_planned"] is not None
    assert not (tmp_path / "plugins").exists()


def test_cli_apply_install_provider_end_to_end(tmp_path):
    module = load_plugin()
    parser = argparse.ArgumentParser()
    module._setup_cli_parser(parser)
    args = parser.parse_args(
        [
            "setup",
            "--hermes-home",
            str(tmp_path),
            "--apply",
            "--install-provider",
        ]
    )
    rec = ProviderRecorder()

    result = module._apply_setup_from_args(
        args,
        mkdir_fn=rec.mkdir,
        run_fn=rec.run,
        provider_copy_fn=rec.provider_copy,
        provider_install_fn=rec.provider_install,
    )

    assert result.applied is True
    assert result.errors == []
    assert result.provider["ok"] is True
    assert rec.copies and rec.installs == [
        ["uv", "tool", "install", "--upgrade", "mempalace"]
    ]
    import dataclasses

    json.dumps(dataclasses.asdict(result))
