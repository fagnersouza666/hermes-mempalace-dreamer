"""Provider install-method strategy + fresh-home smoke (TDD).

Covers the non-``uv`` provider bootstrap path: the configurable
``auto|uv|pipx|pip-user`` install method, its deterministic candidate
ordering, honest fallback/rollback, and an end-to-end dry-run + apply against
a brand-new fake ``$HERMES_HOME``.

Every side effect is dependency-injected and recorded. Nothing here runs a
real Hermes, real package manager, cron, Obsidian, or memory.
"""
from pathlib import Path
import argparse
import importlib.util
import json
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mempalace_dreaming.setup import apply_setup_plan  # noqa: E402


def load_plugin():
    spec = importlib.util.spec_from_file_location(
        "plugin_install_method_test", ROOT / "__init__.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["plugin_install_method_test"] = module
    spec.loader.exec_module(module)
    return module


class ProviderRecorder:
    def __init__(self, fail_methods=()):
        self.mkdirs = []
        self.runs = []
        self.copies = []
        self.installs = []
        self.schedules = []
        self.verifies = 0
        self._fail_methods = set(fail_methods)

    def mkdir(self, path):
        self.mkdirs.append(path)

    def run(self, argv):
        self.runs.append(list(argv))

    def schedule(self, argv):
        self.schedules.append(list(argv))

    def verify(self):
        self.verifies += 1
        return {"ok": True, "warnings": [], "checks": {}}

    def provider_copy(self, source, target):
        self.copies.append((source, target))

    def provider_install(self, argv):
        argv = list(argv)
        self.installs.append(argv)
        # Simulate a tool being absent: the first token is the package
        # manager (uv / pipx) or the python interpreter for pip-user.
        if argv[0] in self._fail_methods or (
            "pip" in argv and "pip-user" in self._fail_methods
        ):
            raise RuntimeError(f"{argv[0]} not found")


UV_ARGV = ["uv", "tool", "install", "--upgrade", "mempalace"]
PIPX_ARGV = ["pipx", "install", "--force", "mempalace"]


def _pip_user_argv():
    return [
        sys.executable,
        "-m",
        "pip",
        "install",
        "--user",
        "--upgrade",
        "mempalace",
    ]


# --- plan shape -----------------------------------------------------------


def test_default_method_is_auto_with_fixed_candidate_order(tmp_path):
    module = load_plugin()
    block = module.build_setup_plan(
        hermes_home=tmp_path, install_provider=True
    )["provider_install"]

    assert block["install_method"] == "auto"
    assert [c["method"] for c in block["install_candidates"]] == [
        "uv",
        "pipx",
        "pip-user",
    ]
    assert block["install_candidates"][0]["argv"] == UV_ARGV
    assert block["install_candidates"][1]["argv"] == PIPX_ARGV
    assert block["install_candidates"][2]["argv"] == _pip_user_argv()
    # Backward-compatible key stays the preferred (uv) argv.
    assert block["cli_install_argv"] == UV_ARGV
    # No shell strings anywhere.
    for cand in block["install_candidates"]:
        assert isinstance(cand["argv"], list)


@pytest.mark.parametrize(
    "method,expected",
    [
        ("uv", UV_ARGV),
        ("pipx", PIPX_ARGV),
        ("pip-user", None),  # resolved at runtime via sys.executable
    ],
)
def test_pinned_method_yields_single_candidate(tmp_path, method, expected):
    module = load_plugin()
    block = module.build_setup_plan(
        hermes_home=tmp_path, install_provider=True, install_method=method
    )["provider_install"]

    assert block["install_method"] == method
    assert len(block["install_candidates"]) == 1
    cand = block["install_candidates"][0]
    assert cand["method"] == method
    assert cand["argv"] == (expected if expected is not None else _pip_user_argv())
    assert block["cli_install_argv"] == cand["argv"]


def test_unknown_method_is_captured_not_raised(tmp_path):
    module = load_plugin()
    block = module.build_setup_plan(
        hermes_home=tmp_path, install_provider=True, install_method="brew"
    )["provider_install"]

    assert "install_method_error" in block
    assert "brew" in block["install_method_error"]
    assert block["install_candidates"] == []
    assert block["cli_install_argv"] == []


# --- apply: fallback / failure honesty ------------------------------------


def _provider_plan(tmp_path, method="auto"):
    return load_plugin().build_setup_plan(
        hermes_home=tmp_path, install_provider=True, install_method=method
    )


def test_auto_falls_back_to_pipx_when_uv_missing(tmp_path):
    plan = _provider_plan(tmp_path, "auto")
    rec = ProviderRecorder(fail_methods={"uv"})

    result = apply_setup_plan(
        plan,
        mkdir_fn=rec.mkdir,
        run_fn=rec.run,
        provider_copy_fn=rec.provider_copy,
        provider_install_fn=rec.provider_install,
        apply=True,
        install_provider=True,
    )

    assert result.provider["ok"] is True
    assert result.provider["cli_install"]["method"] == "pipx"
    assert result.provider["cli_install"]["ran"] is True
    # uv attempted and failed, pipx attempted and succeeded, pip-user skipped.
    assert rec.installs == [UV_ARGV, PIPX_ARGV]
    methods = [(a["method"], a["ok"]) for a in result.provider["attempts"]]
    assert methods == [("uv", False), ("pipx", True)]


def test_auto_all_methods_fail_is_reported_not_raised(tmp_path):
    plan = _provider_plan(tmp_path, "auto")
    rec = ProviderRecorder(fail_methods={"uv", "pipx", "pip-user"})

    result = apply_setup_plan(
        plan,
        mkdir_fn=rec.mkdir,
        run_fn=rec.run,
        provider_copy_fn=rec.provider_copy,
        provider_install_fn=rec.provider_install,
        schedule_fn=rec.schedule,
        verify_fn=rec.verify,
        apply=True,
        install_provider=True,
        create_cron=True,
        verify_after_apply=True,
    )

    assert result.provider["ok"] is False
    assert result.provider["cli_install"]["ran"] is False
    assert "all install methods failed" in result.provider["cli_install"]["error"]
    for m in ("uv", "pipx", "pip-user"):
        assert m in result.provider["cli_install"]["error"]
    # Files were copied before the install attempts.
    assert rec.copies
    assert len(result.provider["attempts"]) == 3
    # Provider failure gates cron + verification.
    assert rec.schedules == []
    assert rec.verifies == 0
    assert result.cron["created"] is False
    assert result.verification["ran"] is False


def test_pinned_pipx_does_not_try_other_methods(tmp_path):
    plan = _provider_plan(tmp_path, "pipx")
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

    assert result.provider["ok"] is True
    assert rec.installs == [PIPX_ARGV]
    assert result.provider["install_method"] == "pipx"


def test_invalid_method_blocks_apply_before_copy(tmp_path):
    plan = _provider_plan(tmp_path, "conda")
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

    assert result.provider["ok"] is False
    assert "invalid install method" in result.provider["error"]
    # Nothing copied or installed for an invalid method.
    assert rec.copies == []
    assert rec.installs == []


def test_rollback_notes_are_method_aware(tmp_path):
    rec = ProviderRecorder()
    plan = _provider_plan(tmp_path, "pip-user")
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
    assert "pip uninstall mempalace" in joined
    assert "install method: pip-user" in joined


# --- CLI wiring -----------------------------------------------------------


def test_cli_rejects_unknown_install_method(tmp_path):
    module = load_plugin()
    parser = argparse.ArgumentParser()
    module._setup_cli_parser(parser)
    with pytest.raises(SystemExit):
        parser.parse_args(
            ["setup", "--install-provider", "--install-method", "snap"]
        )


def test_cli_apply_pinned_pip_user_end_to_end(tmp_path):
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
            "--install-method",
            "pip-user",
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
    assert result.provider["ok"] is True
    assert rec.installs == [_pip_user_argv()]


# --- fresh / fake Hermes home smoke ---------------------------------------


def test_fresh_fake_hermes_home_dry_run_then_apply(tmp_path, capsys):
    """A brand-new empty $HERMES_HOME: deterministic dry-run, then a full
    apply with every side effect injected. No real Hermes, no real fs writes
    beyond the recorder."""
    module = load_plugin()
    fresh_home = tmp_path / "fresh-hermes"
    assert not fresh_home.exists()

    parser = argparse.ArgumentParser()
    module._setup_cli_parser(parser)

    # 1. Dry-run via the real CLI path is byte-stable and side-effect free.
    dry_args = parser.parse_args(
        [
            "setup",
            "--hermes-home",
            str(fresh_home),
            "--install-provider",
            "--schedule-dreaming",
        ]
    )
    dry_args.func(dry_args)
    first = capsys.readouterr().out
    dry_args.func(dry_args)
    second = capsys.readouterr().out
    assert first == second, "dry-run must be deterministic"

    payload = json.loads(first)
    assert payload["applied"] is False
    assert payload["provider"] is None
    assert payload["provider_install_planned"]["install_method"] == "auto"
    # Fresh home: nothing was created on disk by the dry-run.
    assert not fresh_home.exists()

    # 2. Full apply against the same fresh home, all seams injected.
    apply_args = parser.parse_args(
        [
            "setup",
            "--hermes-home",
            str(fresh_home),
            "--apply",
            "--install-provider",
            "--schedule-dreaming",
            "--create-cron",
            "--verify-after-apply",
        ]
    )
    rec = ProviderRecorder()
    result = module._apply_setup_from_args(
        apply_args,
        mkdir_fn=rec.mkdir,
        run_fn=rec.run,
        schedule_fn=rec.schedule,
        provider_copy_fn=rec.provider_copy,
        provider_install_fn=rec.provider_install,
        verify_fn=rec.verify,
    )

    assert result.applied is True
    assert result.errors == []
    # Directories planned under the fresh home only.
    assert all(str(fresh_home) in d for d in result.created_directories)
    assert rec.mkdirs == result.created_directories
    # Provider bootstrapped via the auto strategy (uv first, succeeds).
    assert result.provider["ok"] is True
    assert result.provider["install_method"] == "auto"
    assert rec.installs == [UV_ARGV]
    # Cron created and verification ran (clean apply).
    assert result.cron["created"] is True
    assert rec.schedules and rec.schedules[0][:3] == ["hermes", "cron", "create"]
    assert result.verification["ran"] is True
    # Safety guarantees intact on a fresh home.
    status = module._build_status()
    assert status["safety"]["provider_install_explicit"] is True
    assert status["safety"]["no_obsidian_writes"] is True
